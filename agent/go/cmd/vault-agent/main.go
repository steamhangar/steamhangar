// Command vault-agent is the production PC listener (WP 2.2, ADR-0002,
// ADR-0005): discover the local Steam library, report the full installed
// list to vault-api, exit. Deliberately dumb (plan §3) - no control logic
// lives here, only discovery + reporting.
//
// Usage:
//
//	vault-agent report                 one-shot: discover -> report -> print -> exit 0/1
//	vault-agent report --loop           keep running, reporting every --interval (jittered)
//	                                     until SIGTERM/CTRL-C
//	vault-agent hosts apply|remove|status
//	                                    opt-in, DNS-free hosts-file mode (WP 2.3) - see
//	                                     hosts.go and agent/go/hostsfile
//	vault-agent --version               print the build version and commit (WP VER-1)
//
// One-shot is the PRIMARY mode (plan §7: a Windows Scheduled Task provides
// the timing); --loop exists for systemd (Phase 2.5's Linux/SteamOS
// packaging) where the service itself stays resident.
//
// Every report carries this build's version and its report interval (WP
// AGENT-FEAT-1), so vault-api can show the machine online/offline. The
// shipped schedulers start a one-shot run at logon/boot and every 10
// minutes and pass that interval with --interval or
// VAULT_AGENT_REPORT_INTERVAL; a one-shot run without it states no
// interval (see report.Payload.WithPresence).
//
// Configuration is flags with an environment-variable fallback - see
// agent/go/agentconfig and agent/README.md's "Configuration" section.
// VAULT_AGENT_API_KEY is never logged in any code path here: every log
// line below is built from named fields, and the api key is never one of
// them - only handed to client.New, which itself never logs (see
// agent/go/client/client.go's package doc).
//
// Exit codes:
//
//	0  the report was sent and accepted (one-shot); or --loop exited
//	   cleanly on SIGTERM/CTRL-C; or -h/--help or --version was requested
//	1  a runtime failure: no readable Steam library under --library-root
//	   (refused without --allow-empty), local report validation failed,
//	   or the HTTP client gave up (network error, 401, 422, redirect,
//	   malformed response, ...)
//	2  a configuration/usage error (missing/invalid flag, no subcommand)
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/Riviera822/steamhangar/agent/acf"
	"github.com/Riviera822/steamhangar/agent/agentconfig"
	"github.com/Riviera822/steamhangar/agent/client"
	"github.com/Riviera822/steamhangar/agent/report"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// run contains all of main's logic, parameterized over args and output
// streams (io.Writer, not *os.File, specifically so a test can pass a
// bytes.Buffer and inspect exactly what would have been printed/logged -
// e.g. main_test.go's TestRun_APIKeyNeverAppearsInLoggedOutput, the
// redaction proof) instead of exec'ing a subprocess.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		printUsage(stderr)
		return 2
	}
	if isVersionFlag(args[0]) {
		printVersion(stdout)
		return 0
	}
	switch args[0] {
	case "report":
		return runReport(args, stdout, stderr)
	case "hosts":
		return runHosts(args[1:], stdout, stderr, programName())
	default:
		fmt.Fprintf(stderr, "unknown command %q\n\n", args[0])
		printUsage(stderr)
		return 2
	}
}

// programName is what the elevation hint tells the user to type. Taken
// from os.Args[0] so a renamed binary still prints a command that works.
func programName() string {
	if len(os.Args) == 0 || strings.TrimSpace(os.Args[0]) == "" {
		return "vault-agent"
	}
	return filepath.Base(os.Args[0])
}

func printUsage(w io.Writer) {
	fmt.Fprintln(w, "usage: vault-agent <command> [flags]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  report [--loop]              discover the local Steam library and report it to vault-api")
	fmt.Fprintln(w, "  hosts apply|remove|status    manage the optional hosts-file cache entry (opt-in, admin rights)")
	fmt.Fprintln(w, "  --version                    print the build version and commit")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "run 'vault-agent report -h' or 'vault-agent hosts' for the full flag list")
}

