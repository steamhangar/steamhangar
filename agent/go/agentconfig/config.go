// Package agentconfig parses vault-agent's configuration: flags with an
// environment-variable fallback (WP 2.2 brief: "config: one URL + API
// key", pick the simplest option that satisfies it).
//
// No config FILE format is implemented. Two reasons this is the simpler
// choice, not just the lazier one:
//   - A file that must hold VAULT_AGENT_API_KEY on disk needs its own
//     permission story and its own .gitignore-style "never commit this"
//     warning; an environment variable set by whatever launches the agent
//     (a Windows Scheduled Task's own "Run" field, a systemd unit's
//     Environment=/EnvironmentFile=, a shell wrapper) already has that
//     story solved by the launcher, not by this package.
//   - Passing the key via --api-key on the command line would leak it into
//     any process listing (`ps`, Task Manager's command-line column); an
//     env var does not.
//
// Every setting can still be set via CLI flag (convenient for a one-off
// manual `report` run) OR environment variable (the recommended way to
// supply VAULT_AGENT_API_KEY for a scheduled/service invocation) - a flag,
// if given, wins over its env var.
package agentconfig

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"runtime"
	"strings"
	"time"
	"unicode"

	"github.com/Riviera822/steamhangar/agent/report"
)

// Default report interval for --loop mode (plan §3: "periodically, e.g.
// every 30 min").
const DefaultReportInterval = 30 * time.Minute

// Env var names, also used as the flag-default source.
const (
	EnvServerURL   = "VAULT_AGENT_SERVER_URL"
	EnvAPIKey      = "VAULT_AGENT_API_KEY"
	EnvClientID    = "VAULT_AGENT_CLIENT_ID"
	EnvLibraryRoot = "VAULT_AGENT_LIBRARY_ROOT"
	EnvInterval    = "VAULT_AGENT_REPORT_INTERVAL"
)

// Config.ClientIDSource values (WP AG-0: the resolved client id's
// provenance must be visible, not just its value - see Config.ClientIDSource
// and Config.ClientIDNote's doc comments).
const (
	ClientIDSourceFlag    = "flag"                  // --client-id was given
	ClientIDSourceEnv     = "env"                   // VAULT_AGENT_CLIENT_ID was set, --client-id was not
	ClientIDSourceDerived = "derived-from-hostname" // neither was given; sanitized os.Hostname() was used
)

// Config is vault-agent's fully validated, ready-to-use configuration.
type Config struct {
	ServerURL string // e.g. "http://100.x.y.z:8080", no trailing slash
	APIKey    string // NEVER logged - see Redacted() and cmd/vault-agent's logger
	ClientID  string

	// ClientIDSource is one of the ClientIDSource* constants above: WHERE
	// ClientID came from. WP AG-0: the mechanism that picks a client id
	// (an explicit flag/env value, or a sanitized-hostname fallback) was
	// already sound, but invisible - nothing told an operator reading the
	// log which of the two happened, or that a different name could have
	// been chosen. cmd/vault-agent logs this alongside ClientID itself at
	// startup.
	ClientIDSource string

	// ClientIDNote is a short, human-readable, ALWAYS non-empty (once a
	// Config is successfully built) description of ClientIDSource's
	// reasoning, meant to be logged on the SAME line as ClientID:
	//   - flag/env sources: which flag/env var was the explicit choice.
	//   - derived source: that it came from this machine's hostname, PLUS
	//     how to override it (--client-id or VAULT_AGENT_CLIENT_ID) -
	//     since a hostname-derived id is exactly the case where the
	//     operator likely didn't know they had a choice.
	//   - derived source, when defaultClientID's sanitizing actually
	//     changed the hostname (non-printable runes replaced, or
	//     truncated to report.MaxClientIDLength): names the original
	//     hostname and what it became, so e.g. a machine named "Joerg-PC"
	//     with an unusual character sees the substitution instead of
	//     silently getting a different id than its own hostname.
	// Never contains anything secret - built entirely from the hostname
	// and fixed strings, never from APIKey.
	ClientIDNote string

	LibraryRoot string
	// LibraryRootProbeNote is non-empty exactly when LibraryRoot came from
	// the Linux none-of-the-candidates-exist fallback guess (WP 2.5's
	// probeLinuxLibraryRoot) rather than an explicit --library-root/env
	// value or a confirmed-existing probe hit. Empty on Windows always,
	// and empty on Linux whenever a real value was found or given.
	//
	// This exists so that guess is not a SILENT one: main.go logs it once
	// at startup (see cmd/vault-agent's runReport) instead of the
	// descriptive error probeLinuxLibraryRoot already builds
	// (probeLinuxLibraryRootError, naming every path it checked) being
	// constructed and then thrown away with no caller ever seeing it -
	// which is exactly what happened before this field existed: the
	// error was real, but unreachable by any operator.
	LibraryRootProbeNote string
	ReportInterval       time.Duration // only consulted in --loop mode
	Loop                 bool

	// AllowEmpty (--allow-empty) lets `report` post an empty installed
	// list even when NO Steam library under LibraryRoot could be read.
	// Off by default (WP AGENT-FIX-1 S1): a wrong library root used to
	// post a legitimate-looking empty snapshot, which made vault-api drop
	// every one of this client's games from the prefill set. A readable
	// library that simply has nothing installed never needs this flag -
	// that empty report is posted regardless (ADR-0002 full-list
	// semantics). Flag-only, like --loop: it is a deliberate per-
	// invocation override, not a deployment setting.
	AllowEmpty bool
}

