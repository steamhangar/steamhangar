package report

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// WP AGENT-FEAT-1: the optional presence fields of the report payload.

func marshal(t *testing.T, p Payload) string {
	t.Helper()
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return string(b)
}

func basePayload() Payload {
	return Payload{ClientID: "pc", AppIDs: []int{440}}
}

func TestWithPresence_CarriesVersionAndInterval(t *testing.T) {
	p, notes := basePayload().WithPresence("0.1.0-rc9", 10*time.Minute, true)
	if len(notes) != 0 {
		t.Errorf("notes = %q, want none", notes)
	}
	got := marshal(t, p)
	want := `{"client_id":"pc","appids":[440],"agent_version":"0.1.0-rc9","report_interval_seconds":600}`
	if got != want {
		t.Errorf("body = %s\nwant   %s", got, want)
	}
}

// Without presence the body is byte-identical to the pre-AGENT-FEAT-1
// payload: this is what the client resends to an old server.
func TestPayload_WithoutPresenceIsTheLegacyBody(t *testing.T) {
	p, _ := basePayload().WithPresence("dev", 10*time.Minute, true)
	if !p.HasPresence() {
		t.Fatal("HasPresence() = false after WithPresence")
	}
	legacy := p.WithoutPresence()
	if legacy.HasPresence() {
		t.Error("HasPresence() = true after WithoutPresence")
	}
	if got, want := marshal(t, legacy), `{"client_id":"pc","appids":[440]}`; got != want {
		t.Errorf("legacy body = %s, want %s", got, want)
	}
	// WithoutPresence returns a copy; the original keeps its fields.
	if p.AgentVersion == nil || p.ReportIntervalSeconds == nil {
		t.Error("WithoutPresence modified its receiver")
	}
}

func TestWithPresence_UnknownIntervalIsNotSent(t *testing.T) {
	p, notes := basePayload().WithPresence("dev", 10*time.Minute, false)
	if p.ReportIntervalSeconds != nil {
		t.Errorf("report_interval_seconds = %d, want it omitted when the interval is not known", *p.ReportIntervalSeconds)
	}
	if p.AgentVersion == nil || *p.AgentVersion != "dev" {
		t.Errorf("agent_version = %v, want \"dev\"", p.AgentVersion)
	}
	if len(notes) != 1 || !strings.Contains(notes[0], "one-shot run without --interval") {
		t.Errorf("notes = %q, want one note naming the missing --interval", notes)
	}
	if strings.Contains(marshal(t, p), "report_interval_seconds") {
		t.Errorf("body %s still names report_interval_seconds", marshal(t, p))
	}
}

// The server's range is 60..86400 seconds inclusive; anything outside
// would 422 the whole report, so it is left out instead.
func TestWithPresence_IntervalRangeBoundaries(t *testing.T) {
	cases := []struct {
		interval time.Duration
		want     int // 0 = omitted
	}{
		{59 * time.Second, 0},
		{59999 * time.Millisecond, 0}, // truncates to 59
		{60 * time.Second, 60},
		{90500 * time.Millisecond, 90}, // whole seconds
		{24 * time.Hour, 86400},
		{24*time.Hour + time.Second, 0},
	}
	for _, tc := range cases {
		p, notes := basePayload().WithPresence("dev", tc.interval, true)
		if tc.want == 0 {
			if p.ReportIntervalSeconds != nil {
				t.Errorf("%v: report_interval_seconds = %d, want omitted", tc.interval, *p.ReportIntervalSeconds)
			}
			if len(notes) != 1 || !strings.Contains(notes[0], "outside 60s..86400s") {
				t.Errorf("%v: notes = %q, want one out-of-range note", tc.interval, notes)
			}
			continue
		}
		if p.ReportIntervalSeconds == nil || *p.ReportIntervalSeconds != tc.want {
			t.Errorf("%v: report_interval_seconds = %v, want %d", tc.interval, p.ReportIntervalSeconds, tc.want)
		}
		if len(notes) != 0 {
			t.Errorf("%v: notes = %q, want none", tc.interval, notes)
		}
	}
}

func TestWithPresence_InvalidVersionIsNotSent(t *testing.T) {
	p, notes := basePayload().WithPresence("0.1.0 rc9", 10*time.Minute, true)
	if p.AgentVersion != nil {
		t.Errorf("agent_version = %q, want omitted", *p.AgentVersion)
	}
	if p.ReportIntervalSeconds == nil {
		t.Error("report_interval_seconds omitted, want it sent: only the version is bad")
	}
	if len(notes) != 1 || !strings.Contains(notes[0], "agent_version not sent") {
		t.Errorf("notes = %q, want one agent_version note", notes)
	}
}

// Parity with vault_api's _VERSION_GRAMMAR (fullmatch, re.ASCII). The
// Python side pins the regex text itself
// (api/tests/test_agent_feat_1_packaging.py); these are the behaviours.
func TestValidAgentVersion_Grammar(t *testing.T) {
	valid := []string{
		"0.1.0", "0.1.0-rc8", "dev", "dev-1a2b3c4", "ci-1a2b3c4", "1+build.5", "a_b",
		"x" + strings.Repeat("y", 63), // 64 characters
	}
	invalid := []string{
		"", "-dev", ".1", "_x", "+x", "0.1.0 rc8", "0.1.0\n", "dev\r", "a/b", `a"b`,
		"x" + strings.Repeat("y", 64), // 65 characters
		"v١",                          // non-ASCII digit
		"café",
	}
	for _, v := range valid {
		if !ValidAgentVersion(v) {
			t.Errorf("ValidAgentVersion(%q) = false, want true", v)
		}
	}
	for _, v := range invalid {
		if ValidAgentVersion(v) {
			t.Errorf("ValidAgentVersion(%q) = true, want false", v)
		}
	}
}
