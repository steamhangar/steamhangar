// The `hosts` subcommand family (WP 2.3): the opt-in, DNS-free hosts-file
// mode from plan §10 deployment mode 3. It automates, auditably and
// reversibly, exactly what poc/steam-client-test/PROTOCOL.md §1 and §4 ask
// the user to do by hand in an elevated Notepad.
//
// Everything about the file format, the refusal cases, and the write
// strategy lives in agent/go/hostsfile; this file is only the CLI surface:
// flags, human-readable output, exit codes, and the resolver "is it live"
// check.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/Riviera822/steamhangar/agent/hostsfile"
)

// resolverTimeout bounds the `hosts status` resolution check. Short on
// purpose: with no hosts entry present this is a real DNS query to
// whatever resolver the machine uses, and status is meant to answer in a
// moment, not hang on a dead link.
const resolverTimeout = 3 * time.Second

// lookupHost is a package var so tests can drive `hosts status` without
// touching the network or depending on the machine's DNS.
var lookupHost = func(ctx context.Context, host string) ([]string, error) {
	return net.DefaultResolver.LookupHost(ctx, host)
}

func hostsUsage(w io.Writer, progName string) {
	fmt.Fprintf(w, `usage: %[1]s hosts <apply|remove|status> [flags]

  apply   --cache-ip <ipv4> [--hosts-path <path>]
          add or update the managed %[2]s block (needs admin/root)
  remove  [--hosts-path <path>]
          delete the managed block again (the clean uninstall path)
  status  [--cache-ip <ipv4>] [--hosts-path <path>]
          report the managed block and what %[2]s resolves to right now

vault-agent manages EXACTLY one marker-delimited block and never touches
anything outside it. Every change is backed up to <hosts file>%[3]s first.
`, progName, hostsfile.Hostname, hostsfile.BackupSuffix)
}

func runHosts(args []string, stdout, stderr io.Writer, progName string) int {
	if len(args) == 0 {
		hostsUsage(stderr, progName)
		return 2
	}
	switch args[0] {
	case "apply":
		return hostsApply(args, stdout, stderr, progName)
	case "remove":
		return hostsRemove(args, stdout, stderr, progName)
	case "status":
		return hostsStatus(args, stdout, stderr, progName)
	default:
		fmt.Fprintf(stderr, "unknown hosts subcommand %q\n\n", args[0])
		hostsUsage(stderr, progName)
		return 2
	}
}

// parseHostsFlags parses a subcommand's flags. The bool result is false
// when the caller should return the returned exit code immediately (help
// requested, bad flag, stray positional argument).
func parseHostsFlags(fs *flag.FlagSet, args []string, stderr io.Writer) (int, bool) {
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0, false // flag already printed usage to stderr
		}
		return 2, false
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(stderr, "%s: unexpected argument %q\n", fs.Name(), fs.Arg(0))
		return 2, false
	}
	return 0, true
}

func hostsPathFlag(fs *flag.FlagSet) *string {
	return fs.String("hosts-path", "",
		"hosts file to operate on (default: this platform's system hosts file). "+
			"Intended for tests and sandboxes")
}

func resolveHostsPath(override string) string {
	if p := strings.TrimSpace(override); p != "" {
		return p
	}
	return hostsfile.DefaultPath()
}