// Redacted returns a copy of cfg safe to log: APIKey is replaced with a
// fixed placeholder, never a partial/truncated key (a truncated key is
// still a key fragment), and any userinfo in ServerURL (a
// "https://user:password@host" form an operator may use for a reverse
// proxy's basic auth) is replaced the same way - cmd/vault-agent logs the
// server URL at every start, into vault-agent.log / the journal (WP
// AGENT-FIX-1 S2).
func (c Config) Redacted() Config {
	c.APIKey = "<redacted>"
	c.ServerURL = redactURL(c.ServerURL)
	return c
}

// redactedUserinfo is the placeholder redactURL substitutes for a URL's
// userinfo. Inserted by string surgery rather than url.User("<redacted>"),
// because url.URL.String percent-encodes '<' and '>' in userinfo
// ("%3Credacted%3E@"), which reads like a real, odd username.
const redactedUserinfo = "<redacted>@"

// redactURL returns raw with any userinfo replaced by redactedUserinfo.
// A URL without userinfo is returned byte-identical. Fail-closed: a raw
// value url.Parse cannot even parse is replaced entirely by a placeholder
// rather than echoed (it could still contain a password).
func redactURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return "<unparseable url, redacted>"
	}
	if u.User == nil {
		return raw
	}
	u.User = nil
	stripped := u.String()
	i := strings.Index(stripped, "://")
	if i < 0 {
		// Scheme-less/opaque URL that still carried userinfo: Config
		// validation rejects these, so this is defensive only.
		return redactedUserinfo + stripped
	}
	return stripped[:i+3] + redactedUserinfo + stripped[i+3:]
}

// Getenv matches os.Getenv's signature; Parse takes it as a parameter
// (rather than calling os.Getenv directly) so tests can inject a fake
// environment without mutating the real process environment.
type Getenv func(key string) string

// ParseError collects every configuration problem found, so a
// misconfigured agent gets ONE clear report instead of failing on the
// first flag and hiding the rest (the brief's "fail loudly" requirement,
// mirroring vault_api.config.Settings.from_env's style: raise with a
// specific, actionable message rather than defaulting a required value).
type ParseError struct {
	Errs []string
}

func (e *ParseError) Error() string {
	return "invalid vault-agent configuration:\n  - " + strings.Join(e.Errs, "\n  - ")
}

// flagSpec is the small set of flags shared by every present/future
// subcommand that needs a Config (today: only `report`).
type flagSpec struct {
	serverURL   string
	apiKey      string
	clientID    string
	libraryRoot string
	interval    string
	loop        bool
	allowEmpty  bool
}

