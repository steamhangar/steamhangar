package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/Riviera822/steamhangar/agent/agentconfig"
	"github.com/Riviera822/steamhangar/agent/client"
)

// WP AGENT-FEAT-1: what `vault-agent report` actually puts on the wire.

type capturedBodies struct {
	mu     sync.Mutex
	bodies []map[string]any
}

func (c *capturedBodies) all() []map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]map[string]any(nil), c.bodies...)
}

// presenceServer records every decoded request body. With legacy=true it
// answers like vault-api v0.1.0-rc8 (extra="forbid": a 422 for any key
// besides client_id/appids); otherwise it accepts everything.
func presenceServer(t *testing.T, legacy bool) (*httptest.Server, *capturedBodies) {
	t.Helper()
	captured := &capturedBodies{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		captured.mu.Lock()
		captured.bodies = append(captured.bodies, body)
		captured.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if legacy {
			for k := range body {
				if k != "client_id" && k != "appids" {
					w.WriteHeader(http.StatusUnprocessableEntity)
					_, _ = w.Write([]byte(`{"detail":[{"type":"extra_forbidden","loc":["body","` + k + `"],"msg":"Extra inputs are not permitted","input":null}]}`))
					return
				}
			}
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"client_id":"pc","received":0,"added":[],"removed":[],"first_report":true}`))
	}))
	t.Cleanup(srv.Close)
	return srv, captured
}

func runReportArgs(srvURL, libraryRoot string, extra ...string) []string {
	return append([]string{
		"report", "--server-url", srvURL, "--api-key", "k", "--client-id", "pc", "--library-root", libraryRoot,
	}, extra...)
}

func TestRun_OneShotSendsVersionAndExplicitInterval(t *testing.T) {
	srv, captured := presenceServer(t, false)
	var stdout, stderr bytes.Buffer
	code := run(runReportArgs(srv.URL, emptyLibraryRoot(t), "--interval", "10m"), &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit code = %d, stderr=%s", code, stderr.String())
	}
	bodies := captured.all()
	if len(bodies) != 1 {
		t.Fatalf("server saw %d request(s), want 1", len(bodies))
	}
	if got := bodies[0]["agent_version"]; got != version {
		t.Errorf("agent_version = %v, want the build version %q", got, version)
	}
	if got := bodies[0]["report_interval_seconds"]; got != float64(600) {
		t.Errorf("report_interval_seconds = %v, want 600", got)
	}
}

// The env var is how the Windows task passes the interval (install-task.ps1
// writes VAULT_AGENT_REPORT_INTERVAL into the env file).
func TestRun_OneShotTakesTheIntervalFromTheEnv(t *testing.T) {
	t.Setenv(agentconfig.EnvInterval, "15m")
	srv, captured := presenceServer(t, false)
	var stdout, stderr bytes.Buffer
	if code := run(runReportArgs(srv.URL, emptyLibraryRoot(t)), &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d, stderr=%s", code, stderr.String())
	}
	if got := captured.all()[0]["report_interval_seconds"]; got != float64(900) {
		t.Errorf("report_interval_seconds = %v, want 900 from %s=15m", got, agentconfig.EnvInterval)
	}
}

// An install from before AGENT-FEAT-1 runs the new binary every 30 minutes
// without telling it so: the report must not claim the 10m default.
func TestRun_OneShotWithoutIntervalStatesNone(t *testing.T) {
	t.Setenv(agentconfig.EnvInterval, "")
	srv, captured := presenceServer(t, false)
	var stdout, stderr bytes.Buffer
	if code := run(runReportArgs(srv.URL, emptyLibraryRoot(t)), &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d, stderr=%s", code, stderr.String())
	}
	body := captured.all()[0]
	if _, ok := body["report_interval_seconds"]; ok {
		t.Errorf("report_interval_seconds sent (%v) by a one-shot run that was never told its interval", body["report_interval_seconds"])
	}
	if body["agent_version"] != version {
		t.Errorf("agent_version = %v, want %q", body["agent_version"], version)
	}
	if !strings.Contains(stderr.String(), "report presence note=") {
		t.Errorf("stderr does not explain the missing interval: %s", stderr.String())
	}
}

// --loop times itself, so its (default) interval is a fact and is sent.
func TestReportOnce_LoopModeStatesItsDefaultInterval(t *testing.T) {
	srv, captured := presenceServer(t, false)
	cfg := agentconfig.Config{
		ServerURL: srv.URL, APIKey: "k", ClientID: "pc", LibraryRoot: emptyLibraryRoot(t),
		ReportInterval: agentconfig.DefaultReportInterval, Loop: true,
	}
	var stdout, stderr bytes.Buffer
	ok := reportOnce(context.Background(), log.New(&stderr, "", 0), &stdout, cfg, client.New(cfg.ServerURL, cfg.APIKey))
	if !ok {
		t.Fatalf("reportOnce failed: %s", stderr.String())
	}
	if got := captured.all()[0]["report_interval_seconds"]; got != float64(600) {
		t.Errorf("report_interval_seconds = %v, want 600 (the 10m default, loop mode)", got)
	}
}

// A new agent against a vault-api that predates the fields still reports
// successfully, and says why the server shows no version for it.
func TestRun_OneShotAgainstAPreAgentFeat1ServerStillSucceeds(t *testing.T) {
	srv, captured := presenceServer(t, true)
	var stdout, stderr bytes.Buffer
	code := run(runReportArgs(srv.URL, emptyLibraryRoot(t), "--interval", "10m"), &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0 against an old server; stderr=%s", code, stderr.String())
	}
	bodies := captured.all()
	if len(bodies) != 2 {
		t.Fatalf("server saw %d request(s), want 2", len(bodies))
	}
	if _, ok := bodies[1]["agent_version"]; ok {
		t.Errorf("resent body still carries agent_version: %v", bodies[1])
	}
	if !strings.Contains(stderr.String(), "vault-api older than AGENT-FEAT-1") {
		t.Errorf("stderr does not tell the operator the server is too old: %s", stderr.String())
	}
	if !strings.Contains(stdout.String(), "reported 0 installed app(s)") {
		t.Errorf("stdout = %q, want the normal result line", stdout.String())
	}
}

func TestRun_StartupLogNamesVersionAndIntervalProvenance(t *testing.T) {
	srv, _ := presenceServer(t, false)
	var stdout, stderr bytes.Buffer
	if code := run(runReportArgs(srv.URL, emptyLibraryRoot(t), "--interval", "10m"), &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	for _, want := range []string{`version="` + version + `"`, "report_interval=10m0s", "report_interval_explicit=true"} {
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("startup log lacks %q: %s", want, stderr.String())
		}
	}
}
