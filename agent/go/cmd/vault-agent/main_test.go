package main

import (
	"bytes"
	"context"
	"io"
	"log"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Riviera822/steamhangar/agent/agentconfig"
	"github.com/Riviera822/steamhangar/agent/client"
)

// emptyLibraryRoot returns a Steam library root that EXISTS and is
// READABLE but has nothing installed: <tmp>/steamapps/ with no manifests
// and no libraryfolders.vdf. Since WP AGENT-FIX-1 S1 that is the only
// shape of "empty" that `report` still posts by default - a bare
// directory with no steamapps/ at all now reads as "Steam not found here"
// and is refused (see TestRun_OneShot_ZeroReadableLibrariesRefusesToPost).
func emptyLibraryRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "steamapps"), 0o755); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestRun_NoSubcommandIsUsageError(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run(nil, &stdout, &stderr)
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr.String(), "usage:") {
		t.Errorf("stderr = %q, want a usage message", stderr.String())
	}
}

func TestRun_UnknownSubcommandIsUsageError(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"bogus"}, &stdout, &stderr)
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
}

func TestRun_MissingRequiredConfigIsUsageError(t *testing.T) {
	var stdout, stderr bytes.Buffer
	// No --server-url, no --api-key: agentconfig.Parse must fail loudly.
	code := run([]string{"report", "--client-id", "pc"}, &stdout, &stderr)
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr.String(), "config error") {
		t.Errorf("stderr = %q, want it to mention a config error", stderr.String())
	}
}

func TestRun_HelpFlagExitsZero(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"report", "-h"}, &stdout, &stderr)
	if code != 0 {
		t.Errorf("exit code = %d, want 0 for -h", code)
	}
}

// TestRun_HelpNeverLeaksAPIKeyFromEnv is the end-to-end version of B1's
// fix at the level real users invoke: agentconfig.Parse is called here
// via run()'s os.Getenv, not injected directly, so this proves the whole
// wiring (not just the agentconfig package in isolation) never echoes
// VAULT_AGENT_API_KEY into -h's usage text.
func TestRun_HelpNeverLeaksAPIKeyFromEnv(t *testing.T) {
	t.Setenv("VAULT_AGENT_API_KEY", "CANARY-MAIN-HELP-LEAK")
	var stdout, stderr bytes.Buffer
	code := run([]string{"report", "-h"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0 for -h", code)
	}
	if strings.Contains(stdout.String(), "CANARY-MAIN-HELP-LEAK") || strings.Contains(stderr.String(), "CANARY-MAIN-HELP-LEAK") {
		t.Fatalf("API key leaked: stdout=%q stderr=%q", stdout.String(), stderr.String())
	}
}

// TestRun_UnknownFlagNeverLeaksAPIKeyFromEnv is the same end-to-end proof
// for the OTHER trigger flag.Usage() prints on: an unrecognized flag,
// which fires regardless of whether --api-key/env was even the flag in
// question.
func TestRun_UnknownFlagNeverLeaksAPIKeyFromEnv(t *testing.T) {
	t.Setenv("VAULT_AGENT_API_KEY", "CANARY-MAIN-UNKNOWNFLAG-LEAK")
	var stdout, stderr bytes.Buffer
	code := run([]string{"report", "--this-flag-does-not-exist"}, &stdout, &stderr)
	if code != 2 {
		t.Fatalf("exit code = %d, want 2 for an unrecognized flag", code)
	}
	if strings.Contains(stdout.String(), "CANARY-MAIN-UNKNOWNFLAG-LEAK") || strings.Contains(stderr.String(), "CANARY-MAIN-UNKNOWNFLAG-LEAK") {
		t.Fatalf("API key leaked: stdout=%q stderr=%q", stdout.String(), stderr.String())
	}
}

func TestRun_OneShotSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	libraryRoot := emptyLibraryRoot(t) // readable steamapps/, nothing installed: a legitimate empty report

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", "test-key",
		"--client-id", "test-pc",
		"--library-root", libraryRoot,
	}, &stdout, &stderr)

	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr.String())
	}
	if !strings.Contains(stdout.String(), "reported") {
		t.Errorf("stdout = %q, want it to mention the report result", stdout.String())
	}
}

