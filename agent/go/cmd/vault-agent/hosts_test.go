package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Riviera822/steamhangar/agent/hostsfile"
)

const testCacheIP = "192.168.1.50"

// stubLookup replaces the resolver used by `hosts status` for the duration
// of one test, so the suite never depends on (or touches) real DNS.
func stubLookup(t *testing.T, addrs []string, err error) {
	t.Helper()
	prev := lookupHost
	lookupHost = func(ctx context.Context, host string) ([]string, error) {
		return addrs, err
	}
	t.Cleanup(func() { lookupHost = prev })
}

func tempHosts(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "hosts")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	return path
}

func readAll(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}
	return string(b)
}

// runHostsCmd drives the CLI exactly as run() would, through the real
// top-level dispatcher.
func runHostsCmd(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	var stdout, stderr bytes.Buffer
	code := run(append([]string{"hosts"}, args...), &stdout, &stderr)
	return code, stdout.String(), stderr.String()
}

// ---------------------------------------------------------------------------
// dispatch
// ---------------------------------------------------------------------------

func TestRun_HostsIsAKnownCommand(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"hosts"}, &stdout, &stderr)
	if code != 2 {
		t.Errorf("exit code = %d, want 2 for `hosts` with no subcommand", code)
	}
	if !strings.Contains(stderr.String(), "usage:") {
		t.Errorf("stderr = %q, want the hosts usage text", stderr.String())
	}
	if !strings.Contains(stderr.String(), hostsfile.Hostname) {
		t.Errorf("stderr = %q, want it to name the managed hostname", stderr.String())
	}
}

func TestRun_TopLevelUsageMentionsBothCommands(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run(nil, &stdout, &stderr)
	if code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	for _, want := range []string{"usage:", "report", "hosts"} {
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("stderr = %q, want it to mention %q", stderr.String(), want)
		}
	}
}

func TestHosts_UnknownSubcommandIsUsageError(t *testing.T) {
	code, _, stderr := runHostsCmd(t, "frobnicate")
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr, "frobnicate") {
		t.Errorf("stderr = %q, want it to name the unknown subcommand", stderr)
	}
}

func TestHosts_HelpExitsZero(t *testing.T) {
	for _, sub := range []string{"apply", "remove", "status"} {
		code, _, _ := runHostsCmd(t, sub, "-h")
		if code != 0 {
			t.Errorf("`hosts %s -h` exit code = %d, want 0", sub, code)
		}
	}
}

func TestHosts_StrayPositionalArgumentIsUsageError(t *testing.T) {
	path := tempHosts(t, "127.0.0.1 localhost\n")
	code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path, "extra")
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr, "extra") {
		t.Errorf("stderr = %q, want it to name the stray argument", stderr)
	}
}

// ---------------------------------------------------------------------------
// apply
// ---------------------------------------------------------------------------

func TestHostsApply_WritesTheBlockAndReportsIt(t *testing.T) {
	const before = "127.0.0.1 localhost\n"
	path := tempHosts(t, before)

	code, stdout, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr)
	}

	got := readAll(t, path)
	if !strings.HasPrefix(got, before) {
		t.Errorf("the original content was not preserved: %q", got)
	}
	if !strings.Contains(got, testCacheIP+" "+hostsfile.Hostname) {
		t.Errorf("hosts file = %q, want the managed entry", got)
	}

	for _, want := range []string{
		path,                          // which file
		string(hostsfile.StateAbsent), // the transition
		string(hostsfile.StatePresentCorrect),
		hostsfile.BackupSuffix, // where the backup went
		hostsfile.MethodRename, // how it was written
		hostsfile.BeginMarker,  // exactly what was written
		"restart Steam",        // what to do next
	} {
		if !strings.Contains(stdout, want) {
			t.Errorf("stdout = %q, want it to mention %q", stdout, want)
		}
	}

	// The backup holds the pre-apply bytes.
	if got := readAll(t, path+hostsfile.BackupSuffix); got != before {
		t.Errorf("backup = %q, want %q", got, before)
	}
}