// samePath compares two paths the way the host filesystem would, so
// `--hosts-path c:\windows\system32\drivers\etc\hosts` is recognized as the
// system file and does not trigger the "this is a different file" note.
// Case-insensitive on Windows only; Linux paths really are case-sensitive.
func samePath(a, b string) bool {
	a, b = filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "windows" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// requireCacheIP validates a --cache-ip value. A bad or missing value is a
// USAGE error (exit 2), not a runtime failure.
func requireCacheIP(raw, what string, stderr io.Writer) (string, int, bool) {
	ip := strings.TrimSpace(raw)
	if ip == "" {
		fmt.Fprintf(stderr, "%s: --cache-ip is required "+
			"(the LAN IPv4 address of the machine running vault-core, e.g. 192.168.1.50)\n", what)
		return "", 2, false
	}
	if err := hostsfile.ValidateCacheIP(ip); err != nil {
		fmt.Fprintf(stderr, "%s: %s\n", what, err)
		return "", 2, false
	}
	return ip, 0, true
}

// reportHostsError prints a failed mutation and, when the cause was a
// permission problem, the exact elevated command to re-run.
func reportHostsError(stderr io.Writer, what string, err error, progName string, args []string) int {
	fmt.Fprintf(stderr, "%s: %s\n", what, err)
	if hostsfile.IsPermissionDenied(err) {
		fmt.Fprintf(stderr, "\n%s\n", hostsfile.ElevationHint(commandLine(progName, args)))
	}
	return 1
}

// commandLine reconstructs the command the user just ran, so the
// elevation hint can say "run exactly this" instead of "run it as admin".
func commandLine(progName string, args []string) string {
	parts := make([]string, 0, len(args)+2)
	parts = append(parts, progName, "hosts")
	for _, a := range args {
		parts = append(parts, quoteArg(a))
	}
	return strings.Join(parts, " ")
}

func quoteArg(a string) string {
	if a == "" {
		return `""`
	}
	if !strings.ContainsAny(a, " \t\"") {
		return a
	}
	return `"` + strings.ReplaceAll(a, `"`, `\"`) + `"`
}

func printBlock(w io.Writer, lines []string) {
	fmt.Fprintln(w, "block:")
	for _, l := range lines {
		fmt.Fprintf(w, "  %s\n", l)
	}
}

func printConflicts(w io.Writer, conflicts []hostsfile.Conflict) {
	if len(conflicts) == 0 {
		return
	}
	fmt.Fprintf(w, "conflicts:  %d entr(y/ies) for %s outside the managed block\n",
		len(conflicts), hostsfile.Hostname)
	for _, c := range conflicts {
		where := "after the managed block"
		if c.BeforeManaged {
			where = "BEFORE the managed block - the resolver uses this one, not ours"
		}
		fmt.Fprintf(w, "  line %d: %s   (%s)\n", c.Line, c.Text, where)
	}
}

// ---------------------------------------------------------------------------

func hostsApply(args []string, stdout, stderr io.Writer, progName string) int {
	fs := flag.NewFlagSet("hosts apply", flag.ContinueOnError)
	fs.SetOutput(stderr)
	cacheIP := fs.String("cache-ip", "",
		"IPv4 address of the machine running vault-core, e.g. 192.168.1.50 (required)")
	hostsPath := hostsPathFlag(fs)
	if code, ok := parseHostsFlags(fs, args[1:], stderr); !ok {
		return code
	}

	ip, code, ok := requireCacheIP(*cacheIP, "hosts apply", stderr)
	if !ok {
		return code
	}
	path := resolveHostsPath(*hostsPath)

	res, err := hostsfile.Apply(path, ip)
	if err != nil {
		return reportHostsError(stderr, "hosts apply", err, progName, args)
	}

	fmt.Fprintf(stdout, "hosts file: %s\n", res.Path)
	if res.Changed {
		fmt.Fprintf(stdout, "state:      %s -> %s\n", res.Before.State, hostsfile.StatePresentCorrect)
		fmt.Fprintf(stdout, "backup:     %s\n", res.BackupPath)
		fmt.Fprintf(stdout, "write:      %s\n", res.Method)
	} else {
		fmt.Fprintf(stdout, "state:      %s (already exactly right - nothing was written)\n", res.Before.State)
	}
	printBlock(stdout, hostsfile.BlockPreview(ip))
	if res.Changed {
		fmt.Fprintln(stdout, "next:       fully quit and restart Steam - it only runs its cache")
		fmt.Fprintln(stdout, "            discovery at startup, so a running client ignores this")
	}
	return 0
}

func hostsRemove(args []string, stdout, stderr io.Writer, progName string) int {
	fs := flag.NewFlagSet("hosts remove", flag.ContinueOnError)
	fs.SetOutput(stderr)
	hostsPath := hostsPathFlag(fs)
	if code, ok := parseHostsFlags(fs, args[1:], stderr); !ok {
		return code
	}
	path := resolveHostsPath(*hostsPath)

	res, err := hostsfile.Remove(path)
	if err != nil {
		return reportHostsError(stderr, "hosts remove", err, progName, args)
	}

	fmt.Fprintf(stdout, "hosts file: %s\n", res.Path)
	if res.Changed {
		fmt.Fprintf(stdout, "state:      %s -> %s\n", res.Before.State, hostsfile.StateAbsent)
		fmt.Fprintf(stdout, "backup:     %s\n", res.BackupPath)
		fmt.Fprintf(stdout, "write:      %s\n", res.Method)
		fmt.Fprintln(stdout, "next:       fully quit and restart Steam so it stops using the cache")
	} else {
		fmt.Fprintf(stdout, "state:      %s (no managed block present - nothing to do)\n", res.Before.State)
	}
	return 0
}

// hostsStatus always exits 0 once it has produced a report, whatever the
// state turns out to be — the state is the OUTPUT, not an error. Exit 1 is
// reserved for "the report could not be produced at all" (e.g. the hosts
// file exists but cannot be read).
func hostsStatus(args []string, stdout, stderr io.Writer, progName string) int {
	fs := flag.NewFlagSet("hosts status", flag.ContinueOnError)
	fs.SetOutput(stderr)
	cacheIP := fs.String("cache-ip", "",
		"IPv4 address to compare the managed block against (optional; "+
			"without it any address in the block counts as correct)")
	hostsPath := hostsPathFlag(fs)
	if code, ok := parseHostsFlags(fs, args[1:], stderr); !ok {
		return code
	}

	expected := strings.TrimSpace(*cacheIP)
	if expected != "" {
		var ok bool
		var code int
		expected, code, ok = requireCacheIP(expected, "hosts status", stderr)
		if !ok {
			return code
		}
	}
	path := resolveHostsPath(*hostsPath)

	st, err := hostsfile.Verify(path, expected)
	if err != nil {
		return reportHostsError(stderr, "hosts status", err, progName, args)
	}

	fmt.Fprintf(stdout, "hosts file: %s\n", st.Path)
	if !st.Exists {
		fmt.Fprintf(stdout, "state:      %s (the hosts file does not exist)\n", st.State)
	} else {
		fmt.Fprintf(stdout, "state:      %s\n", st.State)
	}
	if st.Detail != "" && st.Exists {
		fmt.Fprintf(stdout, "detail:     %s\n", st.Detail)
	}
	if st.Address != "" {
		fmt.Fprintf(stdout, "managed IP: %s\n", st.Address)
	}
	if st.Present() {
		lines := make([]string, 0, len(st.BlockLines)+2)
		lines = append(lines, hostsfile.BeginMarker)
		lines = append(lines, st.BlockLines...)
		lines = append(lines, hostsfile.EndMarker)
		printBlock(stdout, lines)
	}
	printConflicts(stdout, st.Conflicts)

	ctx, cancel := context.WithTimeout(context.Background(), resolverTimeout)
	defer cancel()
	addrs, lerr := lookupHost(ctx, hostsfile.Hostname)
	if lerr != nil {
		fmt.Fprintf(stdout, "resolver:   %s does not resolve (%s)\n", hostsfile.Hostname, lerr)
	} else {
		fmt.Fprintf(stdout, "resolver:   %s -> %s\n", hostsfile.Hostname, strings.Join(addrs, ", "))
	}

	// Honesty note: the resolver answers from the SYSTEM hosts file, always.
	// With --hosts-path pointed somewhere else the two lines above describe
	// two different files, and silently letting the user conflate them would
	// be exactly the kind of "looks verified, isn't" this package avoids.
	if !samePath(st.Path, hostsfile.DefaultPath()) {
		fmt.Fprintf(stdout, "note:       the resolver line reflects the SYSTEM hosts file (%s),\n",
			hostsfile.DefaultPath())
		fmt.Fprintf(stdout, "            not the --hosts-path file reported above\n")
	}
	return 0
}