// --- WP AGENT-FIX-1 S1: a library root with no readable steamapps/ is
// "Steam not found", not "nothing installed" - and must NOT be posted as
// an empty snapshot (which would make vault-api drop every one of this
// client's games from the prefill set and log "REMOVED N apps").

func TestRun_OneShot_ZeroReadableLibrariesRefusesToPost(t *testing.T) {
	var requests int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requests, 1)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	wrongRoot := t.TempDir() // exists, but has no steamapps/ - a typical wrong --library-root

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", "test-key",
		"--client-id", "test-pc",
		"--library-root", wrongRoot,
	}, &stdout, &stderr)

	if code != 1 {
		t.Fatalf("exit code = %d, want 1. stderr=%s", code, stderr.String())
	}
	if got := atomic.LoadInt32(&requests); got != 0 {
		t.Fatalf("server received %d request(s), want 0 - nothing may be posted when no library was readable", got)
	}
	if strings.Contains(stdout.String(), "reported") {
		t.Errorf("stdout = %q, must not claim a report was made", stdout.String())
	}
	// The refusal line logs probed=%q, so on Windows the backslashes of
	// t.TempDir() appear doubled - assert the quoted form, not the raw path.
	for _, want := range []string{"report refused", "probed=" + strconv.Quote(wrongRoot), "--library-root", agentconfig.EnvLibraryRoot, "--allow-empty"} {
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("stderr = %q, want it to contain %q", stderr.String(), want)
		}
	}
}

func TestRun_OneShot_AllowEmptyPostsDespiteZeroReadableLibraries(t *testing.T) {
	var requests int32
	var body []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requests, 1)
		body, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--allow-empty",
		"--server-url", srv.URL,
		"--api-key", "test-key",
		"--client-id", "test-pc",
		"--library-root", t.TempDir(), // no steamapps/ - explicitly allowed this time
	}, &stdout, &stderr)

	if code != 0 {
		t.Fatalf("exit code = %d, want 0 with --allow-empty. stderr=%s", code, stderr.String())
	}
	if got := atomic.LoadInt32(&requests); got != 1 {
		t.Fatalf("server received %d request(s), want exactly 1", got)
	}
	if !strings.Contains(string(body), `"appids":[]`) {
		t.Errorf("posted body = %s, want an empty appids list", body)
	}
	if strings.Contains(stderr.String(), "report refused") {
		t.Errorf("stderr = %q, must not log a refusal when --allow-empty is given", stderr.String())
	}
}

// --- WP AGENT-FIX-1 S2: the startup log line prints server_url; a URL
// carrying userinfo (reverse-proxy basic auth) must have it redacted there.
func TestRun_StartupLogNeverShowsServerURLUserinfo(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	// http://alice:URL-PASSWORD-CANARY@127.0.0.1:port
	withUserinfo := strings.Replace(srv.URL, "http://", "http://alice:URL-PASSWORD-CANARY@", 1)

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", withUserinfo,
		"--api-key", "test-key",
		"--client-id", "test-pc",
		"--library-root", emptyLibraryRoot(t),
	}, &stdout, &stderr)

	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr.String())
	}
	if strings.Contains(stderr.String(), "URL-PASSWORD-CANARY") || strings.Contains(stderr.String(), "alice") {
		t.Fatalf("server URL userinfo leaked into the log: %s", stderr.String())
	}
	if !strings.Contains(stderr.String(), "server_url=\"http://<redacted>@") {
		t.Errorf("stderr = %q, want the startup line to show the redacted userinfo placeholder", stderr.String())
	}
}