func TestHostsApply_IsIdempotent(t *testing.T) {
	path := tempHosts(t, "127.0.0.1 localhost\n")

	if code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path); code != 0 {
		t.Fatalf("first apply failed: %s", stderr)
	}
	first := readAll(t, path)

	code, stdout, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 0 {
		t.Fatalf("second apply exit code = %d. stderr=%s", code, stderr)
	}
	if !strings.Contains(stdout, "nothing was written") {
		t.Errorf("stdout = %q, want it to say the file was already right", stdout)
	}
	if got := readAll(t, path); got != first {
		t.Errorf("the second apply changed the file:\n%q\n%q", first, got)
	}
}

func TestHostsApply_MissingCacheIPIsUsageError(t *testing.T) {
	path := tempHosts(t, "127.0.0.1 localhost\n")
	code, _, stderr := runHostsCmd(t, "apply", "--hosts-path", path)
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr, "--cache-ip is required") {
		t.Errorf("stderr = %q, want it to say --cache-ip is required", stderr)
	}
}

func TestHostsApply_InvalidCacheIPIsUsageErrorAndTouchesNothing(t *testing.T) {
	const before = "127.0.0.1 localhost\n"
	for _, bad := range []string{"cache.lan", "2001:db8::1", "0.0.0.0", "192.168.1.256", "010.1.1.1"} {
		path := tempHosts(t, before)
		code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", bad, "--hosts-path", path)
		if code != 2 {
			t.Errorf("--cache-ip %q: exit code = %d, want 2", bad, code)
		}
		if !strings.Contains(stderr, bad) {
			t.Errorf("--cache-ip %q: stderr = %q, want it to quote the bad value", bad, stderr)
		}
		if got := readAll(t, path); got != before {
			t.Errorf("--cache-ip %q modified the file: %q", bad, got)
		}
	}
}

func TestHostsApply_RefusesCorruptMarkers(t *testing.T) {
	before := "127.0.0.1 localhost\n" + hostsfile.BeginMarker + "\n" + testCacheIP + " " + hostsfile.Hostname + "\n"
	path := tempHosts(t, before)

	code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 1 {
		t.Errorf("exit code = %d, want 1", code)
	}
	if !strings.Contains(stderr, "Refusing to modify") {
		t.Errorf("stderr = %q, want an explicit refusal", stderr)
	}
	if got := readAll(t, path); got != before {
		t.Errorf("the file was modified despite the refusal: %q", got)
	}
}

func TestHostsApply_RefusesWhenAConflictingEntryExists(t *testing.T) {
	before := "127.0.0.1 localhost\n10.0.0.7 " + hostsfile.Hostname + "\n"
	path := tempHosts(t, before)

	code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 1 {
		t.Errorf("exit code = %d, want 1", code)
	}
	if !strings.Contains(stderr, "line 2") {
		t.Errorf("stderr = %q, want it to point at the conflicting line", stderr)
	}
	if got := readAll(t, path); got != before {
		t.Errorf("the file was modified despite the refusal: %q", got)
	}
}

// ---------------------------------------------------------------------------
// remove
// ---------------------------------------------------------------------------

func TestHostsRemove_RestoresTheOriginalFile(t *testing.T) {
	const before = "127.0.0.1 localhost\n10.0.0.5 nas.lan\n"
	path := tempHosts(t, before)

	if code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path); code != 0 {
		t.Fatalf("apply failed: %s", stderr)
	}
	code, stdout, stderr := runHostsCmd(t, "remove", "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr)
	}
	if got := readAll(t, path); got != before {
		t.Fatalf("after remove got %q, want the original %q", got, before)
	}
	if !strings.Contains(stdout, string(hostsfile.StateAbsent)) {
		t.Errorf("stdout = %q, want it to report the new state", stdout)
	}
}

