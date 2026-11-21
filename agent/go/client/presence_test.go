package client

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Riviera822/steamhangar/agent/report"
)

// WP AGENT-FEAT-1 sends the presence fields; WP AGENT-RC8-COMPAT-1
// (rc23) removed the resend to servers that predate them. These pins
// cover what is left: the fields go out, and an old server's 422 is final
// and says "update vault-api".

// rc8ExtraForbidden is the exact 422 body vault-api v0.1.0-rc8 returns for
// a report carrying both presence fields, measured by posting that body to
// the rc8 tree (git archive v0.1.0-rc8 api) through FastAPI's TestClient.
const rc8ExtraForbidden = `{"detail":[{"type":"extra_forbidden","loc":["body","agent_version"],"msg":"Extra inputs are not permitted","input":"0.1.0"},{"type":"extra_forbidden","loc":["body","report_interval_seconds"],"msg":"Extra inputs are not permitted","input":600}]}`

// rc8BadClientIDAndExtra is rc8's answer when the client_id is ALSO bad
// (measured the same way, client_id "pc ").
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
	if result.Received != 2 || !result.FirstReport {
		t.Errorf("result = %+v, want the response parsed", result)
	}
}

// tooOld asserts err is the 422 APIError naming exactly fields as unknown,
// with the "update vault-api" hint and the minimum version in its message.
func tooOld(t *testing.T, err error, fields ...string) *APIError {
	t.Helper()
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("err = %v, want the 422 APIError", err)
	}
	if !reflect.DeepEqual(apiErr.UnknownFields, fields) {
		t.Errorf("UnknownFields = %q, want %q", apiErr.UnknownFields, fields)
	}
	msg := err.Error()
	for _, want := range append([]string{"vault-api is older than this agent", "update vault-api to " + MinServerVersion + " or newer"}, fields...) {
		if !strings.Contains(msg, want) {
			t.Errorf("error %q does not contain %q", msg, want)
		}
	}
	return apiErr
}

// An rc8 server refuses the presence fields: the report is NOT resent and
// the error says the server must be updated.
func TestReportInstalled_Rc8ServerIsNotResentAndSaysUpdate(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusUnprocessableEntity, rc8ExtraForbidden })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	tooOld(t, err, "agent_version", "report_interval_seconds")
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1 (no resend)", n)
	}
}

// A 422 that also names a real problem keeps the hint (the server is still
// too old) and shows the real problem in the body excerpt.
func TestReportInstalled_422WithAnotherErrorKeepsBoth(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusUnprocessableEntity, rc8BadClientIDAndExtra })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	apiErr := tooOld(t, err, "agent_version", "report_interval_seconds")
	if !strings.Contains(apiErr.Body, "client_id must not start or end with whitespace") {
		t.Errorf("Body = %q, want the client_id error kept", apiErr.Body)
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1", n)
	}
}

func TestUnknownOptionalFields(t *testing.T) {
	cases := []struct {
		name string
		body string
		want []string
	}{
		{"rc8 both fields", rc8ExtraForbidden, []string{"agent_version", "report_interval_seconds"}},
		{"rc16 local_addrs", `{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"],"msg":"x"}]}`, []string{"local_addrs"}},
		{"duplicate entry", `{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"]},{"type":"extra_forbidden","loc":["body","local_addrs"]}]}`, []string{"local_addrs"}},
		{"rc8 plus a real error", rc8BadClientIDAndExtra, []string{"agent_version", "report_interval_seconds"}},
		{"optional plus a non-optional unknown", `{"detail":[{"type":"extra_forbidden","loc":["body","local_addrs"]},{"type":"extra_forbidden","loc":["body","lan"]}]}`, []string{"local_addrs"}},
		{"another unknown field", `{"detail":[{"type":"extra_forbidden","loc":["body","appIds"],"msg":"x"}]}`, nil},
		{"optional field with another error type", `{"detail":[{"type":"value_error","loc":["body","local_addrs"],"msg":"x"}]}`, nil},
		{"error inside an optional field", `{"detail":[{"type":"value_error","loc":["body","local_addrs",0],"msg":"x"}]}`, nil},
		{"not in the body", `{"detail":[{"type":"extra_forbidden","loc":["query","agent_version"],"msg":"x"}]}`, nil},
		{"nested loc", `{"detail":[{"type":"extra_forbidden","loc":["body","agent_version","x"],"msg":"x"}]}`, nil},
		{"numeric loc", `{"detail":[{"type":"extra_forbidden","loc":["body",1],"msg":"x"}]}`, nil},
		{"empty detail", `{"detail":[]}`, nil},
		{"string detail", `{"detail":"bad client_id"}`, nil},
		{"not json", `<html>proxy error</html>`, nil},
		{"empty", ``, nil},
	}
	for _, tc := range cases {
		if got := unknownOptionalFields([]byte(tc.body)); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: unknownOptionalFields = %q, want %q", tc.name, got, tc.want)
		}
	}
}

// Only a 422 is FastAPI's validation answer: a 400 (e.g. from a reverse
// proxy) that happens to carry the same body gets no "too old" hint.
func TestReportInstalled_OnlyA422SaysServerTooOld(t *testing.T) {
	var rs recordingServer
	srv := rs.start(t, func([]byte) (int, string) { return http.StatusBadRequest, rc8ExtraForbidden })

	_, err := New(srv.URL, "k", testBackoff()).ReportInstalled(context.Background(), presencePayload())
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusBadRequest {
		t.Fatalf("err = %v, want the 400 APIError", err)
	}
	if len(apiErr.UnknownFields) != 0 || strings.Contains(err.Error(), "update vault-api") {
		t.Errorf("err = %v, want no server-too-old hint on a 400", err)
	}
	if n := len(rs.got()); n != 1 {
		t.Errorf("server saw %d request(s), want exactly 1", n)
	}
}
