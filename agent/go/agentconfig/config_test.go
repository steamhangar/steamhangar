package agentconfig

import (
	"bytes"
	"errors"
	"flag"
	"io"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

func emptyEnv(string) string { return "" }

func envMap(m map[string]string) Getenv {
	return func(key string) string { return m[key] }
}

// parseDiscard is a thin Parse wrapper for tests that don't care about
// flag.FlagSet's usage/error output - it goes to io.Discard instead of
// being asserted on. Tests that DO care about that output (the B1
// redaction proofs below) call Parse directly with their own buffer.
func parseDiscard(args []string, getenv Getenv) (Config, error) {
	return Parse("report", args, getenv, io.Discard)
}

func TestParse_MinimalValidConfig(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080",
		"--api-key", "s3cr3t",
		"--client-id", "gaming-pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.1:8080" {
		t.Errorf("ServerURL = %q", cfg.ServerURL)
	}
	if cfg.APIKey != "s3cr3t" {
		t.Errorf("APIKey = %q", cfg.APIKey)
	}
	if cfg.ClientID != "gaming-pc" {
		t.Errorf("ClientID = %q", cfg.ClientID)
	}
	if cfg.ReportInterval != DefaultReportInterval {
		t.Errorf("ReportInterval = %v, want default %v", cfg.ReportInterval, DefaultReportInterval)
	}
	if cfg.LibraryRoot == "" {
		t.Error("LibraryRoot should have a default, got empty")
	}
}

func TestParse_TrailingSlashStrippedFromServerURL(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080/",
		"--api-key", "k",
		"--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.1:8080" {
		t.Errorf("ServerURL = %q, want trailing slash stripped", cfg.ServerURL)
	}
}

func TestParse_MissingServerURLFailsLoudly(t *testing.T) {
	_, err := parseDiscard([]string{"--api-key", "k", "--client-id", "pc"}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a missing server URL")
	}
	if !strings.Contains(err.Error(), "server URL is required") {
		t.Errorf("error = %v, want it to mention the missing server URL", err)
	}
}

func TestParse_InvalidServerURLScheme(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "ftp://example.com", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a non-http(s) scheme")
	}
	if !strings.Contains(err.Error(), "http://") {
		t.Errorf("error = %v, want it to mention the http(s) requirement", err)
	}
}

func TestParse_ServerURLWithNoHost(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a URL with no host")
	}
}

func TestParse_MissingAPIKeyFailsLoudly(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a missing API key")
	}
	if !strings.Contains(err.Error(), "API key is required") {
		t.Errorf("error = %v, want it to mention the missing API key", err)
	}
}