// Parse parses args (NOT including the subcommand name itself, e.g. for
// `vault-agent report --loop`, args is `["--loop"]`) against flags,
// applies the env-var fallback and defaults, and validates the result.
//
// name is used as the flag.FlagSet's name (for its own usage/error
// messages only). output receives flag.FlagSet's usage/error text (-h,
// an unknown flag, ...) - it MUST be the same writer the caller logs to,
// never left to default to the real os.Stderr, so a caller that captures
// its own output (tests; a future embedding) sees everything flag prints.
//
// SECURITY: flags are registered with EMPTY string defaults, never
// getenv(...). flag.FlagSet's usage text (printed on -h AND on an unknown
// flag) includes each flag's default value verbatim - registering
// getenv(EnvAPIKey) as the "api-key" flag's default would print the REAL
// API key to stdout/stderr the moment a user ran `vault-agent report -h`
// or fat-fingered a flag name, regardless of whether --api-key was even
// given (WP 2.2 review finding B1). The env fallback is applied
// separately in build(), AFTER flag parsing, where it never touches any
// usage/help text.
func Parse(name string, args []string, getenv Getenv, output io.Writer) (Config, error) {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(output)
	spec := flagSpec{}

	fs.StringVar(&spec.serverURL, "server-url", "",
		"vault-api base URL, e.g. http://100.x.y.z:8080 (env "+EnvServerURL+")")
	fs.StringVar(&spec.apiKey, "api-key", "",
		"vault-api X-Api-Key value (env "+EnvAPIKey+"; prefer the env var over this flag - "+
			"flag values are visible in process listings)")
	fs.StringVar(&spec.clientID, "client-id", "",
		"identifies this machine to vault-api, 1-64 chars (env "+EnvClientID+"; default: sanitized hostname)")
	fs.StringVar(&spec.libraryRoot, "library-root", "",
		"Steam install directory containing steamapps/ (env "+EnvLibraryRoot+"; default: OS-specific)")
	fs.StringVar(&spec.interval, "interval", "",
		"report interval for --loop mode, e.g. 30m (env "+EnvInterval+"; default: "+DefaultReportInterval.String()+")")
	fs.BoolVar(&spec.loop, "loop", false, "keep running, reporting every --interval (jittered) until SIGTERM/CTRL-C")
	fs.BoolVar(&spec.allowEmpty, "allow-empty", false,
		"post an empty installed list even when no Steam library under --library-root could be read "+
			"(default: refuse and exit 1, since that usually means a wrong --library-root)")

	if err := fs.Parse(args); err != nil {
		return Config{}, err // flag package already printed usage (to output) on -h/bad flag
	}

	return build(spec, getenv)
}