func TestRun_OneShotFailureOn401(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"bad key"}`))
	}))
	defer srv.Close()

	libraryRoot := emptyLibraryRoot(t)

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", "wrong-key",
		"--client-id", "test-pc",
		"--library-root", libraryRoot,
	}, &stdout, &stderr)

	if code != 1 {
		t.Errorf("exit code = %d, want 1 for a rejected report", code)
	}
}

// TestRun_APIKeyNeverAppearsInLoggedOutput is the redaction proof the WP
// 2.2 brief asks for explicitly ("api key redacted everywhere ... test
// it"): run a full one-shot report with a distinctive API key and assert
// that exact value never appears anywhere in stdout or stderr, in any
// code path (startup log line, request, response handling).
func TestRun_APIKeyNeverAppearsInLoggedOutput(t *testing.T) {
	const secretAPIKey = "MY-VERY-SECRET-REDACTION-CANARY-VALUE"

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Api-Key") != secretAPIKey {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"detail":"bad key"}`))
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	libraryRoot := emptyLibraryRoot(t)

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", secretAPIKey,
		"--client-id", "test-pc",
		"--library-root", libraryRoot,
	}, &stdout, &stderr)

	if code != 0 {
		t.Fatalf("exit code = %d, want 0 (the server DOES receive the real key over the wire - only logs must never show it). stderr=%s", code, stderr.String())
	}
	if strings.Contains(stdout.String(), secretAPIKey) {
		t.Fatalf("API key leaked into stdout: %s", stdout.String())
	}
	if strings.Contains(stderr.String(), secretAPIKey) {
		t.Fatalf("API key leaked into stderr (log output): %s", stderr.String())
	}
	if !strings.Contains(stderr.String(), "<redacted>") {
		t.Errorf("stderr = %q, want the startup log line to show the redaction placeholder", stderr.String())
	}
}

func TestRun_ConfigErrorDoesNotLeakAPIKey(t *testing.T) {
	const secretAPIKey = "ANOTHER-SECRET-CANARY"
	var stdout, stderr bytes.Buffer
	// Missing --server-url -> config error path, before any HTTP call.
	code := run([]string{"report", "--api-key", secretAPIKey, "--client-id", ".."}, &stdout, &stderr)
	if code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	if strings.Contains(stderr.String(), secretAPIKey) {
		t.Fatalf("API key leaked into the config-error log line: %s", stderr.String())
	}
}

// --- WP AG-0: the startup log line must show client_id's PROVENANCE, not
// just its value - an operator reading the log needs to see whether the
// id was their explicit choice or a silently-inherited hostname default.

func TestRun_StartupLogShowsExplicitClientIDSource(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"test-pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", "test-key",
		"--client-id", "test-pc",
		"--library-root", emptyLibraryRoot(t),
	}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr.String())
	}
	if !strings.Contains(stderr.String(), "client_id_source=flag") {
		t.Errorf("stderr = %q, want the startup line to show client_id_source=flag", stderr.String())
	}
	if !strings.Contains(stderr.String(), "--client-id") {
		t.Errorf("stderr = %q, want the startup line to name --client-id as the explicit source", stderr.String())
	}
}

