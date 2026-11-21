package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"strings"
	"testing"

	"github.com/Riviera822/steamhangar/agent/report"
)

// WP AGENT-ADDR-1: local_addrs, and (WP AGENT-RC8-COMPAT-1, rc23) what a
// server that predates it answers now that the agent no longer resends.

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
	return presencePayload().WithLocalAddrs([]string{"10.0.0.42"})
}

func TestReportInstalled_SendsLocalAddrs(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids", "agent_version", "report_interval_seconds", "local_addrs"))
	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	if err != nil {
		t.Fatal(err)
	}
	bodies := rs.got()
	want := `{"client_id":"test-pc","appids":[440,730],"agent_version":"0.1.0","report_interval_seconds":600,"local_addrs":["10.0.0.42"]}`
	if len(bodies) != 1 || bodies[0] != want {
		t.Fatalf("bodies = %q, want exactly [%s]", bodies, want)
	}
	if result.Received != 2 {
		t.Errorf("result = %+v, want the response parsed", result)
	}
}

// rc9..rc16 know the presence fields but not local_addrs: one request,
// a final 422 that names local_addrs and says to update vault-api.
func TestReportInstalled_PreAddrServerIsNotResentAndSaysUpdate(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids", "agent_version", "report_interval_seconds"))
	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	tooOld(t, err, "local_addrs")
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1 (no resend)", n)
	}
}

// rc8 knows none of the optional fields: all three are named, no resend.
func TestReportInstalled_Rc8ServerNamesAllThreeFields(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids"))
	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
	tooOld(t, err, "agent_version", "local_addrs", "report_interval_seconds")
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1 (no resend)", n)
	}
}

// No usable LAN address means no local_addrs field at all (omitempty), so
// such a report is accepted by an rc9..rc16 server as it always was.
func TestReportInstalled_WithoutLocalAddrsPreAddrServerAccepts(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, forbidExtras("client_id", "appids", "agent_version", "report_interval_seconds"))
	if _, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want 1", n)
	}
}

// A server that refuses the address values themselves (an error under
// body.local_addrs that is not extra_forbidden) is a plain 422: no resend
// and no "too old" hint, since that server does know the field.
func TestReportInstalled_LocalAddrsValueErrorIsFinal(t *testing.T) {
	for _, loc := range []string{`["body","local_addrs"]`, `["body","local_addrs",0]`} {
		var rs recordingServer
		srv := rs.start(t, func(body []byte) (int, string) {
			if strings.Contains(string(body), "local_addrs") {
				return http.StatusUnprocessableEntity, `{"detail":[{"type":"value_error","loc":` + loc + `,"msg":"x"}]}`
			}
			return http.StatusOK, okBody
		})
		_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), newPayload())
		var apiErr *APIError
		if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusUnprocessableEntity {
			t.Fatalf("loc %s: err = %v, want the 422 APIError", loc, err)
		}
		if len(apiErr.UnknownFields) != 0 || strings.Contains(err.Error(), "update vault-api") {
			t.Errorf("loc %s: err = %v, want no server-too-old hint", loc, err)
		}
		if n := len(rs.got()); n != 1 {
			t.Errorf("loc %s: server saw %d request(s), want 1", loc, n)
		}
	}
}