// build applies the env-var fallback (a non-empty flag value always wins
// over its env var - see spec field vs. getenv(...) below) and validates
// the result. Every flag defaults to "" from Parse, so this is the ONLY
// place a real secret/URL value is assembled - never inside a flag
// default, which flag.Usage() would echo back out.
func build(spec flagSpec, getenv Getenv) (Config, error) {
	var errs []string

	serverURL := spec.serverURL
	if serverURL == "" {
		serverURL = getenv(EnvServerURL)
	}
	serverURL = strings.TrimSpace(serverURL)
	if serverURL == "" {
		errs = append(errs, fmt.Sprintf(
			"server URL is required: set --server-url or %s", EnvServerURL,
		))
	} else if err := validateServerURL(serverURL); err != nil {
		errs = append(errs, err.Error())
	}
	serverURL = strings.TrimRight(serverURL, "/")

	apiKey := spec.apiKey
	if apiKey == "" {
		apiKey = getenv(EnvAPIKey)
	}
	// deliberately NOT trimmed beyond this: a key is opaque data, not text
	if apiKey == "" {
		errs = append(errs, fmt.Sprintf(
			"API key is required: set --api-key or (preferred) %s", EnvAPIKey,
		))
	}

	clientID := spec.clientID
	var clientIDSource, clientIDNote string
	if clientID != "" {
		clientIDSource = ClientIDSourceFlag
		clientIDNote = "explicit via --client-id"
	} else {
		clientID = getenv(EnvClientID)
		if clientID != "" {
			clientIDSource = ClientIDSourceEnv
			clientIDNote = fmt.Sprintf("explicit via %s", EnvClientID)
		}
	}
	if clientID == "" {
		derived, sanitizedNote, derr := defaultClientID(hostnameFunc)
		if derr != nil {
			errs = append(errs, fmt.Sprintf(
				"client-id not given and could not be derived from the local hostname (%s); "+
					"set --client-id or %s explicitly", derr, EnvClientID,
			))
		} else {
			clientID = derived
			clientIDSource = ClientIDSourceDerived
			if sanitizedNote != "" {
				clientIDNote = fmt.Sprintf("%s; override with --client-id or %s", sanitizedNote, EnvClientID)
			} else {
				clientIDNote = fmt.Sprintf(
					"derived from this machine's hostname; override with --client-id or %s", EnvClientID,
				)
			}
		}
	}
	if clientID != "" {
		if err := report.ValidateClientID(clientID); err != nil {
			errs = append(errs, fmt.Sprintf("client-id: %s", err))
		}
	}

	libraryRoot := spec.libraryRoot
	if libraryRoot == "" {
		libraryRoot = getenv(EnvLibraryRoot)
	}
	libraryRoot = strings.TrimSpace(libraryRoot)
	var libraryRootProbeNote string
	if libraryRoot == "" {
		libraryRoot, libraryRootProbeNote = defaultLibraryRoot(runtime.GOOS)
	}

	rawInterval := spec.interval
	if rawInterval == "" {
		rawInterval = getenv(EnvInterval)
	}

	interval := DefaultReportInterval
	if raw := strings.TrimSpace(rawInterval); raw != "" {
		parsed, err := time.ParseDuration(raw)
		if err != nil {
			errs = append(errs, fmt.Sprintf(
				"--interval/%s value %q is not a valid duration (e.g. \"30m\", \"1h\"): %s",
				EnvInterval, raw, err,
			))
		} else if parsed <= 0 {
			errs = append(errs, fmt.Sprintf(
				"--interval/%s must be positive, got %q", EnvInterval, raw,
			))
		} else {
			interval = parsed
		}
	}

	if len(errs) > 0 {
		return Config{}, &ParseError{Errs: errs}
	}

	return Config{
		ServerURL:            serverURL,
		APIKey:               apiKey,
		ClientID:             clientID,
		ClientIDSource:       clientIDSource,
		ClientIDNote:         clientIDNote,
		LibraryRoot:          libraryRoot,
		LibraryRootProbeNote: libraryRootProbeNote,
		ReportInterval:       interval,
		Loop:                 spec.loop,
		AllowEmpty:           spec.allowEmpty,
	}, nil
}

// validateServerURL never echoes raw into its error: the messages land
// in the "config error=" log line, and a value with userinfo would leak
// the password there (WP AGENT-FIX-1 S2). The parse-failure branch
// unwraps *url.Error deliberately - its Error() text quotes the whole
// raw URL.
func validateServerURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil {
		cause := err
		var urlErr *url.Error
		if errors.As(err, &urlErr) {
			cause = urlErr.Err
		}
		return fmt.Errorf("server URL is not a valid URL: %w", cause)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("server URL %q must use http:// or https://, got scheme %q", redactURL(raw), u.Scheme)
	}
	if u.Host == "" {
		return fmt.Errorf("server URL %q has no host", redactURL(raw))
	}
	return nil
}

// hostnameFunc is a package var (not a hardcoded os.Hostname() call) so
// config_test.go can substitute a deterministic value.
var hostnameFunc = os.Hostname