func TestHostsRemove_IsIdempotent(t *testing.T) {
	const before = "127.0.0.1 localhost\n"
	path := tempHosts(t, before)

	code, stdout, stderr := runHostsCmd(t, "remove", "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr)
	}
	if !strings.Contains(stdout, "nothing to do") {
		t.Errorf("stdout = %q, want it to say there was nothing to remove", stdout)
	}
	if got := readAll(t, path); got != before {
		t.Errorf("a no-op remove modified the file: %q", got)
	}
	if _, err := os.Stat(path + hostsfile.BackupSuffix); err == nil {
		t.Error("a no-op remove wrote a backup")
	}
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

func TestHostsStatus_ReportsStateAndResolution(t *testing.T) {
	stubLookup(t, []string{testCacheIP}, nil)
	path := tempHosts(t, "127.0.0.1 localhost\n")
	if code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path); code != 0 {
		t.Fatalf("apply failed: %s", stderr)
	}

	code, stdout, stderr := runHostsCmd(t, "status", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0. stderr=%s", code, stderr)
	}
	for _, want := range []string{
		string(hostsfile.StatePresentCorrect),
		"managed IP: " + testCacheIP,
		"resolver:",
		hostsfile.Hostname + " -> " + testCacheIP,
		hostsfile.BeginMarker,
	} {
		if !strings.Contains(stdout, want) {
			t.Errorf("stdout = %q, want it to contain %q", stdout, want)
		}
	}
}

func TestHostsStatus_ExitsZeroWhateverTheState(t *testing.T) {
	stubLookup(t, nil, errors.New("no such host"))
	cases := map[string]string{
		"absent":  "127.0.0.1 localhost\n",
		"corrupt": hostsfile.BeginMarker + "\n",
		"modified": hostsfile.BeginMarker + "\nnonsense\n" +
			hostsfile.EndMarker + "\n",
	}
	for name, content := range cases {
		t.Run(name, func(t *testing.T) {
			path := tempHosts(t, content)
			code, stdout, stderr := runHostsCmd(t, "status", "--hosts-path", path)
			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (the state is the output, not an error). stderr=%s", code, stderr)
			}
			if !strings.Contains(stdout, "state:") {
				t.Errorf("stdout = %q, want a state line", stdout)
			}
			if !strings.Contains(stdout, "does not resolve") {
				t.Errorf("stdout = %q, want the failed-resolution line", stdout)
			}
			if got := readAll(t, path); got != content {
				t.Errorf("status modified the file: %q", got)
			}
		})
	}
}

func TestHostsStatus_ReportsDifferentIP(t *testing.T) {
	stubLookup(t, []string{"10.0.0.7"}, nil)
	path := tempHosts(t, hostsfile.BeginMarker+"\n10.0.0.7 "+hostsfile.Hostname+"\n"+hostsfile.EndMarker+"\n")

	code, stdout, _ := runHostsCmd(t, "status", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}
	if !strings.Contains(stdout, string(hostsfile.StatePresentDifferentIP)) {
		t.Errorf("stdout = %q, want %q", stdout, hostsfile.StatePresentDifferentIP)
	}
	if !strings.Contains(stdout, "managed IP: 10.0.0.7") {
		t.Errorf("stdout = %q, want the address actually in the file", stdout)
	}
}

func TestHostsStatus_WarnsThatTheResolverReflectsTheSystemFile(t *testing.T) {
	stubLookup(t, []string{"1.2.3.4"}, nil)
	path := tempHosts(t, "127.0.0.1 localhost\n")

	_, stdout, _ := runHostsCmd(t, "status", "--hosts-path", path)
	if !strings.Contains(stdout, "SYSTEM hosts file") {
		t.Errorf("stdout = %q, want the note that the resolver line describes a DIFFERENT file "+
			"than --hosts-path", stdout)
	}
	if !strings.Contains(stdout, hostsfile.DefaultPath()) {
		t.Errorf("stdout = %q, want the note to name the system hosts file path", stdout)
	}
}