func TestParse_APIKeyNeverAppearsInErrorMessages(t *testing.T) {
	// Even when the config is otherwise invalid (missing server URL), the
	// supplied API key must never leak into the aggregated error text.
	_, err := parseDiscard([]string{
		"--api-key", "super-secret-value", "--client-id", "..",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error")
	}
	if strings.Contains(err.Error(), "super-secret-value") {
		t.Fatalf("API key leaked into error message: %v", err)
	}
}

// --- B1 (review finding): the API key must never appear in flag.FlagSet's
// usage/error OUTPUT either - not just the aggregated ParseError text
// above. flag.Usage() (invoked on -h AND on an unrecognized flag) prints
// every flag's CURRENT DEFAULT VALUE. Registering getenv(EnvAPIKey) as
// the "api-key" flag's default (the pre-fix code) would print the real
// key the moment anyone ran `vault-agent report -h` or mistyped a flag -
// regardless of whether --api-key was given on that invocation at all.
// These tests set a canary key via the env (exactly the vector that
// leaked) and assert it never appears in Parse's output writer.

func TestParse_HelpOutputNeverContainsAPIKey(t *testing.T) {
	env := envMap(map[string]string{EnvAPIKey: "CANARY-HELP-LEAK-VALUE"})
	var out bytes.Buffer
	_, err := Parse("report", []string{"-h"}, env, &out)
	if !errors.Is(err, flag.ErrHelp) {
		t.Fatalf("err = %v, want flag.ErrHelp", err)
	}
	if strings.Contains(out.String(), "CANARY-HELP-LEAK-VALUE") {
		t.Fatalf("API key leaked into -h output:\n%s", out.String())
	}
}

func TestParse_UnknownFlagOutputNeverContainsAPIKey(t *testing.T) {
	env := envMap(map[string]string{EnvAPIKey: "CANARY-UNKNOWN-FLAG-LEAK"})
	var out bytes.Buffer
	_, err := Parse("report", []string{"--this-flag-does-not-exist"}, env, &out)
	if err == nil {
		t.Fatal("expected an error for an unrecognized flag")
	}
	if strings.Contains(out.String(), "CANARY-UNKNOWN-FLAG-LEAK") {
		t.Fatalf("API key leaked into unknown-flag output:\n%s", out.String())
	}
}

func TestParse_OutputGoesToTheGivenWriter(t *testing.T) {
	// Proves fs.SetOutput(output) is actually wired - a caller that
	// captures its own stderr must see -h's usage text there, not on the
	// real os.Stderr.
	var out bytes.Buffer
	_, _ = Parse("report", []string{"-h"}, emptyEnv, &out)
	if out.Len() == 0 {
		t.Fatal("expected -h usage text in the provided output writer, got nothing")
	}
	if !strings.Contains(out.String(), "server-url") {
		t.Errorf("output = %q, want it to list the server-url flag", out.String())
	}
}

func TestParse_InvalidExplicitClientID(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "..",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for client-id \"..\"")
	}
}

func TestParse_InvalidIntervalFormat(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
		"--interval", "notaduration",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a malformed --interval")
	}
	if !strings.Contains(err.Error(), "not a valid duration") {
		t.Errorf("error = %v, want it to mention the invalid duration", err)
	}
}

func TestParse_NonPositiveIntervalRejected(t *testing.T) {
	for _, bad := range []string{"0s", "-5m"} {
		_, err := parseDiscard([]string{
			"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
			"--interval", bad,
		}, emptyEnv)
		if err == nil {
			t.Errorf("--interval=%q: expected an error, got nil", bad)
		}
	}
}

func TestParse_ValidIntervalOverridesDefault(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
		"--interval", "5m",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ReportInterval != 5*time.Minute {
		t.Errorf("ReportInterval = %v, want 5m", cfg.ReportInterval)
	}
}

func TestParse_EnvVarsUsedWhenFlagsOmitted(t *testing.T) {
	env := envMap(map[string]string{
		EnvServerURL:   "http://100.64.0.9:9090",
		EnvAPIKey:      "env-key",
		EnvClientID:    "env-client",
		EnvLibraryRoot: "/custom/steam",
		EnvInterval:    "10m",
	})
	cfg, err := parseDiscard(nil, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.9:9090" {
		t.Errorf("ServerURL = %q", cfg.ServerURL)
	}
	if cfg.APIKey != "env-key" {
		t.Errorf("APIKey = %q", cfg.APIKey)
	}
	if cfg.ClientID != "env-client" {
		t.Errorf("ClientID = %q", cfg.ClientID)
	}
	if cfg.LibraryRoot != "/custom/steam" {
		t.Errorf("LibraryRoot = %q", cfg.LibraryRoot)
	}
	if cfg.ReportInterval != 10*time.Minute {
		t.Errorf("ReportInterval = %v", cfg.ReportInterval)
	}
}

func TestParse_FlagOverridesEnvVar(t *testing.T) {
	env := envMap(map[string]string{
		EnvServerURL: "http://from-env:1",
		EnvAPIKey:    "env-key",
		EnvClientID:  "env-client",
	})
	cfg, err := parseDiscard([]string{"--server-url", "http://from-flag:2"}, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://from-flag:2" {
		t.Errorf("ServerURL = %q, want the flag value to win over the env var", cfg.ServerURL)
	}
}

func TestParse_LoopFlag(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc", "--loop",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !cfg.Loop {
		t.Error("Loop = false, want true")
	}
}