// defaultClientID sanitizes the local hostname into something that passes
// report.ValidateClientID: trims surrounding whitespace, replaces every
// non-printable rune (see report.ValidateClientID's doc comment for
// exactly which runes that excludes - control characters, invisible
// format characters like a zero-width joiner, private-use and unassigned
// codepoints, non-ASCII spaces) with "-", and truncates to
// report.MaxClientIDLength. Returns an error if os.Hostname() itself
// fails, or if the result is empty or exactly "." or ".." after
// sanitizing (report.ValidateClientID would reject those verbatim - this
// function does not disagree with it, it just gives a clearer error
// attributing the cause to hostname derivation specifically).
//
// sanitizedNote is empty when the returned id is exactly the (whitespace-
// trimmed) hostname, and non-empty - naming both the original hostname and
// the id it became - whenever rune-replacement or truncation actually
// changed something (WP AG-0: this is the ONLY place that knows both
// values, so build() logs this text verbatim rather than re-deriving "did
// sanitizing change anything" from the two strings itself, which would be
// a second, driftable implementation of the same comparison).
func defaultClientID(hostname func() (string, error)) (clientID string, sanitizedNote string, err error) {
	name, err := hostname()
	if err != nil {
		return "", "", fmt.Errorf("os.Hostname failed: %w", err)
	}
	trimmed := strings.TrimSpace(name)

	var b strings.Builder
	for _, r := range trimmed {
		if !unicode.IsPrint(r) {
			b.WriteRune('-')
			continue
		}
		b.WriteRune(r)
	}
	sanitized := b.String()

	// Truncate to MaxClientIDLength RUNES (not bytes) - see
	// report.ValidateClientID's own rune-counting rationale.
	runes := []rune(sanitized)
	if len(runes) > report.MaxClientIDLength {
		runes = runes[:report.MaxClientIDLength]
	}
	sanitized = strings.TrimSpace(string(runes)) // truncation could re-expose trailing whitespace

	if sanitized == "" || sanitized == "." || sanitized == ".." {
		return "", "", fmt.Errorf("hostname %q sanitizes to an unusable client-id %q", name, sanitized)
	}

	if sanitized != trimmed {
		sanitizedNote = fmt.Sprintf(
			"hostname %q was sanitized to %q (non-printable characters replaced with \"-\" and/or truncated to %d characters)",
			name, sanitized, report.MaxClientIDLength,
		)
	}
	return sanitized, sanitizedNote, nil
}

// defaultLibraryRoot returns the Steam install directory to use when
// neither --library-root nor VAULT_AGENT_LIBRARY_ROOT is given, plus a
// note (empty string when there's nothing to say). An explicit
// --library-root/env value ALWAYS wins over this - build() only calls
// this when both are empty, and only build() decides what to do with the
// note (surface it as Config.LibraryRootProbeNote, which cmd/vault-agent
// logs once at startup - see agent/README.md's "Linux/SteamOS variant"
// section).
//
// No registry lookup is done on Windows (v1 scope, WP 2.2 brief); the
// note is always empty there. Linux probes three real, documented
// install locations in order (WP 2.5, see linuxLibraryRootCandidates)
// rather than a single hardcoded guess; the note is empty when a
// candidate was confirmed to exist, and non-empty (naming every path
// checked) exactly when none did and candidate 0 is being returned as an
// unconfirmed guess.
func defaultLibraryRoot(goos string) (path string, note string) {
	switch goos {
	case "windows":
		return `C:\Program Files (x86)\Steam`, ""
	default:
		// Linux/SteamOS (ADR-0002, WP 2.5). probeLinuxLibraryRoot only
		// fails when NONE of the candidates exist yet (fresh machine,
		// Steam not installed at any known location) - that is not a
		// config-parse error (mirrors the Windows default, which never
		// checks the disk either): fall back to the modern default
		// (candidate 0) and let the returned note (surfaced via
		// Config.LibraryRootProbeNote) tell the operator it's an
		// unconfirmed guess, on top of acf.DiscoverInstalled's own
		// resilience contract surfacing the missing-library Warning once
		// `report` actually runs against it.
		home, _ := os.UserHomeDir() // error/empty handled by the candidates helper itself
		candidates := linuxLibraryRootCandidates(home)
		found, err := probeLinuxLibraryRoot(candidates, dirExists)
		if err == nil {
			return found, ""
		}
		return candidates[0], fmt.Sprintf(
			"defaulted library-root to %q without confirming it exists (%s)",
			candidates[0], err,
		)
	}
}

