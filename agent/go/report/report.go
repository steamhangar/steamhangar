// Package report builds and client-side validates the payload vault-agent
// posts to vault-api's `POST /v1/agent/installed` (WP 2.2, ADR-0002).
//
// The wire contract is THE CONTRACT this package targets, defined in
// api/vault_api/routers/agent.py + api/vault_api/agent_reports.py +
// api/vault_api/validation.py and documented in api/README.md's "Agent
// reports" section:
//
//	POST /v1/agent/installed
//	{"client_id": "<1-64 char id>", "appids": [<int ge=1>, ...],
//	 "agent_version": "<VER-1 grammar>",       (optional, WP AGENT-FEAT-1)
//	 "report_interval_seconds": <60..86400>}   (optional, WP AGENT-FEAT-1)
//
// The two optional presence fields are attached with Payload.WithPresence
// and are only ever sent when they pass the server's own rules (see
// WithPresence); a server that predates them rejects them, and
// agent/go/client then resends the report without them (see that
// package's "Servers that predate the presence fields" section).
//
// BuildReport mirrors the server's validation rules LOCALLY, so a
// misconfigured agent fails fast with a clear message instead of spending a
// round trip on a 422 the server would have returned anyway (this package
// has no network dependency at all — see agent/go/client for the HTTP
// side).
package report

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Riviera822/steamhangar/agent/acf"
)

// MaxClientIDLength mirrors vault_api.agent_reports.MAX_CLIENT_ID_LENGTH.
const MaxClientIDLength = 64

// MaxAppIDs mirrors vault_api.agent_reports.MAX_APPIDS_PER_REPORT.
const MaxAppIDs = 10_000

// MinReportIntervalSeconds and MaxReportIntervalSeconds mirror
// vault_api.agent_reports.MIN_/MAX_REPORT_INTERVAL_SECONDS (WP
// AGENT-FEAT-1): the range the server accepts for report_interval_seconds.
const (
	MinReportIntervalSeconds = 60
	MaxReportIntervalSeconds = 24 * 60 * 60
)

