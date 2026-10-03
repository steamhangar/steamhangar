package main

import (
	"fmt"
	"io"
)

// Build identity (WP VER-1). A source build reports "dev" / "unknown"; the
// release binaries get the real values from .github/workflows/publish.yml's
// agent-binaries job:
//
//	go build -ldflags "-X main.version=0.1.0-rc8 -X main.commit=<sha>"
//
// The linker silently ignores -X for a symbol that does not exist, so these
// two names are pinned from both ends: TestLdflagsSetVersionAndCommit builds
// with exactly those flags, and api/tests/test_ver_1_build_version.py pins
// the flags in publish.yml. Keep them plain package-level string variables
// initialised to constant strings: that is the case the linker documents
// -X for. (Measured on go1.27.1: a trivially inlinable function
// initialiser still worked, but that is not the documented contract.)
var (
	version = "dev"
	commit  = "unknown"
)

// isVersionFlag reports whether arg asks for the version. Go's flag
// package accepts one or two dashes, so both spellings work here too.
func isVersionFlag(arg string) bool {
	return arg == "--version" || arg == "-version"
}

// printVersion writes the one-line build identity, e.g.
// "vault-agent 0.1.0-rc8 (commit 1a2b...)". Every report also carries
// version as agent_version (WP AGENT-FEAT-1, see main.go's reportOnce).
func printVersion(w io.Writer) {
	fmt.Fprintf(w, "vault-agent %s (commit %s)\n", version, commit)
}