// dirExists reports whether path exists and is a directory. A package var
// (not a direct os.Stat call inlined into probeLinuxLibraryRoot) so tests
// can substitute a fake filesystem view - real candidate paths live under
// a real user's $HOME and unit tests must never depend on what happens to
// exist there on whichever machine runs `go test` (WP 2.5).
var dirExists = func(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// linuxLibraryRootCandidates returns, in probe order, the real Steam-on-
// Linux install locations defaultLibraryRoot checks (WP 2.5, ADR-0002):
//
//  1. ~/.local/share/Steam
//     The modern (2019+) default. Confirmed against a real installed
//     client (WSL2 Ubuntu, current stable Steam-for-Linux, see
//     agent/README.md's Linux discovery section) - this is where the
//     official installer puts it, and it's the XDG data-home convention
//     (Steam does not otherwise honor $XDG_DATA_HOME; the path is
//     hardcoded by the client, but shares the same location XDG would
//     pick).
//  2. ~/.steam/steam
//     The legacy path. On every modern install this directory is ITSELF
//     a symlink into #1 (confirmed on the same real install:
//     ~/.steam/steam -> ~/.local/share/Steam) - kept as a distinct probe
//     entry for older or hand-installed setups where it is a real
//     directory instead of a symlink to #1, not because it usually
//     points somewhere different (os.Stat follows symlinks, so on a
//     modern install this candidate resolves to the same directory as
//     #1 and is simply redundant with it, never wrong).
//  3. ~/.var/app/com.valvesoftware.Steam/.local/share/Steam
//     The Flatpak sandbox location. Not exercised by the real WSL2 probe
//     (that install is the native/deb package, not Flatpak), but this is
//     Flatpak's standard per-app data directory convention
//     (`~/.var/app/<app-id>/.local/share/...` mirrors the app's sandboxed
//     $XDG_DATA_HOME) applied to Steam's Flatpak app ID
//     (com.valvesoftware.Steam) - documented Flatpak behavior, checked
//     last because it's the least common of the three on a Steam
//     Deck/desktop-Linux gaming box, which normally has Steam natively
//     installed or preinstalled by the OS image.
//
// home is os.UserHomeDir()'s result. An empty home (lookup failed) falls
// back to bare relative paths - candidate 0 (".local/share/Steam") is
// then BYTE-IDENTICAL to the exact string the pre-WP-2.5 default
// returned in this case (that code returned the literal
// ".local/share/Steam", no "./" prefix), so a broken $HOME lookup
// degrades exactly as before, not merely to something equivalent. (An
// earlier draft of this function built these with a "." + "/" base
// instead, which produces "./.local/share/Steam" - a working but
// DIFFERENT string from the pre-WP-2.5 one; the doc comment claimed
// parity that code didn't actually have. Fixed here, not just reworded.)
func linuxLibraryRootCandidates(home string) []string {
	if home == "" {
		return []string{
			".local/share/Steam",
			".steam/steam",
			".var/app/com.valvesoftware.Steam/.local/share/Steam",
		}
	}
	return []string{
		home + "/.local/share/Steam",
		home + "/.steam/steam",
		home + "/.var/app/com.valvesoftware.Steam/.local/share/Steam",
	}
}

// probeLinuxLibraryRootError is returned by probeLinuxLibraryRoot when
// none of the candidates exist. It names every path that was checked (in
// the order they were checked) so a log line built from it is actionable
// on its own, without the reader needing to already know the probe order
// documented above.
type probeLinuxLibraryRootError struct {
	candidates []string
}

func (e *probeLinuxLibraryRootError) Error() string {
	return fmt.Sprintf(
		"no Steam installation found at any of the known Linux locations (checked in order: %s); "+
			"pass --library-root or set %s explicitly if Steam is installed elsewhere",
		strings.Join(e.candidates, ", "), EnvLibraryRoot,
	)
}

// probeLinuxLibraryRoot returns the first candidate for which exists
// reports true, preserving candidate order (first-exists-wins). Returns a
// *probeLinuxLibraryRootError - never a bare string message, so a caller
// can errors.As if it ever needs to inspect which paths were tried -
// when none exist.
func probeLinuxLibraryRoot(candidates []string, exists func(string) bool) (string, error) {
	for _, c := range candidates {
		if exists(c) {
			return c, nil
		}
	}
	return "", &probeLinuxLibraryRootError{candidates: candidates}
}