func TestParse_AllowEmptyFlagDefaultsOff(t *testing.T) {
	base := []string{"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc"}
	cfg, err := parseDiscard(base, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.AllowEmpty {
		t.Error("AllowEmpty = true by default, want false (a wrong library root must not post an empty report silently)")
	}
	cfg, err = parseDiscard(append([]string{"--allow-empty"}, base...), emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !cfg.AllowEmpty {
		t.Error("AllowEmpty = false with --allow-empty given, want true")
	}
}

func TestParse_MultipleErrorsAreAllReported(t *testing.T) {
	_, err := parseDiscard([]string{"--client-id", ".."}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "server URL is required") {
		t.Errorf("missing server-url error not reported: %v", msg)
	}
	if !strings.Contains(msg, "API key is required") {
		t.Errorf("missing api-key error not reported: %v", msg)
	}
}

func TestConfig_RedactedNeverExposesAPIKey(t *testing.T) {
	cfg := Config{APIKey: "top-secret"}
	red := cfg.Redacted()
	if red.APIKey == "top-secret" || strings.Contains(red.APIKey, "top-secret") {
		t.Errorf("Redacted().APIKey = %q, still contains the real key", red.APIKey)
	}
}

// --- S2 (WP AGENT-FIX-1): Redacted() must also strip userinfo from the
// server URL - cmd/vault-agent logs it at every start. Cases: an "@"
// INSIDE the password (Go's parser splits the authority at the LAST "@",
// so this is a real, valid input), an IPv6 host (the bracketed host must
// survive intact), a plain URL that must come back byte-identical, and a
// path/query that must not be lost.

func TestConfig_RedactedStripsServerURLUserinfo(t *testing.T) {
	cases := []struct {
		name       string
		in         string
		want       string
		mustNotSee []string
	}{
		{
			name:       "password containing @",
			in:         "http://alice:p@ss:w0rd@100.64.0.5:8080",
			want:       "http://<redacted>@100.64.0.5:8080",
			mustNotSee: []string{"alice", "p@ss", "w0rd"},
		},
		{
			name:       "ipv6 host with userinfo",
			in:         "https://bob:hunter2@[fd00::1]:8443/api",
			want:       "https://<redacted>@[fd00::1]:8443/api",
			mustNotSee: []string{"bob", "hunter2"},
		},
		{
			name:       "username only",
			in:         "https://token@vault.example.internal",
			want:       "https://<redacted>@vault.example.internal",
			mustNotSee: []string{"token"},
		},
		{
			name: "no userinfo is byte-identical",
			in:   "http://127.0.0.1:8080",
			want: "http://127.0.0.1:8080",
		},
		{
			name: "no userinfo with path and query is byte-identical",
			in:   "https://vault.example/prefix?x=1",
			want: "https://vault.example/prefix?x=1",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Config{ServerURL: tc.in, APIKey: "k"}.Redacted().ServerURL
			if got != tc.want {
				t.Errorf("Redacted().ServerURL = %q, want %q", got, tc.want)
			}
			for _, secret := range tc.mustNotSee {
				if strings.Contains(got, secret) {
					t.Errorf("Redacted().ServerURL = %q still contains %q", got, secret)
				}
			}
		})
	}
}

func TestRedactURL_UnparseableIsNotEchoed(t *testing.T) {
	// A colon-in-port form url.Parse rejects outright; the raw value must
	// not be echoed since it could still carry a password.
	got := redactURL("http://u:SECRET@host:notaport")
	if strings.Contains(got, "SECRET") {
		t.Errorf("redactURL echoed an unparseable URL verbatim: %q", got)
	}
}

// The config-error log line is built from these messages - a rejected
// URL must not be quoted back with its password in it.
func TestParse_InvalidServerURLNeverEchoesUserinfo(t *testing.T) {
	for _, raw := range []string{
		"ftp://u:SECRET-SCHEME@host",     // scheme branch
		"http://u:SECRET-NOHOST@",        // no-host branch
		"http://u:SECRET-PARSE@host:xyz", // url.Parse failure branch
	} {
		_, err := parseDiscard([]string{"--server-url", raw, "--api-key", "k", "--client-id", "pc"}, emptyEnv)
		if err == nil {
			t.Fatalf("%q: expected a config error", raw)
		}
		if strings.Contains(err.Error(), "SECRET") {
			t.Errorf("%q: error %q echoes the password", raw, err.Error())
		}
	}
}

func TestDefaultClientID_SanitizesAndTruncates(t *testing.T) {
	longName := strings.Repeat("x", 100)
	got, note, err := defaultClientID(func() (string, error) { return longName, nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len([]rune(got)) != 64 {
		t.Errorf("len(got) = %d, want truncated to 64", len([]rune(got)))
	}
	if note == "" {
		t.Error("note = \"\", want a non-empty note when truncation changed the hostname")
	}
	if !strings.Contains(note, longName) || !strings.Contains(note, got) {
		t.Errorf("note = %q, want it to mention both the original hostname and the truncated id", note)
	}
}

func TestDefaultClientID_ReplacesControlCharacters(t *testing.T) {
	got, note, err := defaultClientID(func() (string, error) { return "pc\nname", nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.ContainsRune(got, '\n') {
		t.Errorf("got = %q, still contains a control character", got)
	}
	if note == "" {
		t.Error("note = \"\", want a non-empty note when rune replacement changed the hostname")
	}
}

func TestDefaultClientID_ReplacesInvisibleFormatCharacters(t *testing.T) {
	// S1: unicode.IsPrint (not just IsControl) is required to catch these -
	// a zero-width joiner is category Cf, not Cc.
	got, note, err := defaultClientID(func() (string, error) { return "pc‍name", nil }) // ZWJ
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.ContainsRune(got, '‍') {
		t.Errorf("got = %q, still contains a zero-width joiner", got)
	}
	if note == "" {
		t.Error("note = \"\", want a non-empty note when a ZWJ was replaced")
	}
}

func TestDefaultClientID_TrimsWhitespace(t *testing.T) {
	got, note, err := defaultClientID(func() (string, error) { return "  gaming-pc  ", nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "gaming-pc" {
		t.Errorf("got = %q, want trimmed", got)
	}
	// Plain surrounding whitespace is not the kind of change the note is
	// for (no rune was replaced, nothing was truncated) - comparing against
	// the ALREADY-TRIMMED hostname, not the raw one, is what keeps this
	// case quiet. See TestDefaultClientID_SanitizedNote_EmptyWhenHostnameUnchanged.
	if note != "" {
		t.Errorf("note = %q, want empty for a hostname that only needed trimming", note)
	}
}

func TestDefaultClientID_HostnameErrorIsPropagated(t *testing.T) {
	_, _, err := defaultClientID(func() (string, error) { return "", errHostname })
	if err == nil {
		t.Fatal("expected an error when os.Hostname fails")
	}
}

func TestDefaultClientID_EmptyAfterSanitizingIsAnError(t *testing.T) {
	_, _, err := defaultClientID(func() (string, error) { return "   ", nil })
	if err == nil {
		t.Fatal("expected an error for a hostname that sanitizes to empty")
	}
}

func TestDefaultClientID_SanitizedNote_EmptyWhenHostnameUnchanged(t *testing.T) {
	got, note, err := defaultClientID(func() (string, error) { return "gaming-pc", nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "gaming-pc" {
		t.Errorf("got = %q, want the hostname unchanged", got)
	}
	if note != "" {
		t.Errorf("note = %q, want empty when nothing about the hostname needed changing", note)
	}
}

// --- WP AG-0: client-id source attribution (flag vs env vs derived), and
// the sanitization-changed-it case surfacing through Parse()/build() as
// Config.ClientIDSource/Config.ClientIDNote - the path an actual operator
// observes via cmd/vault-agent's startup log line, mirroring the
// LibraryRootProbeNote tests above.

func TestParse_ClientIDSource_ExplicitFlag(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "gaming-pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientIDSource != ClientIDSourceFlag {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceFlag)
	}
	if !strings.Contains(cfg.ClientIDNote, "--client-id") {
		t.Errorf("ClientIDNote = %q, want it to mention --client-id", cfg.ClientIDNote)
	}
}

func TestParse_ClientIDSource_ExplicitEnv(t *testing.T) {
	env := envMap(map[string]string{EnvClientID: "env-client"})
	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientID != "env-client" {
		t.Fatalf("ClientID = %q, want the env value", cfg.ClientID)
	}
	if cfg.ClientIDSource != ClientIDSourceEnv {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceEnv)
	}
	if !strings.Contains(cfg.ClientIDNote, EnvClientID) {
		t.Errorf("ClientIDNote = %q, want it to mention %s", cfg.ClientIDNote, EnvClientID)
	}
}

func TestParse_ClientIDSource_ExplicitFlagWinsOverEnv(t *testing.T) {
	env := envMap(map[string]string{EnvClientID: "env-client"})
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "flag-client",
	}, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientID != "flag-client" {
		t.Errorf("ClientID = %q, want the flag value to win", cfg.ClientID)
	}
	if cfg.ClientIDSource != ClientIDSourceFlag {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceFlag)
	}
}

func TestParse_ClientIDSource_Derived(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "test-host-01", nil }
	defer func() { hostnameFunc = orig }()

	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientIDSource != ClientIDSourceDerived {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceDerived)
	}
	if !strings.Contains(cfg.ClientIDNote, "--client-id") || !strings.Contains(cfg.ClientIDNote, EnvClientID) {
		t.Errorf("ClientIDNote = %q, want it to explain how to override the derived id", cfg.ClientIDNote)
	}
	if strings.Contains(cfg.ClientIDNote, "sanitized") {
		t.Errorf("ClientIDNote = %q, want no sanitization mention for an already-clean hostname", cfg.ClientIDNote)
	}
}

// TestParse_ClientIDSource_DerivedFromPaddedHostnameIsNotFlaggedSanitized
// pins the "plain trim is not sanitizing" rule (see
// TestDefaultClientID_TrimsWhitespace's unit-level version) at the layer an
// operator actually observes it: Config.ClientIDNote via Parse(), not just
// defaultClientID() in isolation. N3 (review round 1): this is exactly the
// layer the WP AG-0 "changed-ness" comparison could regress at without a
// unit test noticing, e.g. if build() ever grew its own second comparison
// between the raw and derived values instead of using defaultClientID's
// note verbatim.
func TestParse_ClientIDSource_DerivedFromPaddedHostnameIsNotFlaggedSanitized(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "  padded  ", nil }
	defer func() { hostnameFunc = orig }()

	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientID != "padded" {
		t.Fatalf("ClientID = %q, want the trimmed hostname", cfg.ClientID)
	}
	if cfg.ClientIDSource != ClientIDSourceDerived {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceDerived)
	}
	if strings.Contains(cfg.ClientIDNote, "sanitized") {
		t.Errorf("ClientIDNote = %q, want no sanitization mention for a hostname that only needed trimming", cfg.ClientIDNote)
	}
}

func TestParse_ClientIDSource_DerivedAndSanitized(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "office\tpc", nil } // tab: non-printable, gets replaced
	defer func() { hostnameFunc = orig }()

	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientIDSource != ClientIDSourceDerived {
		t.Errorf("ClientIDSource = %q, want %q", cfg.ClientIDSource, ClientIDSourceDerived)
	}
	// %q-escapes the tab (as \t) rather than embedding a literal one - check
	// for the escaped form actually produced.
	if !strings.Contains(cfg.ClientIDNote, `office\tpc`) {
		t.Errorf("ClientIDNote = %q, want it to name the original hostname", cfg.ClientIDNote)
	}
	if !strings.Contains(cfg.ClientIDNote, cfg.ClientID) {
		t.Errorf("ClientIDNote = %q, want it to name the resulting client id %q", cfg.ClientIDNote, cfg.ClientID)
	}
	if !strings.Contains(cfg.ClientIDNote, "--client-id") || !strings.Contains(cfg.ClientIDNote, EnvClientID) {
		t.Errorf("ClientIDNote = %q, want it to still explain how to override", cfg.ClientIDNote)
	}
}

