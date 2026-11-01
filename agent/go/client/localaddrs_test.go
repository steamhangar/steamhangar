package client

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/Riviera822/steamhangar/agent/report"
)

// WP AGENT-ADDR-1: local_addrs against servers that predate it.

// forbidExtras answers like a vault-api whose request model knows only the
// allowed keys: ONE 422 listing every other key as extra_forbidden (what
// FastAPI does), else 200.
func forbidExtras(allowed ...string) func([]byte) (int, string) {
	ok := map[string]bool{}
	for _, k := range allowed {
		ok[k] = true
	}
	return func(body []byte) (int, string) {
		var m map[string]any
		if err := json.Unmarshal(body, &m); err != nil {
			return http.StatusBadRequest, `{"detail":"bad json"}`
		}
		var keys []string
		for k := range m {
			if !ok[k] {
				keys = append(keys, k)
			}
		}
		if len(keys) == 0 {
			return http.StatusOK, okBody
		}
		sort.Strings(keys)
		var details []string
		for _, k := range keys {
			details = append(details, `{"type":"extra_forbidden","loc":["body","`+k+`"],"msg":"Extra inputs are not permitted","input":null}`)
		}
		return http.StatusUnprocessableEntity, `{"detail":[` + strings.Join(details, ",") + `]}`
	}
}

func newPayload() report.Payload {
	return presencePayload().WithLocalAddrs([]string{"192.168.1.42"})
}

func TestReportInstalled_SendsLocalAddrs(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids", "agent_version", "report_interval_seconds", "local_addrs"))
	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	if err != nil {
		t.Fatal(err)
	}
	bodies := rs.got()
	want := `{"client_id":"test-pc","appids":[440,730],"agent_version":"0.1.0","report_interval_seconds":600,"local_addrs":["192.168.1.42"]}`
	if len(bodies) != 1 || bodies[0] != want {
		t.Fatalf("bodies = %q, want exactly [%s]", bodies, want)
	}
	if result.LocalAddrsDropped || result.PresenceDropped {
		t.Errorf("result = %+v, nothing should be dropped", result)
	}
}

// rc9..rc16 know the presence fields but not local_addrs: only local_addrs
// is dropped, the presence fields keep flowing.
func TestReportInstalled_PreAddrServerKeepsThePresenceFields(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids", "agent_version", "report_interval_seconds"))
	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	if err != nil {
		t.Fatalf("an rc16 server must still get the report: %v", err)
	}
	bodies := rs.got()
	if len(bodies) != 2 {
		t.Fatalf("server saw %d request(s), want 2: %q", len(bodies), bodies)
	}
	if want := `{"client_id":"test-pc","appids":[440,730],"agent_version":"0.1.0","report_interval_seconds":600}`; bodies[1] != want {
		t.Errorf("resent body = %s, want %s", bodies[1], want)
	}
	if !result.LocalAddrsDropped || result.PresenceDropped {
		t.Errorf("result = %+v, want LocalAddrsDropped only", result)
	}
}

// rc8 knows none of the optional fields: one resend with the legacy body.
func TestReportInstalled_Rc8ServerGetsTheLegacyBody(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids"))
	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	if err != nil {
		t.Fatal(err)
	}
	bodies := rs.got()
	if len(bodies) != 2 || bodies[1] != `{"client_id":"test-pc","appids":[440,730]}` {
		t.Fatalf("bodies = %q, want the legacy body second", bodies)
	}
	if !result.LocalAddrsDropped || !result.PresenceDropped {
		t.Errorf("result = %+v, want both flags", result)
	}
}

// A 422 naming local_addrs for a payload that does not carry it is not the
// old-server shape: returned as is, no resend.
func TestReportInstalled_UnknownFieldNotCarriedIsFinal(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) {
		return http.StatusUnprocessableEntity, `{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"],"msg":"x"}]}`
	})
	if _, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload()); err == nil {
		t.Fatal("expected the 422")
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want 1", n)
	}
}

// A server that refuses the addresses themselves (every error under
// body.local_addrs, any type) gets the report again without them, keeping
// the presence fields.
func TestReportInstalled_LocalAddrsValueErrorResendsWithoutThem(t *testing.T) {
	for _, loc := range []string{`["body","local_addrs"]`, `["body","local_addrs",0]`} {
		var rs recordingServer
		srv := rs.start(t, func(body []byte) (int, string) {
			if strings.Contains(string(body), "local_addrs") {
				return http.StatusUnprocessableEntity, `{"detail":[{"type":"value_error","loc":` + loc + `,"msg":"x"}]}`
			}
			return http.StatusOK, okBody
		})
		result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
		if err != nil {
			t.Fatalf("loc %s: %v", loc, err)
		}
		bodies := rs.got()
		if want := `{"client_id":"test-pc","appids":[440,730],"agent_version":"0.1.0","report_interval_seconds":600}`; len(bodies) != 2 || bodies[1] != want {
			t.Errorf("loc %s: bodies = %q, want the resend %s", loc, bodies, want)
		}
		if !result.LocalAddrsDropped || result.PresenceDropped {
			t.Errorf("loc %s: result = %+v", loc, result)
		}
	}
}

// A 422 that ALSO names another field is a real rejection: not resent.
func TestReportInstalled_LocalAddrsPlusAnotherErrorIsFinal(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) {
		return http.StatusUnprocessableEntity, `{"detail":[{"type":"value_error","loc":["body","local_addrs",0],"msg":"x"},{"type":"value_error","loc":["body","client_id"],"msg":"x"}]}`
	})
	if _, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload()); err == nil {
		t.Fatal("expected the 422")
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want 1", n)
	}
}

func TestUnknownOptionalFields(t *testing.T) {
	cases := []struct {
		body string
		want []string
	}{
		{`{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"]}]}`, []string{"local_addrs"}},
		{rc8ExtraForbidden, []string{"agent_version", "report_interval_seconds"}},
		{`{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"]},{"type":"extra_forbidden","loc":["body","local_addrs"]}]}`, []string{"local_addrs"}},
		{`{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"]},{"type":"extra_forbidden","loc":["body","lan"]}]}`, nil},
		{rc8BadClientIDAndExtra, nil},
	}
	for _, tc := range cases {
		if got := unknownOptionalFields([]byte(tc.body)); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("unknownOptionalFields(%s) = %v, want %v", tc.body, got, tc.want)
		}
	}
}
