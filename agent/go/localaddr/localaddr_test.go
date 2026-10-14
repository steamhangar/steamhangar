package localaddr

import (
	"context"
	"errors"
	"net"
	"reflect"
	"strings"
	"sync"
	"testing"
)

// fakeResolver answers LookupIPAddr from a fixed table.
type fakeResolver struct {
	answers map[string][]string
	err     error
	asked   []string
}

func (r *fakeResolver) LookupIPAddr(_ context.Context, host string) ([]net.IPAddr, error) {
	r.asked = append(r.asked, host)
	if r.err != nil {
		return nil, r.err
	}
	var out []net.IPAddr
	for _, s := range r.answers[host] {
		out = append(out, net.IPAddr{IP: net.ParseIP(s)})
	}
	return out, nil
}

// fakeDialer returns a conn whose LocalAddr is routes[destination IP];
// a destination without an entry fails like "network unreachable".
type fakeDialer struct {
	mu     sync.Mutex
	routes map[string]string
	dialed []string
}

func (d *fakeDialer) DialContext(_ context.Context, network, address string) (net.Conn, error) {
	d.mu.Lock()
	d.dialed = append(d.dialed, network+" "+address)
	d.mu.Unlock()
	host, _, _ := net.SplitHostPort(address)
	local, ok := d.routes[host]
	if !ok {
		return nil, errors.New("network is unreachable")
	}
	return fakeConn{local: &net.UDPAddr{IP: net.ParseIP(local), Port: 50000}}, nil
}

type fakeConn struct {
	net.Conn
	local net.Addr
}

func (c fakeConn) LocalAddr() net.Addr { return c.local }
func (c fakeConn) Close() error        { return nil }

func TestDetect_OneSourceAddressPerResolvedFamily(t *testing.T) {
	r := &fakeResolver{answers: map[string][]string{"vault.lan": {"192.168.1.10", "fd00:10::10"}}}
	d := &fakeDialer{routes: map[string]string{
		"192.168.1.10": "192.168.1.42",
		"fd00:10::10":  "fd00:10::42",
	}}
	got, notes := Detect(context.Background(), "http://vault.lan:8080", r, d)
	if want := []string{"192.168.1.42", "fd00:10::42"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("Detect = %v, want %v (notes %v)", got, want, notes)
	}
	if want := []string{"udp 192.168.1.10:8080", "udp [fd00:10::10]:8080"}; !reflect.DeepEqual(d.dialed, want) {
		t.Errorf("dialed %v, want %v (UDP to the vault's own port)", d.dialed, want)
	}
}

func TestDetect_DefaultPortsFollowTheScheme(t *testing.T) {
	for scheme, port := range map[string]string{"http": "80", "https": "443"} {
		d := &fakeDialer{routes: map[string]string{"192.168.1.10": "192.168.1.42"}}
		if _, _ = Detect(context.Background(), scheme+"://192.168.1.10/", &fakeResolver{}, d); len(d.dialed) != 1 ||
			d.dialed[0] != "udp 192.168.1.10:"+port {
			t.Errorf("%s: dialed %v, want port %s", scheme, d.dialed, port)
		}
	}
}

func TestDetect_IPLiteralHostIsNotResolved(t *testing.T) {
	r := &fakeResolver{}
	d := &fakeDialer{routes: map[string]string{"192.168.1.10": "192.168.1.42"}}
	got, _ := Detect(context.Background(), "http://192.168.1.10:8080", r, d)
	if !reflect.DeepEqual(got, []string{"192.168.1.42"}) {
		t.Errorf("Detect = %v", got)
	}
	if len(r.asked) != 0 {
		t.Errorf("resolver asked %v for an IP literal", r.asked)
	}
}