func TestParse_UsesDefaultClientIDWhenHostnameAvailable(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "test-host-01", nil }
	defer func() { hostnameFunc = orig }()

	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientID != "test-host-01" {
		t.Errorf("ClientID = %q, want the sanitized hostname", cfg.ClientID)
	}
}

func TestParse_UnusableHostnameFailsLoudlyWhenNoClientIDGiven(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "", errHostname }
	defer func() { hostnameFunc = orig }()

	_, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error when hostname derivation fails and no --client-id was given")
	}
	if !strings.Contains(err.Error(), "client-id") {
		t.Errorf("error = %v, want it to mention client-id", err)
	}
}

func TestDefaultLibraryRoot_PerOS(t *testing.T) {
	win, winNote := defaultLibraryRoot("windows")
	if !strings.Contains(win, "Steam") {
		t.Errorf("windows default = %q, want it to mention Steam", win)
	}
	if winNote != "" {
		t.Errorf("windows note = %q, want empty (Windows never probes)", winNote)
	}
	linux, _ := defaultLibraryRoot("linux") // note depends on real disk state here, not asserted
	if !strings.Contains(linux, ".local/share/Steam") {
		t.Errorf("linux default = %q, want the XDG Steam path", linux)
	}
}

func TestDefaultLibraryRoot_MatchesRuntimeGOOSConvention(t *testing.T) {
	// Sanity check that Parse's real default (using runtime.GOOS) doesn't
	// crash and produces a non-empty path on whatever OS the test runs on.
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want, _ := defaultLibraryRoot(runtime.GOOS)
	if cfg.LibraryRoot != want {
		t.Errorf("LibraryRoot = %q, want %q", cfg.LibraryRoot, want)
	}
}

