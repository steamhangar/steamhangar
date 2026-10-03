package client

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/Riviera822/steamhangar/agent/report"
)

// WP AGENT-FEAT-1: a new agent against a server that predates the
// presence fields (every vault-api up to v0.1.0-rc8).

// rc8ExtraForbidden is the exact 422 body vault-api v0.1.0-rc8 returns for
// a report carrying both presence fields, measured by posting that body to
// the rc8 tree (git archive v0.1.0-rc8 api) through FastAPI's TestClient.
const rc8ExtraForbidden = `{"detail":[{"type":"extra_forbidden","loc":["body","agent_version"],"msg":"Extra inputs are not permitted","input":"0.1.0"},{"type":"extra_forbidden","loc":["body","report_interval_seconds"],"msg":"Extra inputs are not permitted","input":600}]}`

// rc8BadClientIDAndExtra is rc8's answer when the client_id is ALSO bad
// (measured the same way, client_id "pc "): a real rejection that the
// legacy resend must never paper over.
const rc8BadClientIDAndExtra = `{"detail":[{"type":"value_error","loc":["body","client_id"],"msg":"Value error, client_id must not start or end with whitespace (it is an identity key: 'pc' and 'pc ' would be two clients)","input":"pc ","ctx":{"error":{}}},{"type":"extra_forbidden","loc":["body","agent_version"],"msg":"Extra inputs are not permitted","input":"0.1.0"},{"type":"extra_forbidden","loc":["body","report_interval_seconds"],"msg":"Extra inputs are not permitted","input":600}]}`

const okBody = `{"client_id":"test-pc","received":2,"added":[440,730],"removed":[],"first_report":true}`

func presencePayload() report.Payload {
	p, _ := testPayload().WithPresence("0.1.0", 10*time.Minute, true)
	return p
}

// recordingServer answers each request with respond(body) and records
// every request body in order.
type recordingServer struct {
	mu     sync.Mutex
	bodies []string
}

func (rs *recordingServer) start(t *testing.T, respond func(body []byte) (int, string)) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		rs.mu.Lock()
		rs.bodies = append(rs.bodies, string(body))
		rs.mu.Unlock()
		code, resp := respond(body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(code)
		_, _ = w.Write([]byte(resp))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func (rs *recordingServer) got() []string {
	rs.mu.Lock()
	defer rs.mu.Unlock()
	return append([]string(nil), rs.bodies...)
}

// legacyServer behaves like rc8's InstalledReportRequest (extra="forbid"):
// any key besides client_id/appids is a 422 with rc8's body.
func legacyServer(body []byte) (int, string) {
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		return 400, `{"detail":"bad json"}`
	}
	for k := range m {
		if k != "client_id" && k != "appids" {
			return http.StatusUnprocessableEntity, rc8ExtraForbidden
		}
	}
	return http.StatusOK, okBody
}

func TestReportInstalled_SendsPresenceFields(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusOK, okBody })

	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	bodies := rs.got()
	want := `{"client_id":"test-pc","appids":[440,730],"agent_version":"0.1.0","report_interval_seconds":600}`
	if len(bodies) != 1 || bodies[0] != want {
		t.Fatalf("request bodies = %q, want exactly [%s]", bodies, want)
	}
	if result.PresenceDropped {
		t.Error("PresenceDropped = true against a server that accepted the fields")
	}
}

func TestReportInstalled_LegacyServerGetsTheReportWithoutPresence(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, legacyServer)

	result, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	if err != nil {
		t.Fatalf("a pre-AGENT-FEAT-1 server must still get the report, got error: %v", err)
	}
	bodies := rs.got()
	if len(bodies) != 2 {
		t.Fatalf("server saw %d request(s), want 2 (with presence, then without): %q", len(bodies), bodies)
	}
	if want := `{"client_id":"test-pc","appids":[440,730]}`; bodies[1] != want {
		t.Errorf("resent body = %s, want the legacy body %s", bodies[1], want)
	}
	if !result.PresenceDropped {
		t.Error("PresenceDropped = false, want true so the agent can tell the operator")
	}
	if result.Received != 2 || !result.FirstReport {
		t.Errorf("result = %+v, want the legacy response parsed", result)
	}
}

// A 422 that names a real problem besides the unknown fields is returned
// as is: resending without presence would only hide it.
func TestReportInstalled_422WithAnotherErrorIsNotResent(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusUnprocessableEntity, rc8BadClientIDAndExtra })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("err = %v, want the 422 APIError", err)
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1", n)
	}
}

// Without presence fields in the payload there is nothing to drop, so even
// an extra_forbidden 422 is final (and is not resent in a loop).
func TestReportInstalled_LegacyPayloadIsNeverResent(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusUnprocessableEntity, rc8ExtraForbidden })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), testPayload())
	if err == nil {
		t.Fatal("expected the 422 error")
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1", n)
	}
}

// The resend happens once: if the legacy body is ALSO rejected, that error
// is the answer.
func TestReportInstalled_ResendHappensOnlyOnce(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusUnprocessableEntity, rc8ExtraForbidden })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	if err == nil {
		t.Fatal("expected an error")
	}
	if n := len(rs.got()); n != 2 {
		t.Errorf("server saw %d request(s), want exactly 2", n)
	}
}

func TestUnknownPresenceFieldsOnly(t *testing.T) {
	cases := []struct {
		name string
		body string
		want bool
	}{
		{"rc8 both fields", rc8ExtraForbidden, true},
		{"rc8 one field", `{"detail":[{"type":"extra_forbidden","loc":["body","agent_version"],"msg":"x","input":"dev"}]}`, true},
		{"rc8 plus a real error", rc8BadClientIDAndExtra, false},
		{"another unknown field", `{"detail":[{"type":"extra_forbidden","loc":["body","appIds"],"msg":"x"}]}`, false},
		{"presence field with another error type", `{"detail":[{"type":"string_pattern_mismatch","loc":["body","agent_version"],"msg":"x"}]}`, false},
		{"not in the body", `{"detail":[{"type":"extra_forbidden","loc":["query","agent_version"],"msg":"x"}]}`, false},
		{"nested loc", `{"detail":[{"type":"extra_forbidden","loc":["body","agent_version","x"],"msg":"x"}]}`, false},
		{"numeric loc", `{"detail":[{"type":"extra_forbidden","loc":["body",1],"msg":"x"}]}`, false},
		{"empty detail", `{"detail":[]}`, false},
		{"string detail", `{"detail":"bad client_id"}`, false},
		{"not json", `<html>proxy error</html>`, false},
		{"empty", ``, false},
	}
	for _, tc := range cases {
		if got := unknownPresenceFieldsOnly([]byte(tc.body)); got != tc.want {
			t.Errorf("%s: unknownPresenceFieldsOnly = %v, want %v", tc.name, got, tc.want)
		}
	}
}

// Only a 422 can mean "the server predates the fields": a 400 (e.g. from a
// reverse proxy) that happens to carry the same body is final.
func TestReportInstalled_OnlyA422TriggersTheResend(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusBadRequest, rc8ExtraForbidden })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusBadRequest {
		t.Fatalf("err = %v, want the 400 APIError", err)
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1", n)
	}
}