func TestHostsStatus_ListsConflicts(t *testing.T) {
	stubLookup(t, []string{"10.0.0.7"}, nil)
	path := tempHosts(t, "10.0.0.7 "+hostsfile.Hostname+"\n")

	_, stdout, _ := runHostsCmd(t, "status", "--hosts-path", path)
	if !strings.Contains(stdout, "conflicts:") {
		t.Errorf("stdout = %q, want a conflicts section", stdout)
	}
	if !strings.Contains(stdout, "BEFORE the managed block") {
		t.Errorf("stdout = %q, want the shadowing warning", stdout)
	}
}

// A UTF-16 hosts file is the one case where `hosts status` does NOT exit 0:
// no honest report can be produced from a file this package cannot read, so
// it fails rather than printing a confident "absent".
func TestHostsStatus_RefusesAUTF16File(t *testing.T) {
	stubLookup(t, nil, errors.New("no such host"))
	path := filepath.Join(t.TempDir(), "hosts")
	utf16 := []byte{0xFF, 0xFE}
	for _, b := range []byte("127.0.0.1 localhost\r\n") {
		utf16 = append(utf16, b, 0x00)
	}
	if err := os.WriteFile(path, utf16, 0o644); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	before := readAll(t, path)

	code, _, stderr := runHostsCmd(t, "status", "--hosts-path", path)
	if code != 1 {
		t.Errorf("exit code = %d, want 1", code)
	}
	if !strings.Contains(stderr, "plain-text hosts file") {
		t.Errorf("stderr = %q, want the encoding refusal", stderr)
	}

	code, _, stderr = runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 1 {
		t.Errorf("apply exit code = %d, want 1", code)
	}
	if !strings.Contains(stderr, "convert it to UTF-8") {
		t.Errorf("stderr = %q, want the conversion hint", stderr)
	}
	if got := readAll(t, path); got != before {
		t.Error("the UTF-16 file was modified")
	}
}

func TestHostsStatus_MissingFile(t *testing.T) {
	stubLookup(t, nil, errors.New("no such host"))
	path := filepath.Join(t.TempDir(), "nope")
	code, stdout, _ := runHostsCmd(t, "status", "--hosts-path", path)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}
	if !strings.Contains(stdout, "does not exist") {
		t.Errorf("stdout = %q, want it to say the file does not exist", stdout)
	}
}

// ---------------------------------------------------------------------------
// elevation hint
// ---------------------------------------------------------------------------

func TestCommandLine_ReconstructsTheInvocation(t *testing.T) {
	got := commandLine("vault-agent.exe", []string{"apply", "--cache-ip", "192.168.1.50"})
	want := "vault-agent.exe hosts apply --cache-ip 192.168.1.50"
	if got != want {
		t.Errorf("commandLine = %q, want %q", got, want)
	}

	got = commandLine("vault-agent", []string{"apply", "--hosts-path", `C:\Program Files\hosts`})
	want = `vault-agent hosts apply --hosts-path "C:\Program Files\hosts"`
	if got != want {
		t.Errorf("commandLine = %q, want %q (a path with spaces must be quoted)", got, want)
	}
}

func TestHostsApply_PermissionDeniedPrintsTheExactElevatedCommand(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("running as root: permission bits are not enforced")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	const before = "127.0.0.1 localhost\n"
	if err := os.WriteFile(path, []byte(before), 0o444); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	code, _, stderr := runHostsCmd(t, "apply", "--cache-ip", testCacheIP, "--hosts-path", path)
	if code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
	// The hint must contain a runnable command naming the same arguments.
	for _, want := range []string{"hosts apply", "--cache-ip", testCacheIP, "--hosts-path", path} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr = %q, want the elevation hint to include %q", stderr, want)
		}
	}
	if got := readAll(t, path); got != before {
		t.Errorf("the file was modified despite the permission failure: %q", got)
	}
}