// agentVersionGrammar mirrors vault_api's VER-1 version grammar
// (api/vault_api/__init__.py _VERSION_GRAMMAR, also publish.yml's version
// step): a letter or digit, then letters, digits and ". _ + -", at most 64
// characters. In Go's RE2 syntax, $ without (?m) matches only at the end of
// the text (unlike Python's $, it does not also match before a trailing
// newline), so ^...$ is a whole-string match like Python's fullmatch.
var agentVersionGrammar = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`)

// ValidAgentVersion reports whether v passes the server's agent_version
// rule (the VER-1 version grammar).
func ValidAgentVersion(v string) bool {
	return agentVersionGrammar.MatchString(v)
}

// Payload is the exact JSON body `POST /v1/agent/installed` expects.
// AppIDs is always sorted ascending and de-duplicated — BuildReport is the
// only constructor and guarantees both, so the wire format is deterministic
// (easier to diff in logs/tests than an arbitrary order would be).
//
// AgentVersion and ReportIntervalSeconds (WP AGENT-FEAT-1) are pointers
// with omitempty, so a payload without them marshals to exactly the
// pre-AGENT-FEAT-1 body: that legacy body is what agent/go/client resends
// to a server that rejects the new fields.
type Payload struct {
	ClientID              string  `json:"client_id"`
	AppIDs                []int   `json:"appids"`
	AgentVersion          *string `json:"agent_version,omitempty"`
	ReportIntervalSeconds *int    `json:"report_interval_seconds,omitempty"`
}

// HasPresence reports whether either optional presence field is set.
func (p Payload) HasPresence() bool {
	return p.AgentVersion != nil || p.ReportIntervalSeconds != nil
}

// WithoutPresence returns a copy of p with both presence fields removed:
// the body a pre-AGENT-FEAT-1 server accepts.
func (p Payload) WithoutPresence() Payload {
	p.AgentVersion = nil
	p.ReportIntervalSeconds = nil
	return p
}

// WithPresence returns a copy of p carrying the agent's version and its
// report interval (WP AGENT-FEAT-1), plus one note per value it left out.
//
// A value is left out, never sent, when the server would reject it: a
// version outside the VER-1 grammar, or an interval outside
// MinReportIntervalSeconds..MaxReportIntervalSeconds. A rejected field
// would 422 the WHOLE report, so an odd -ldflags version or a 30s test
// interval would otherwise stop the agent reporting at all. A left-out
// field reads as "unknown" server-side: version unknown, interval assumed
// 30 minutes.
//
// intervalKnown is false when nobody told the agent how often it runs: a
// one-shot `report` without --interval / VAULT_AGENT_REPORT_INTERVAL. The
// scheduler that starts it may run it at any cadence (an install from
// before AGENT-FEAT-1 runs it every 30 minutes), so the agent does not
// claim its own default for it. --loop always knows: it times itself.
func (p Payload) WithPresence(version string, interval time.Duration, intervalKnown bool) (Payload, []string) {
	var notes []string
	p.AgentVersion = nil
	p.ReportIntervalSeconds = nil
	if ValidAgentVersion(version) {
		v := version
		p.AgentVersion = &v
	} else {
		notes = append(notes, fmt.Sprintf(
			"agent_version not sent: build version %q is outside the version grammar", version))
	}
	if intervalKnown {
		seconds := int(interval / time.Second)
		if seconds >= MinReportIntervalSeconds && seconds <= MaxReportIntervalSeconds {
			p.ReportIntervalSeconds = &seconds
		} else {
			notes = append(notes, fmt.Sprintf(
				"report_interval_seconds not sent: interval %s is outside %ds..%ds; vault-api assumes 30m",
				interval, MinReportIntervalSeconds, MaxReportIntervalSeconds))
		}
	} else {
		notes = append(notes,
			"report_interval_seconds not sent: one-shot run without --interval/VAULT_AGENT_REPORT_INTERVAL; "+
				"vault-api assumes 30m (set it to the scheduler's cadence)")
	}
	return p, notes
}

// ValidationError is returned by BuildReport/ValidateClientID for any
// locally-rejected input. Kept as a distinct type (rather than a bare
// fmt.Errorf) so callers (cmd/vault-agent) can tell "this would have 422'd
// the server anyway" apart from a network/transport error and choose the
// right exit code without string-matching.
type ValidationError struct {
	Msg string
}

func (e *ValidationError) Error() string { return e.Msg }

func validationErrorf(format string, args ...any) *ValidationError {
	return &ValidationError{Msg: fmt.Sprintf(format, args...)}
}

// ValidateClientID applies the SAME rules
// vault_api.routers.agent.InstalledReportRequest._validate_client_id
// enforces server-side (api/vault_api/routers/agent.py), so a bad
// client_id (including the default derived from the local hostname, see
// agent/go/agentconfig) is rejected here rather than round-tripped to the
// server for a 422:
//
//   - 1-64 CHARACTERS (Unicode code points, matching Python's len() —
//     not bytes: utf8.RuneCountInString, not len(string)).
//   - No leading/trailing whitespace (it is an identity key: "pc" and
//     "pc " must not silently become two different clients).
//   - Every rune must be PRINTABLE (unicode.IsPrint), matching Python's
//     str.isprintable() rule the server enforces (WP 2.2 review finding
//     S1, verified against 46 parity cases spanning the server's
//     Pydantic validator): rejects ASCII control characters (NUL, tab,
//     CR, LF — the value is written into log lines, so a newline-like
//     character would let a malformed agent forge a fake log line),
//     Unicode format characters (Cf — e.g. a zero-width joiner/non-joiner,
//     invisible and would make two visually-identical ids compare
//     unequal), private-use (Co) and unassigned (Cn) codepoints, and
//     every space-like separator except the plain ASCII space (Zs other
//     than U+0020, plus Zl/Zp). A single ordinary emoji (category So,
//     Symbol) IS printable and stays allowed; a ZWJ-joined compound emoji
//     sequence is rejected because it contains a Cf joiner rune, exactly
//     matching the server's 422 for the same input. An earlier version of
//     this check used unicode.IsControl plus an explicit Zl/Zp
//     allowlist-complement, which missed Cf/Co/Cn and non-ASCII Zs
//     (e.g. NBSP) entirely — unicode.IsPrint is the single check the
//     Python parity sweep confirms matches every case.
//   - Not "." or "..".
func ValidateClientID(clientID string) error {
	length := utf8.RuneCountInString(clientID)
	if length < 1 || length > MaxClientIDLength {
		return validationErrorf(
			"client_id must be 1-%d characters, got %d (%q)",
			MaxClientIDLength, length, clientID,
		)
	}
	if strings.TrimSpace(clientID) != clientID {
		return validationErrorf(
			"client_id must not start or end with whitespace "+
				"(it is an identity key: %q and %q would be two clients): %q",
			clientID, clientID+" ", clientID,
		)
	}
	for _, r := range clientID {
		if !unicode.IsPrint(r) {
			return validationErrorf(
				"client_id must not contain non-printable characters "+
					"(control characters, invisible format characters like a zero-width "+
					"joiner, private-use or unassigned codepoints, non-ASCII spaces); "+
					"use a plain label such as a hostname: %q", clientID,
			)
		}
	}
	if clientID == "." || clientID == ".." {
		return validationErrorf(
			`client_id must not be "." or ".."; use a plain label such as a hostname`,
		)
	}
	return nil
}

// BuildReport filters apps down to the installed ones (StateFlags bit 4,
// see acf.InstalledApp.Installed), de-duplicates by app id, sorts
// ascending for a deterministic wire format, and validates the result
// against the server's rules before returning it.
//
// clientID is validated with ValidateClientID. Each app's AppID string is
// converted to an int and validated ge=1 (mirroring
// vault_api.validation.AppId's Field(ge=1)) — acf's own parser already
// enforces a strict ASCII-digit grammar on appid (agent/go/acf/
// appmanifest.go), so a conversion failure here would indicate a bug in
// that invariant rather than a real-world manifest, but it is still
// checked explicitly rather than assumed, and appid "0" (grammatically
// valid digits, but not ge=1) IS a real case this rejects. The full
// installed-app-count cap (MaxAppIDs) is checked AFTER de-duplication,
// matching the server, which counts distinct ids.
func BuildReport(apps []acf.InstalledApp, clientID string) (Payload, error) {
	if err := ValidateClientID(clientID); err != nil {
		return Payload{}, err
	}

	seen := make(map[int]struct{}, len(apps))
	ids := make([]int, 0, len(apps))
	for _, app := range apps {
		if !app.Installed() {
			continue
		}
		id, err := strconv.Atoi(app.AppID)
		if err != nil {
			return Payload{}, validationErrorf(
				"app %q: appid %q is not a valid integer: %s", app.Name, app.AppID, err,
			)
		}
		if id < 1 {
			return Payload{}, validationErrorf(
				"app %q: appid %d must be >= 1", app.Name, id,
			)
		}
		if _, dup := seen[id]; dup {
			continue
		}
		seen[id] = struct{}{}
		ids = append(ids, id)
	}

	if len(ids) > MaxAppIDs {
		return Payload{}, validationErrorf(
			"report has %d distinct installed app id(s), exceeds the %d cap", len(ids), MaxAppIDs,
		)
	}

	sort.Ints(ids)

	return Payload{ClientID: clientID, AppIDs: ids}, nil
}