// TestRun_StartupLogShowsDerivedClientIDAndOverrideHint is the case the
// whole WP exists for: no --client-id/VAULT_AGENT_CLIENT_ID given at all,
// so vault-agent falls back to the sanitized local hostname. The log line
// must say so AND say how to choose a different one - this is exactly the
// invisibility the brief calls out ("nothing at install time or run time
// tells the user what name this machine will report under").
func TestRun_StartupLogShowsDerivedClientIDAndOverrideHint(t *testing.T) {
	// Force the env var empty regardless of what the host running this test
	// happens to have set - t.Setenv restores the prior value on cleanup.
	t.Setenv(agentconfig.EnvClientID, "")

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"whatever","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	code := run([]string{
		"report",
		"--server-url", srv.URL,
		"--api-key", "test-key",
		// no --client-id, no VAULT_AGENT_CLIENT_ID set: forces the
		// hostname-derived path this test targets.
		"--library-root", emptyLibraryRoot(t),
	}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr.String())
	}
	if !strings.Contains(stderr.String(), "client_id_source=derived-from-hostname") {
		t.Errorf("stderr = %q, want the startup line to show client_id_source=derived-from-hostname", stderr.String())
	}
	if !strings.Contains(stderr.String(), "--client-id") || !strings.Contains(stderr.String(), agentconfig.EnvClientID) {
		t.Errorf("stderr = %q, want the startup line to say how to override the derived id", stderr.String())
	}
}

func TestJitteredInterval_StaysWithinTenPercent(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	interval := 30 * time.Minute
	lower := interval - interval/10
	upper := interval + interval/10
	for i := 0; i < 200; i++ {
		got := jitteredInterval(interval, rng)
		if got < lower || got > upper {
			t.Fatalf("jitteredInterval() = %v, want within [%v, %v]", got, lower, upper)
		}
	}
}

func TestJitteredInterval_ZeroStaysZero(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	if got := jitteredInterval(0, rng); got != 0 {
		t.Errorf("jitteredInterval(0) = %v, want 0", got)
	}
}

// TestRunLoop_ContinuesAfterFailureAndExitsCleanlyOnCancel pins the two
// loop-mode behaviors the WP 2.2 review asked for a dedicated test on:
// (1) a failed report does NOT stop the loop - plan §7 "tolerate VPN/
// network outages" means staying up through a bad interval; (2) canceling
// ctx (main wires this from SIGTERM/CTRL-C via signal.NotifyContext) makes
// runLoop return promptly and log the clean-shutdown line.
//
// Driving runLoop directly with an ordinary cancelable context (rather
// than sending the test process a real OS SIGTERM) is deliberate: it is
// deterministic, portable, and doesn't risk the test binary's own
// process-wide signal handling - see runLoop's doc comment.
func TestRunLoop_ContinuesAfterFailureAndExitsCleanlyOnCancel(t *testing.T) {
	var requestCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&requestCount, 1)
		if n == 1 {
			// First report fails - the loop must not give up because of it.
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"detail":"temporary"}`))
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	defer srv.Close()

	cfg := agentconfig.Config{
		ServerURL:      srv.URL,
		APIKey:         "k",
		ClientID:       "pc",
		LibraryRoot:    emptyLibraryRoot(t), // readable, nothing installed: a legitimate empty report
		ReportInterval: 10 * time.Millisecond,
	}
	// MaxRetries(0): each reportOnce attempt fails/succeeds immediately -
	// the LOOP's own repetition (not the client's internal retry) is what
	// this test proves recovers from the first failure.
	httpClient := client.New(cfg.ServerURL, cfg.APIKey, client.WithMaxRetries(0))

	var stdout, stderr bytes.Buffer
	logger := log.New(&stderr, "", 0)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		runLoop(ctx, logger, &stdout, cfg, httpClient)
		close(done)
	}()

	// Let the loop run through the first (failing) report and at least
	// one subsequent (succeeding) one before asking it to stop.
	time.Sleep(80 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("runLoop did not return within 2s of ctx being canceled")
	}

	if got := atomic.LoadInt32(&requestCount); got < 2 {
		t.Fatalf("server received %d request(s), want at least 2 (a failure followed by the loop continuing)", got)
	}
	if !strings.Contains(stdout.String(), "reported") {
		t.Errorf("stdout = %q, want at least one successful report despite the first failure", stdout.String())
	}
	if !strings.Contains(stderr.String(), "shutdown signal received, exiting cleanly") {
		t.Errorf("stderr = %q, want the clean-shutdown log line", stderr.String())
	}
}
