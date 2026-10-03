package main

import (
	"bytes"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// WP VER-1: `vault-agent --version` (and Go's single-dash spelling) prints
// the build identity on stdout and exits 0, before any subcommand or config
// parsing -- it needs no server URL or API key.
func TestRun_VersionFlagPrintsBuildIdentity(t *testing.T) {
	for _, arg := range []string{"--version", "-version"} {
		var stdout, stderr bytes.Buffer
		code := run([]string{arg}, &stdout, &stderr)
		if code != 0 {
			t.Errorf("%s: exit code = %d, want 0", arg, code)
		}
		if got, want := stdout.String(), "vault-agent dev (commit unknown)\n"; got != want {
			t.Errorf("%s: stdout = %q, want %q", arg, got, want)
		}
		if stderr.Len() != 0 {
			t.Errorf("%s: stderr = %q, want empty", arg, stderr.String())
		}
	}
}

// The output comes from the two variables the linker overwrites, not from
// a literal that happens to equal their defaults.
func TestRun_VersionFlagReadsTheStampedVariables(t *testing.T) {
	oldVersion, oldCommit := version, commit
	t.Cleanup(func() { version, commit = oldVersion, oldCommit })
	version, commit = "0.1.0-rc99", "0123456789abcdef0123456789abcdef01234567"

	var stdout, stderr bytes.Buffer
	if code := run([]string{"--version"}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}
	want := "vault-agent 0.1.0-rc99 (commit 0123456789abcdef0123456789abcdef01234567)\n"
	if got := stdout.String(); got != want {
		t.Errorf("stdout = %q, want %q", got, want)
	}
}

// `--version` is not a report flag: it is only recognised as the first
// argument, so `report --version` still goes to the report flag set (which
// rejects it as unknown, exit 2) instead of silently printing a version.
func TestRun_VersionFlagOnlyAsFirstArgument(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"report", "--version"}, &stdout, &stderr)
	if code != 2 {
		t.Errorf("exit code = %d, want 2", code)
	}
	if strings.Contains(stdout.String(), "vault-agent dev") {
		t.Errorf("stdout = %q, want no version line", stdout.String())
	}
}

// The release build's -ldflags (publish.yml, agent-binaries) must actually
// reach the variables. The linker ignores -X for a symbol that does not
// exist, so a renamed variable would build fine and ship "dev"; this builds
// the real binary with the exact flag shape publish.yml uses and runs it.
func TestLdflagsSetVersionAndCommit(t *testing.T) {
	if testing.Short() {
		t.Skip("builds a binary; skipped with -short")
	}
	goBin, err := exec.LookPath("go")
	if err != nil {
		t.Skip("go toolchain not on PATH")
	}
	out := filepath.Join(t.TempDir(), "vault-agent")
	if runtime.GOOS == "windows" {
		out += ".exe"
	}
	const wantVersion = "9.8.7-ldflags"
	const wantCommit = "fedcba9876543210fedcba9876543210fedcba98"
	build := exec.Command(goBin, "build",
		"-ldflags", "-X main.version="+wantVersion+" -X main.commit="+wantCommit,
		"-o", out, ".")
	build.Env = append(build.Environ(), "CGO_ENABLED=0")
	if msg, err := build.CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, msg)
	}
	got, err := exec.Command(out, "--version").Output()
	if err != nil {
		t.Fatalf("%s --version: %v", out, err)
	}
	want := "vault-agent " + wantVersion + " (commit " + wantCommit + ")"
	if strings.TrimRight(string(got), "\r\n") != want {
		t.Errorf("--version printed %q, want %q", got, want)
	}
}