// runReport is the WP 2.2 `report` subcommand, unchanged by WP 2.3's
// addition of `hosts` beyond being lifted out of run()'s body. args
// INCLUDES the subcommand name at index 0.
func runReport(args []string, stdout, stderr io.Writer) int {
	logger := log.New(stderr, "", log.LstdFlags)

	// output goes to stderr (the SAME writer the caller passed in, not the
	// real os.Stderr) so -h/unknown-flag usage text is captured wherever
	// the rest of this process's logging goes (WP 2.2 review finding B1's
	// fs.SetOutput requirement).
	cfg, err := agentconfig.Parse("report", args[1:], os.Getenv, stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0 // flag package already printed usage to stderr
		}
		logger.Printf("config error=%q", err)
		return 2
	}

	redacted := cfg.Redacted()
	// client_id_source/client_id_note (WP AG-0) make the id's provenance
	// visible on the SAME line as the id itself - "derived-from-hostname"
	// plus a note telling the operator how to override it, or (when
	// sanitizing actually changed the hostname) what it became and why -
	// rather than a bare value that looks identical whether it was chosen
	// or silently inherited. Both fields are built entirely in
	// agentconfig.build()/defaultClientID(); nothing here re-derives them.
	logger.Printf("vault-agent starting version=%q server_url=%q client_id=%q client_id_source=%s client_id_note=%q library_root=%q loop=%v report_interval=%s report_interval_explicit=%v api_key=%s",
		version, redacted.ServerURL, redacted.ClientID, redacted.ClientIDSource, redacted.ClientIDNote,
		redacted.LibraryRoot, redacted.Loop, redacted.ReportInterval, redacted.ReportIntervalExplicit, redacted.APIKey)

	// WP 2.5 S2 (review): LibraryRootProbeNote is only ever non-empty when
	// LibraryRoot is an UNCONFIRMED Linux fallback guess (none of the
	// probed install locations exist) - logged once here, at startup,
	// exactly once per run, so that guess is not a silent one. Never
	// contains anything secret (see Config.LibraryRootProbeNote's doc
	// comment), so no redaction is needed.
	if cfg.LibraryRootProbeNote != "" {
		logger.Printf("library root note=%q", cfg.LibraryRootProbeNote)
	}

	httpClient := client.New(cfg.ServerURL, cfg.APIKey)

	if !cfg.Loop {
		if reportOnce(context.Background(), logger, stdout, cfg, httpClient) {
			return 0
		}
		return 1
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	runLoop(ctx, logger, stdout, cfg, httpClient)
	return 0
}

// reportOnce discovers + reports exactly once and prints a human-readable
// result line to stdout. Returns true on success.
func reportOnce(ctx context.Context, logger *log.Logger, stdout io.Writer, cfg agentconfig.Config, c *client.Client) bool {
	discovered := acf.Discover(cfg.LibraryRoot)
	for _, w := range discovered.Warnings {
		logger.Printf("discover warning=%q", w.Message)
	}

	// WP AGENT-FIX-1 S1: zero READABLE libraries means Steam was not found
	// under library_root (wrong --library-root, Steam moved, drive not
	// mounted) - not that nothing is installed. Posting {"appids": []}
	// here would be a legitimate-looking snapshot that makes vault-api
	// diff every previously reported game as REMOVED and drop this client
	// from the prefill set. So: refuse, name what was probed, exit
	// non-zero. --allow-empty keeps the ADR-0002 empty-report path
	// available as an explicit choice. A readable library with no
	// manifests (Steam installed, nothing installed in it) is NOT this
	// case - LibrariesRead >= 1 and the empty report is posted as before.
	if discovered.LibrariesRead == 0 && !cfg.AllowEmpty {
		logger.Printf("report refused error=%q probed=%q hint=%q",
			"no readable Steam library under library_root - refusing to post an empty installed list",
			strings.Join(discovered.LibrariesProbed, ", "),
			"check --library-root / "+agentconfig.EnvLibraryRoot+" (the directory containing steamapps/), "+
				"or pass --allow-empty to post an empty report anyway")
		return false
	}

	payload, err := report.BuildReport(discovered.Apps, cfg.ClientID)
	if err != nil {
		logger.Printf("report build failed error=%q", err)
		return false
	}
	// WP AGENT-FEAT-1: version and interval let vault-api show this
	// machine online/offline. --loop times itself, so its interval is
	// always a fact; a one-shot run states one only when it was told
	// (see report.Payload.WithPresence).
	payload, notes := payload.WithPresence(version, cfg.ReportInterval, cfg.Loop || cfg.ReportIntervalExplicit)
	for _, note := range notes {
		logger.Printf("report presence note=%q", note)
	}
	logger.Printf("report built installed_count=%d client_id=%q agent_version=%s report_interval_seconds=%s",
		len(payload.AppIDs), payload.ClientID, optString(payload.AgentVersion), optInt(payload.ReportIntervalSeconds))

	// A single HTTP attempt (with client.Client's own internal retries) is
	// bounded generously - this is a small JSON POST, not a download; 2
	// minutes covers even a client.Client configured with a larger-than-
	// default retry budget on a flaky link without hanging the scheduled
	// task indefinitely.
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()

	result, err := c.ReportInstalled(reqCtx, payload)
	if err != nil {
		logger.Printf("report send failed error=%q", err)
		return false
	}

	if result.PresenceDropped {
		logger.Printf("report presence note=%q", "the server does not accept agent_version/report_interval_seconds "+
			"yet (vault-api older than AGENT-FEAT-1); the report was resent without them and accepted - "+
			"upgrade vault-api to see this machine's version and online state")
	}
	fmt.Fprintf(stdout, "reported %d installed app(s) for client_id=%s: added=%v removed=%v first_report=%v\n",
		result.Received, result.ClientID, result.Added, result.Removed, result.FirstReport)
	logger.Printf("report accepted received=%d added=%d removed=%d first_report=%v",
		result.Received, len(result.Added), len(result.Removed), result.FirstReport)
	return true
}