// --- WP 2.5: Linux/SteamOS library-root probe order ---
//
// fakeExists below stubs the dirExists package var so these tests never
// touch the real filesystem or depend on what happens to be installed on
// whatever machine runs `go test` (real Steam install state on the WSL2
// dev/test box would otherwise make TestProbeLinuxLibraryRoot_* pass or
// fail depending on which candidate paths exist there right now).

// fakeExists returns a dirExists-shaped func backed by a fixed set of
// "existing" paths, plus the ordered list of paths it was actually asked
// about (so a test can assert probing stopped at the first hit instead of
// checking every candidate).
func fakeExists(existing ...string) (func(string) bool, *[]string) {
	set := map[string]bool{}
	for _, p := range existing {
		set[p] = true
	}
	var asked []string
	fn := func(p string) bool {
		asked = append(asked, p)
		return set[p]
	}
	return fn, &asked
}

func TestLinuxLibraryRootCandidates_OrderAndPaths(t *testing.T) {
	got := linuxLibraryRootCandidates("/home/deck")
	want := []string{
		"/home/deck/.local/share/Steam",
		"/home/deck/.steam/steam",
		"/home/deck/.var/app/com.valvesoftware.Steam/.local/share/Steam",
	}
	if len(got) != len(want) {
		t.Fatalf("got %d candidates, want %d: %v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("candidate[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestLinuxLibraryRootCandidates_EmptyHomeFallsBackToRelative(t *testing.T) {
	got := linuxLibraryRootCandidates("")
	// Byte-identical to the exact string the pre-WP-2.5 default returned
	// for this case (no "./" prefix) - not just an equivalent path. See
	// linuxLibraryRootCandidates' doc comment for why this distinction
	// matters (a review finding: an earlier draft produced
	// "./.local/share/Steam" here and claimed parity it didn't have).
	want := []string{
		".local/share/Steam",
		".steam/steam",
		".var/app/com.valvesoftware.Steam/.local/share/Steam",
	}
	if len(got) != len(want) {
		t.Fatalf("got %d candidates, want %d: %v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("candidate[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestProbeLinuxLibraryRoot_FirstExistsWins(t *testing.T) {
	candidates := []string{"/home/deck/.local/share/Steam", "/home/deck/.steam/steam", "/home/deck/flatpak/Steam"}
	// All three "exist" - the modern (first) candidate must still win, and
	// probing must stop there (never even ask about the later two).
	exists, asked := fakeExists(candidates...)

	got, err := probeLinuxLibraryRoot(candidates, exists)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != candidates[0] {
		t.Errorf("got %q, want the first candidate %q", got, candidates[0])
	}
	if len(*asked) != 1 || (*asked)[0] != candidates[0] {
		t.Errorf("asked = %v, want probing to stop at the first hit", *asked)
	}
}

func TestProbeLinuxLibraryRoot_SecondCandidateWinsWhenFirstMissing(t *testing.T) {
	candidates := []string{"/home/deck/.local/share/Steam", "/home/deck/.steam/steam", "/home/deck/flatpak/Steam"}
	// Only the legacy symlink location exists - modern location absent
	// (e.g. a manual/legacy install). Second candidate must win.
	exists, asked := fakeExists(candidates[1])

	got, err := probeLinuxLibraryRoot(candidates, exists)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != candidates[1] {
		t.Errorf("got %q, want the second candidate %q", got, candidates[1])
	}
	want := []string{candidates[0], candidates[1]}
	if len(*asked) != len(want) || (*asked)[0] != want[0] || (*asked)[1] != want[1] {
		t.Errorf("asked = %v, want %v (probing stops at the first hit)", *asked, want)
	}
}

func TestProbeLinuxLibraryRoot_ThirdCandidateWinsWhenFirstTwoMissing(t *testing.T) {
	candidates := []string{"/home/deck/.local/share/Steam", "/home/deck/.steam/steam", "/home/deck/flatpak/Steam"}
	// Only the Flatpak sandbox location exists.
	exists, _ := fakeExists(candidates[2])

	got, err := probeLinuxLibraryRoot(candidates, exists)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != candidates[2] {
		t.Errorf("got %q, want the third (Flatpak) candidate %q", got, candidates[2])
	}
}

func TestProbeLinuxLibraryRoot_NoneExistIsAClearError(t *testing.T) {
	candidates := []string{"/home/deck/.local/share/Steam", "/home/deck/.steam/steam", "/home/deck/flatpak/Steam"}
	exists, asked := fakeExists() // nothing exists

	_, err := probeLinuxLibraryRoot(candidates, exists)
	if err == nil {
		t.Fatal("expected an error when none of the candidates exist")
	}
	msg := err.Error()
	for _, c := range candidates {
		if !strings.Contains(msg, c) {
			t.Errorf("error %q does not name checked candidate %q", msg, c)
		}
	}
	if !strings.Contains(msg, "--library-root") || !strings.Contains(msg, EnvLibraryRoot) {
		t.Errorf("error %q does not point the operator at the escape hatch", msg)
	}
	if len(*asked) != len(candidates) {
		t.Errorf("asked = %v, want every candidate checked when none match", *asked)
	}
}

func TestDefaultLibraryRoot_Linux_PicksFirstExistingCandidateViaRealProbe(t *testing.T) {
	// Exercises defaultLibraryRoot's actual Linux branch (not just the
	// probeLinuxLibraryRoot helper in isolation), with the package-level
	// dirExists var stubbed so this stays deterministic regardless of
	// what's really installed on the machine running `go test`.
	orig := dirExists
	defer func() { dirExists = orig }()

	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		t.Skip("no $HOME on this test runner")
	}
	legacy := home + "/.steam/steam"
	dirExists = func(p string) bool { return p == legacy }

	got, note := defaultLibraryRoot("linux")
	if got != legacy {
		t.Errorf("defaultLibraryRoot(\"linux\") = %q, want the legacy candidate %q to win", got, legacy)
	}
	if note != "" {
		t.Errorf("note = %q, want empty when a candidate was confirmed to exist", note)
	}
}

func TestDefaultLibraryRoot_Linux_FallsBackToModernDefaultWhenNoneExist(t *testing.T) {
	orig := dirExists
	defer func() { dirExists = orig }()
	dirExists = func(string) bool { return false }

	home, _ := os.UserHomeDir()
	want := linuxLibraryRootCandidates(home)[0]

	got, note := defaultLibraryRoot("linux")
	if got != want {
		t.Errorf("defaultLibraryRoot(\"linux\") = %q, want the modern-default fallback %q", got, want)
	}
	// S2 (review): probeLinuxLibraryRoot's descriptive error used to be
	// built and immediately discarded here - reachable by nothing and
	// nobody. It must now come back out through defaultLibraryRoot's
	// second return value.
	if note == "" {
		t.Fatal("note = \"\", want a non-empty note when none of the candidates exist")
	}
	if !strings.Contains(note, want) {
		t.Errorf("note = %q, want it to mention the fallback path %q", note, want)
	}
}

func TestDefaultLibraryRoot_Windows_NeverProbesTheFilesystem(t *testing.T) {
	orig := dirExists
	defer func() { dirExists = orig }()
	dirExists = func(string) bool {
		t.Fatal("dirExists must not be called for the windows branch")
		return false
	}

	got, note := defaultLibraryRoot("windows")
	if !strings.Contains(got, "Steam") {
		t.Errorf("windows default = %q", got)
	}
	if note != "" {
		t.Errorf("note = %q, want empty on windows", note)
	}
}

// --- S2 (review): the fallback note must be reachable all the way through
// Parse()/build() as Config.LibraryRootProbeNote - that's the path an
// actual operator (via cmd/vault-agent's startup log line) observes it
// through, not the internal probeLinuxLibraryRoot helper directly.

func TestParse_LibraryRootProbeNote_SetWhenFallbackGuessUsed(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fallback-guess note only exists on the non-Windows branch")
	}
	orig := dirExists
	defer func() { dirExists = orig }()
	dirExists = func(string) bool { return false } // nothing exists -> fallback guess

	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.LibraryRootProbeNote == "" {
		t.Fatal("expected LibraryRootProbeNote to be set when the Linux probe fallback guess was used")
	}
	if !strings.Contains(cfg.LibraryRootProbeNote, cfg.LibraryRoot) {
		t.Errorf("note = %q, want it to mention the resulting LibraryRoot %q", cfg.LibraryRootProbeNote, cfg.LibraryRoot)
	}
}

func TestParse_LibraryRootProbeNote_EmptyWhenProbeFindsSomething(t *testing.T) {
	orig := dirExists
	defer func() { dirExists = orig }()
	dirExists = func(string) bool { return true } // first candidate "exists"

	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if runtime.GOOS != "windows" && cfg.LibraryRootProbeNote != "" {
		t.Errorf("note = %q, want empty when a probe candidate is confirmed to exist", cfg.LibraryRootProbeNote)
	}
}

func TestParse_LibraryRootProbeNote_EmptyWhenLibraryRootGivenExplicitly(t *testing.T) {
	orig := dirExists
	defer func() { dirExists = orig }()
	dirExists = func(string) bool { return false } // would produce a note if consulted at all

	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
		"--library-root", "/custom/steam/path",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.LibraryRoot != "/custom/steam/path" {
		t.Errorf("LibraryRoot = %q, want the explicit value", cfg.LibraryRoot)
	}
	if cfg.LibraryRootProbeNote != "" {
		t.Errorf("note = %q, want empty when --library-root is explicit (the probe must not even run)", cfg.LibraryRootProbeNote)
	}
}

// errHostname is a fixed sentinel error for tests that need os.Hostname
// to fail.
var errHostname = &staticError{"hostname lookup failed"}

type staticError struct{ msg string }

func (e *staticError) Error() string { return e.msg }