// Loopback (agent on the vault host), link-local and unspecified sources
// are nothing another host could log: dropped, not sent.
func TestDetect_DropsUnusableSources(t *testing.T) {
	r := &fakeResolver{answers: map[string][]string{"vault": {"127.0.0.1", "::1", "fe80::10", "192.168.1.10"}}}
	d := &fakeDialer{routes: map[string]string{
		"127.0.0.1":    "127.0.0.1",
		"::1":          "::1",
		"fe80::10":     "fe80::42",
		"192.168.1.10": "::ffff:192.168.1.42", // dual-stack spelling, unmapped
	}}
	got, _ := Detect(context.Background(), "http://vault:8080", r, d)
	if !reflect.DeepEqual(got, []string{"192.168.1.42"}) {
		t.Errorf("Detect = %v, want only the unmapped LAN address", got)
	}
}

func TestDetect_FailuresAreNotesNotErrors(t *testing.T) {
	got, notes := Detect(context.Background(), "http://vault:8080", &fakeResolver{err: errors.New("no such host")}, &fakeDialer{})
	if len(got) != 0 || len(notes) != 1 || !strings.Contains(notes[0], "resolving") {
		t.Errorf("Detect = %v, notes %v; want nothing and one resolve note", got, notes)
	}
	got, notes = Detect(context.Background(), "http://192.168.1.10", &fakeResolver{}, &fakeDialer{})
	if len(got) != 0 || len(notes) != 1 || !strings.Contains(notes[0], "no route") {
		t.Errorf("Detect = %v, notes %v; want nothing and one route note", got, notes)
	}
	got, notes = Detect(context.Background(), "::not a url", nil, nil)
	if len(got) != 0 || len(notes) != 1 {
		t.Errorf("Detect = %v, notes %v; want nothing and one note", got, notes)
	}
}

func TestDetect_CapsTheResult(t *testing.T) {
	var answers []string
	routes := map[string]string{}
	for i := 1; i <= 12; i++ {
		dest := net.IPv4(192, 168, 2, byte(i)).String()
		answers = append(answers, dest)
		routes[dest] = net.IPv4(192, 168, 1, byte(i)).String()
	}
	got, _ := Detect(context.Background(), "http://vault", &fakeResolver{answers: map[string][]string{"vault": answers}}, &fakeDialer{routes: routes})
	if len(got) != MaxAddrs {
		t.Errorf("len = %d, want the cap %d", len(got), MaxAddrs)
	}
}

// The real system dialer against loopback: works on every OS and CI
// runner, and must yield nothing (the agent on the vault host itself).
func TestDetect_RealLoopbackYieldsNothing(t *testing.T) {
	got, notes := Detect(context.Background(), "http://127.0.0.1:8080", nil, nil)
	if len(got) != 0 {
		t.Errorf("Detect = %v (notes %v), want nothing toward loopback", got, notes)
	}
}

// Usable mirrors vault_api.agent_reports.normalize_local_addr; the same
// cases are in api/tests/test_agent_addr_1.py.
func TestUsable_MirrorsTheServerRule(t *testing.T) {
	cases := []struct {
		in, want string
		ok       bool
	}{
		{"192.168.1.42", "192.168.1.42", true},
		{"::ffff:192.168.1.42", "192.168.1.42", true},
		{"fd00:10:0:0::42", "fd00:10::42", true},
		{"2001:db8::42", "2001:db8::42", true},
		{"not-an-ip", "", false},
		{"192.168.1.300", "", false},
		{" 192.168.1.42", "", false},
		{"192.168.1.42\n", "", false},
		{"fe80::1%eth0", "", false},
		{"127.0.0.1", "", false},
		{"::1", "", false},
		{"0.0.0.0", "", false},
		{"169.254.10.10", "", false},
		{"fe80::1", "", false},
		{"224.0.0.1", "", false},
		{"ff02::1", "", false},
	}
	for _, tc := range cases {
		got, ok := Usable(tc.in)
		if ok != tc.ok || got != tc.want {
			t.Errorf("Usable(%q) = %q, %v; want %q, %v", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}