// optString and optInt render an optional payload field for a log line:
// the quoted value, or "-" when it is not sent.
func optString(v *string) string {
	if v == nil {
		return "-"
	}
	return fmt.Sprintf("%q", *v)
}

func optInt(v *int) string {
	if v == nil {
		return "-"
	}
	return fmt.Sprintf("%d", *v)
}

// runLoop reports on cfg.ReportInterval (+/- jitter) until ctx is
// canceled (main wires ctx from signal.NotifyContext for SIGTERM/CTRL-C;
// taking ctx as a parameter rather than constructing it here also lets a
// test drive runLoop directly with an ordinary cancelable context instead
// of sending the test process a real OS signal). Report failures are
// logged but never stop the loop - the whole point of --loop is to keep
// trying across a VPN/network outage (plan §7) rather than give up after
// one bad interval.
func runLoop(ctx context.Context, logger *log.Logger, stdout io.Writer, cfg agentconfig.Config, c *client.Client) {
	rng := rand.New(rand.NewSource(time.Now().UnixNano()))
	logger.Printf("loop mode started interval=%s", cfg.ReportInterval)

	for {
		reportOnce(ctx, logger, stdout, cfg, c)

		// Checked BEFORE logging "sleeping until next report": if shutdown
		// arrived while reportOnce was running, this exits right away -
		// otherwise the log would print a "sleeping for Xm" line and then
		// immediately exit without ever having slept, which misrepresents
		// what actually happened (WP 2.2 review nitpick).
		select {
		case <-ctx.Done():
			logger.Printf("shutdown signal received, exiting cleanly")
			return
		default:
		}

		delay := jitteredInterval(cfg.ReportInterval, rng)
		logger.Printf("sleeping until next report in=%s", delay)

		// time.NewTimer + Stop rather than time.After: a time.After
		// channel is not collected until it fires, so a shutdown early in
		// a long (10 min) sleep would leave the timer pending for the
		// rest of the interval - harmless in practice, but the same
		// pattern client.go's backoff wait already uses (WP AGENT-FIX-1
		// N4).
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			logger.Printf("shutdown signal received, exiting cleanly")
			return
		case <-timer.C:
		}
	}
}

// jitteredInterval returns interval +/- up to 10%, so many agents on the
// same network configured with the same interval don't all report in
// lockstep against the same vault-api instance.
func jitteredInterval(interval time.Duration, rng *rand.Rand) time.Duration {
	if interval <= 0 {
		return interval
	}
	const jitterFraction = 0.10
	spread := float64(interval) * jitterFraction
	offset := (rng.Float64()*2 - 1) * spread // in [-spread, +spread]
	result := time.Duration(float64(interval) + offset)
	if result < 0 {
		result = 0
	}
	return result
}
