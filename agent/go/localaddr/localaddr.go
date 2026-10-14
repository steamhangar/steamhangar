// Package localaddr finds the LAN address(es) this machine uses toward the
// vault (WP AGENT-ADDR-1).
//
// Why the agent says it at all: behind Docker's published ports vault-api
// sees every connection coming from the Docker bridge gateway, never from
// the PC, so the TCP peer of a report cannot tell vault-api which address
// this PC's Steam downloads show up under in vault-core's cache log.
// vault-core sees the PC's real LAN address; so does this agent.
//
// How: for every address the vault host resolves to, ask the OS which
// local address it would send from - a UDP "connect" (no packet is sent,
// UDP connect only selects the route and the source address) and read
// LocalAddr. That is the source address the kernel picks for the TCP
// connection to the same destination too, so it is the address vault-core
// logs for this PC when Steam talks to the cache on the same host. IPv4 and
// IPv6 are both covered: an A and an AAAA record each give their own
// source address.
//
// Loopback, link-local, unspecified and multicast results are dropped
// (Usable): an agent on the vault host itself, or a link-local-only path,
// gives no address another host could log, and vault-api refuses those
// values anyway. Nothing found is a normal outcome: the report then simply
// carries no local_addrs and vault-api falls back to the TCP peer.
//
// Detection lives in the binary only (user constraint 2026-10-14): no flag,
// no environment variable, no config entry, no scheduled-task argument.
package localaddr

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"sort"
	"strings"
)

// MaxAddrs mirrors vault_api.agent_reports.MAX_LOCAL_ADDRS: the most
// addresses one report may state.
const MaxAddrs = 8

// maxDestinations bounds how many resolved vault addresses are probed. A
// home vault resolves to one or two; this only bounds a pathological DNS
// answer.
const maxDestinations = 16

// Resolver is the subset of *net.Resolver Detect needs.
type Resolver interface {
	LookupIPAddr(ctx context.Context, host string) ([]net.IPAddr, error)
}

// Dialer opens the probe "connection". *net.Dialer satisfies it.
type Dialer interface {
	DialContext(ctx context.Context, network, address string) (net.Conn, error)
}

// Detect returns the usable local addresses toward the host in serverURL
// (canonical, sorted, de-duplicated, at most MaxAddrs) plus one note per
// thing that went wrong. It never fails: a note is for the log, and an
// empty result is a valid answer. resolver and dialer may be nil for the
// system ones.
func Detect(ctx context.Context, serverURL string, resolver Resolver, dialer Dialer) ([]string, []string) {
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	if dialer == nil {
		dialer = &net.Dialer{}
	}
	var notes []string

	u, err := url.Parse(serverURL)
	if err != nil || u.Hostname() == "" {
		return nil, []string{"local address not detected: server URL has no host"}
	}
	host := u.Hostname()
	port := u.Port()
	if port == "" {
		port = "80"
		if strings.EqualFold(u.Scheme, "https") {
			port = "443"
		}
	}

	var destinations []netip.Addr
	if literal, err := netip.ParseAddr(host); err == nil {
		destinations = append(destinations, literal.Unmap())
	} else {
		resolved, err := resolver.LookupIPAddr(ctx, host)
		if err != nil {
			return nil, []string{fmt.Sprintf("local address not detected: resolving %q failed: %v", host, err)}
		}
		for _, ia := range resolved {
			if a, ok := netip.AddrFromSlice(ia.IP); ok {
				destinations = append(destinations, a.Unmap())
			}
		}
	}
	if len(destinations) > maxDestinations {
		destinations = destinations[:maxDestinations]
	}

	found := map[string]struct{}{}
	for _, dest := range destinations {
		conn, err := dialer.DialContext(ctx, "udp", net.JoinHostPort(dest.String(), port))
		if err != nil {
			notes = append(notes, fmt.Sprintf("no route to %s: %v", dest, err))
			continue
		}
		local := conn.LocalAddr()
		_ = conn.Close()
		addr, ok := addrOf(local)
		if !ok {
			continue
		}
		if canonical, ok := Usable(addr.String()); ok {
			found[canonical] = struct{}{}
		}
	}

	out := make([]string, 0, len(found))
	for a := range found {
		out = append(out, a)
	}
	sort.Strings(out)
	if len(out) > MaxAddrs {
		out = out[:MaxAddrs]
	}
	return out, notes
}

// addrOf extracts the IP of a net.Addr returned by LocalAddr.
func addrOf(a net.Addr) (netip.Addr, bool) {
	switch v := a.(type) {
	case *net.UDPAddr:
		ip, ok := netip.AddrFromSlice(v.IP)
		return ip.Unmap(), ok
	case *net.TCPAddr:
		ip, ok := netip.AddrFromSlice(v.IP)
		return ip.Unmap(), ok
	}
	if a == nil {
		return netip.Addr{}, false
	}
	ap, err := netip.ParseAddrPort(a.String())
	if err != nil {
		return netip.Addr{}, false
	}
	return ap.Addr().Unmap(), true
}

// Usable applies vault-api's rule for one local_addrs entry
// (vault_api.agent_reports.normalize_local_addr) and returns the canonical
// spelling: a plain IP literal, no zone id, not loopback, unspecified,
// link-local or multicast; an IPv4-mapped IPv6 address becomes plain IPv4.
// The report package uses it to drop anything the server would 422 on.
func Usable(s string) (string, bool) {
	if s != strings.TrimSpace(s) || strings.Contains(s, "%") {
		return "", false
	}
	a, err := netip.ParseAddr(s)
	if err != nil {
		return "", false
	}
	a = a.Unmap()
	if a.IsLoopback() || a.IsUnspecified() || a.IsLinkLocalUnicast() ||
		a.IsMulticast() || a.IsLinkLocalMulticast() || a.IsInterfaceLocalMulticast() {
		return "", false
	}
	return a.String(), true
}
