package hostsfile

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const testIP = "192.168.1.50"

// writeFixture writes content to a fresh temp dir and returns the path.
// Content is written with os.WriteFile (no line-ending translation), so
// whatever bytes the test wrote are exactly what the code under test reads.
func writeFixture(t *testing.T, content string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	return path
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}
	return string(b)
}

func sha(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// assertUnchanged is the hash-compare the WP 2.3 brief asks for: the file
// must be byte-identical to what it was.
func assertUnchanged(t *testing.T, path, before string) {
	t.Helper()
	after := readFile(t, path)
	if after != before {
		t.Fatalf("file changed.\nbefore (sha %s): %q\nafter  (sha %s): %q",
			sha(before), before, sha(after), after)
	}
}

// block renders the managed block with the given line ending, including a
// terminator after the END marker.
func block(ip, eol string) string {
	return BeginMarker + eol + ip + " " + Hostname + eol + EndMarker + eol
}

// ---------------------------------------------------------------------------
// Verify
// ---------------------------------------------------------------------------

func TestVerify_Absent(t *testing.T) {
	path := writeFixture(t, "127.0.0.1 localhost\n")
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StateAbsent {
		t.Errorf("State = %q, want %q", st.State, StateAbsent)
	}
	if st.Present() {
		t.Error("Present() = true, want false")
	}
}

func TestVerify_MissingFileIsAbsentNotAnError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nope")
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify on a missing file must not error, got %v", err)
	}
	if st.Exists {
		t.Error("Exists = true, want false")
	}
	if st.State != StateAbsent {
		t.Errorf("State = %q, want %q", st.State, StateAbsent)
	}
}

func TestVerify_PresentCorrectAndDifferentIP(t *testing.T) {
	path := writeFixture(t, "127.0.0.1 localhost\n"+block(testIP, "\n"))

	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentCorrect {
		t.Errorf("State = %q, want %q (detail %q)", st.State, StatePresentCorrect, st.Detail)
	}
	if st.Address != testIP {
		t.Errorf("Address = %q, want %q", st.Address, testIP)
	}
	if !st.Canonical {
		t.Error("Canonical = false, want true for a block we rendered ourselves")
	}

	st, err = Verify(path, "10.0.0.9")
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentDifferentIP {
		t.Errorf("State = %q, want %q", st.State, StatePresentDifferentIP)
	}
	if st.Address != testIP {
		t.Errorf("Address = %q, want the address actually in the file (%q)", st.Address, testIP)
	}
}

func TestVerify_NoExpectationAcceptsAnyAddress(t *testing.T) {
	path := writeFixture(t, block("10.11.12.13", "\n"))
	st, err := Verify(path, "")
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentCorrect {
		t.Errorf("State = %q, want %q when no expectation was given", st.State, StatePresentCorrect)
	}
	if st.Address != "10.11.12.13" {
		t.Errorf("Address = %q, want 10.11.12.13", st.Address)
	}
}

func TestVerify_NonCanonicalFormattingIsStillCorrect(t *testing.T) {
	// Semantically identical, formatted differently (extra spaces, a tab,
	// a trailing comment): effective, so not "modified" - but not what we
	// would write, so not Canonical either.
	path := writeFixture(t, BeginMarker+"\n"+testIP+"\t "+Hostname+"   # added by hand\n"+EndMarker+"\n")
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentCorrect {
		t.Errorf("State = %q, want %q", st.State, StatePresentCorrect)
	}
	if st.Canonical {
		t.Error("Canonical = true, want false for hand-formatted content")
	}
	if st.Detail == "" {
		t.Error("Detail is empty, want an explanation that re-applying normalizes it")
	}
}

func TestVerify_ModifiedInteriorCases(t *testing.T) {
	cases := map[string]string{
		"extra line inside": BeginMarker + "\n" + testIP + " " + Hostname + "\n10.0.0.1 evil.example\n" + EndMarker + "\n",
		"empty block":       BeginMarker + "\n" + EndMarker + "\n",
		"wrong hostname":    BeginMarker + "\n" + testIP + " other.example\n" + EndMarker + "\n",
		"not an entry":      BeginMarker + "\njust some text\n" + EndMarker + "\n",
		"non-IPv4 address":  BeginMarker + "\nnotanip " + Hostname + "\n" + EndMarker + "\n",
		"IPv6 address":      BeginMarker + "\n::1 " + Hostname + "\n" + EndMarker + "\n",
	}
	for name, content := range cases {
		t.Run(name, func(t *testing.T) {
			path := writeFixture(t, content)
			st, err := Verify(path, testIP)
			if err != nil {
				t.Fatalf("Verify: %v", err)
			}
			if st.State != StatePresentModified {
				t.Errorf("State = %q, want %q", st.State, StatePresentModified)
			}
			if st.Detail == "" {
				t.Error("Detail is empty, want an explanation")
			}
			if !st.Present() {
				t.Error("Present() = false, want true (the boundaries are known)")
			}
		})
	}
}

func TestVerify_CorruptMarkerCases(t *testing.T) {
	cases := map[string]string{
		"BEGIN without END": "127.0.0.1 localhost\n" + BeginMarker + "\n" + testIP + " " + Hostname + "\n",
		"END without BEGIN": "127.0.0.1 localhost\n" + testIP + " " + Hostname + "\n" + EndMarker + "\n",
		"END before BEGIN":  EndMarker + "\n" + BeginMarker + "\n",
		"two BEGINs":        BeginMarker + "\n" + BeginMarker + "\n" + testIP + " " + Hostname + "\n" + EndMarker + "\n",
		"two ENDs":          BeginMarker + "\n" + testIP + " " + Hostname + "\n" + EndMarker + "\n" + EndMarker + "\n",
		"two whole blocks":  block(testIP, "\n") + block("10.0.0.1", "\n"),
	}
	for name, content := range cases {
		t.Run(name, func(t *testing.T) {
			path := writeFixture(t, content)
			st, err := Verify(path, testIP)
			if err != nil {
				t.Fatalf("Verify: %v", err)
			}
			if st.State != StateMarkersCorrupt {
				t.Fatalf("State = %q, want %q", st.State, StateMarkersCorrupt)
			}
			if st.Detail == "" {
				t.Error("Detail is empty, want the offending line numbers")
			}

			// Both mutations must refuse AND leave the file untouched.
			for _, op := range []struct {
				name string
				run  func() (Result, error)
			}{
				{"Apply", func() (Result, error) { return Apply(path, testIP) }},
				{"Remove", func() (Result, error) { return Remove(path) }},
			} {
				res, err := op.run()
				var ce *CorruptError
				if !asCorrupt(err, &ce) {
					t.Errorf("%s error = %v, want *CorruptError", op.name, err)
				}
				if res.Changed {
					t.Errorf("%s reported Changed=true on a corrupt file", op.name)
				}
				assertUnchanged(t, path, content)
				if _, serr := os.Stat(path + BackupSuffix); serr == nil {
					t.Errorf("%s wrote a backup despite refusing to act", op.name)
				}
			}
		})
	}
}

func asCorrupt(err error, target **CorruptError) bool {
	if err == nil {
		return false
	}
	ce, ok := err.(*CorruptError)
	if ok {
		*target = ce
	}
	return ok
}

func TestVerify_MarkerCommentTextMayDrift(t *testing.T) {
	// A block written by a hypothetical future/older version with a
	// different parenthetical must still be recognized - otherwise Apply
	// would append a SECOND block.
	content := "# BEGIN steamhangar-agent (some other wording)\n" +
		testIP + " " + Hostname + "\n# END steamhangar-agent v2\n"
	path := writeFixture(t, content)
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentCorrect {
		t.Fatalf("State = %q, want %q - marker detection must tolerate a reworded comment",
			st.State, StatePresentCorrect)
	}
}

// ---------------------------------------------------------------------------
// Legacy markers (WP RN-1, product rename steamvault -> steamhangar).
//
// A block written by an OLDER vault-agent build, before the rename, uses
// "# BEGIN steamvault-agent" / "# END steamvault-agent". Detection must
// still find it (or Apply would append a SECOND block above/below the
// unrecognized one, and Remove would leave it orphaned in the user's real
// system hosts file forever). steamkeep-agent/steamsilo-agent are the two
// other product names briefly considered the same day, recognized only
// defensively - they were never committed to the default branch.
// ---------------------------------------------------------------------------

func TestVerify_LegacyMarkerIsDetected(t *testing.T) {
	for _, legacyName := range []string{"steamvault-agent", "steamkeep-agent", "steamsilo-agent"} {
		t.Run(legacyName, func(t *testing.T) {
			content := "# BEGIN " + legacyName + " (managed block - do not edit inside)\n" +
				testIP + " " + Hostname + "\n# END " + legacyName + "\n"
			path := writeFixture(t, content)
			st, err := Verify(path, testIP)
			if err != nil {
				t.Fatalf("Verify: %v", err)
			}
			if st.State != StatePresentCorrect {
				t.Fatalf("State = %q, want %q - a legacy %s block must still be recognized as present",
					st.State, StatePresentCorrect, legacyName)
			}
			if !st.Present() {
				t.Errorf("Present() = false for a legacy %s block", legacyName)
			}
		})
	}
}

func TestApply_HealsLegacyMarkerToCurrentName(t *testing.T) {
	content := "# BEGIN steamvault-agent (managed block - do not edit inside)\n" +
		testIP + " " + Hostname + "\n# END steamvault-agent\n"
	path := writeFixture(t, content)

	res, err := Apply(path, testIP)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if !res.Changed {
		t.Fatal("Changed = false - a legacy-marker block must be rewritten to the current marker")
	}

	after := readFile(t, path)
	if strings.Contains(after, "steamvault-agent") {
		t.Errorf("legacy marker survived healing: %q", after)
	}
	if !strings.Contains(after, BeginMarker) || !strings.Contains(after, EndMarker) {
		t.Errorf("healed file does not contain the current markers: %q", after)
	}

	// Healing must not duplicate the block - exactly one BEGIN/END pair.
	if n := strings.Count(after, "# BEGIN"); n != 1 {
		t.Errorf("found %d BEGIN markers after healing, want 1 (a second block would mean the "+
			"legacy one was not recognized as ours): %q", n, after)
	}
}

func TestRemove_RemovesLegacyMarkerBlockEntirely(t *testing.T) {
	before := "127.0.0.1 localhost\n"
	content := before +
		"# BEGIN steamsilo-agent (managed block - do not edit inside)\n" +
		testIP + " " + Hostname + "\n# END steamsilo-agent\n"
	path := writeFixture(t, content)

	res, err := Remove(path)
	if err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if !res.Changed {
		t.Fatal("Changed = false - a legacy-marker block must be removed")
	}

	after := readFile(t, path)
	if after != before {
		t.Errorf("after Remove = %q, want exactly %q (legacy block fully gone, rest untouched)",
			after, before)
	}
	if strings.Contains(after, "steamsilo-agent") {
		t.Errorf("legacy marker survived Remove: %q", after)
	}
}

// ---------------------------------------------------------------------------
// Conflicts
// ---------------------------------------------------------------------------

func TestConflict_BlocksApplyButNotRemove(t *testing.T) {
	content := "127.0.0.1 localhost\n10.0.0.7 " + Hostname + "\n"
	path := writeFixture(t, content)

	res, err := Apply(path, testIP)
	var ce *ConflictError
	if err == nil {
		t.Fatal("Apply succeeded despite a conflicting entry")
	}
	if e, ok := err.(*ConflictError); ok {
		ce = e
	} else {
		t.Fatalf("Apply error = %T (%v), want *ConflictError", err, err)
	}
	if len(ce.Conflicts) != 1 || ce.Conflicts[0].Line != 2 {
		t.Errorf("conflicts = %+v, want one on line 2", ce.Conflicts)
	}
	if res.Changed {
		t.Error("Changed = true on a refused apply")
	}
	assertUnchanged(t, path, content)

	// Remove must NOT be blocked by a conflict (uninstall always works).
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove refused because of a conflict: %v", err)
	}
}

func TestConflict_CommentedOutEntryIsNotAConflict(t *testing.T) {
	path := writeFixture(t, "# 10.0.0.7 "+Hostname+" (parked)\n127.0.0.1 localhost\n")
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if len(st.Conflicts) != 0 {
		t.Errorf("Conflicts = %+v, want none for a commented-out line", st.Conflicts)
	}
	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply refused because of a commented-out line: %v", err)
	}
}

func TestConflict_FullyQualifiedTrailingDotCountsAsAConflict(t *testing.T) {
	// "lancache.steamcontent.com." is the same name to every resolver
	// (Windows normalizes the root label), so it shadows our block exactly
	// like the dotless spelling. Missing it would let Apply append a block
	// that is present, correct-looking, and completely ineffective.
	content := "127.0.0.1 localhost\n10.0.0.7 " + Hostname + ".\n"
	path := writeFixture(t, content)

	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if len(st.Conflicts) != 1 {
		t.Fatalf("Conflicts = %+v, want the FQDN spelling to count as one", st.Conflicts)
	}
	if _, err := Apply(path, testIP); err == nil {
		t.Fatal("Apply added a second entry despite an FQDN-spelled conflict")
	}
	assertUnchanged(t, path, content)
}

func TestVerify_TrailingDotInsideTheBlockIsStillCorrect(t *testing.T) {
	path := writeFixture(t, BeginMarker+"\n"+testIP+" "+Hostname+".\n"+EndMarker+"\n")
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if st.State != StatePresentCorrect {
		t.Errorf("State = %q, want %q (the FQDN spelling resolves identically)", st.State, StatePresentCorrect)
	}
	if st.Canonical {
		t.Error("Canonical = true, want false - re-applying should normalize the spelling")
	}
}

func TestConflict_HostnameMatchIsCaseInsensitiveAndPositionAware(t *testing.T) {
	content := "10.0.0.7 LanCache.SteamContent.COM\n" + block(testIP, "\n") +
		"10.0.0.8 other.example " + strings.ToUpper(Hostname) + "\n"
	path := writeFixture(t, content)
	st, err := Verify(path, testIP)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if len(st.Conflicts) != 2 {
		t.Fatalf("Conflicts = %+v, want 2", st.Conflicts)
	}
	if !st.Conflicts[0].BeforeManaged {
		t.Error("first conflict must be flagged as preceding the managed block")
	}
	if st.Conflicts[1].BeforeManaged {
		t.Error("last conflict must NOT be flagged as preceding the managed block")
	}
}

// ---------------------------------------------------------------------------
// Apply / Remove: byte-exactness across line-ending variants
// ---------------------------------------------------------------------------

func TestApplyRemove_LineEndingVariants(t *testing.T) {
	cases := []struct {
		name    string
		content string
		wantEOL string
	}{
		{
			name:    "LF",
			content: "127.0.0.1 localhost\n::1 localhost\n",
			wantEOL: "\n",
		},
		{
			name: "CRLF (a real Windows hosts file)",
			content: "# Copyright (c) 1993-2009 Microsoft Corp.\r\n#\r\n" +
				"#\t127.0.0.1       localhost\r\n#\t::1             localhost\r\n",
			wantEOL: "\r\n",
		},
		{
			name:    "mixed, CRLF dominant",
			content: "a 1.example\r\nb 2.example\r\nc 3.example\n",
			wantEOL: "\r\n",
		},
		{
			name:    "mixed, LF dominant",
			content: "a 1.example\nb 2.example\nc 3.example\r\n",
			wantEOL: "\n",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := writeFixture(t, tc.content)

			res, err := Apply(path, testIP)
			if err != nil {
				t.Fatalf("Apply: %v", err)
			}
			if !res.Changed {
				t.Fatal("Changed = false on a first apply")
			}

			got := readFile(t, path)
			want := tc.content + block(testIP, tc.wantEOL)
			if got != want {
				t.Fatalf("applied content mismatch.\ngot:  %q\nwant: %q", got, want)
			}
			// The prefix - i.e. the whole original file - must be byte-exact.
			if !strings.HasPrefix(got, tc.content) {
				t.Fatal("the original content was not preserved byte-exactly")
			}

			// ...and removing it again restores the original exactly.
			if _, err := Remove(path); err != nil {
				t.Fatalf("Remove: %v", err)
			}
			assertUnchanged(t, path, tc.content)
		})
	}
}

func TestApply_NoTrailingNewlineGetsOneAndDocumentedRoundTrip(t *testing.T) {
	const content = "127.0.0.1 localhost" // no trailing newline
	path := writeFixture(t, content)

	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	eol := "\n"
	if runtime.GOOS == "windows" {
		eol = "\r\n"
	}
	want := content + eol + block(testIP, eol)
	if got := readFile(t, path); got != want {
		t.Fatalf("got %q, want %q", readFile(t, path), want)
	}

	// Documented deviation: the terminator inserted before the block cannot
	// be told apart from one the file always had, so Remove leaves it.
	// This is THE one non-byte-exact round trip, and it is a strict
	// improvement (a hosts file should end with a newline).
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if got := readFile(t, path); got != content+eol {
		t.Fatalf("after round trip got %q, want %q (the documented single-newline normalization)",
			got, content+eol)
	}
}

func TestApply_EmptyFile(t *testing.T) {
	path := writeFixture(t, "")
	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	eol := "\n"
	if runtime.GOOS == "windows" {
		eol = "\r\n"
	}
	if got, want := readFile(t, path), block(testIP, eol); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if got := readFile(t, path); got != "" {
		t.Fatalf("after Remove got %q, want an empty file", got)
	}
}

func TestRemove_FileWithOnlyOurBlock(t *testing.T) {
	path := writeFixture(t, block(testIP, "\n"))
	res, err := Remove(path)
	if err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if !res.Changed {
		t.Error("Changed = false")
	}
	if got := readFile(t, path); got != "" {
		t.Fatalf("got %q, want an empty file", got)
	}
}

func TestApply_BlockInTheMiddlePreservesBothHalves(t *testing.T) {
	head := "127.0.0.1 localhost\n# a comment\n"
	tail := "10.0.0.5 nas.lan\n\n# trailing comment, no newline after this one"
	content := head + block("10.9.9.9", "\n") + tail
	path := writeFixture(t, content)

	res, err := Apply(path, testIP)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if !res.Changed {
		t.Fatal("Changed = false on an IP change")
	}
	if res.Before.State != StatePresentDifferentIP {
		t.Errorf("Before.State = %q, want %q", res.Before.State, StatePresentDifferentIP)
	}

	want := head + block(testIP, "\n") + tail
	if got := readFile(t, path); got != want {
		t.Fatalf("got %q\nwant %q", got, want)
	}

	// And the block stays where it was - the tail was not re-terminated.
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if got, want := readFile(t, path), head+tail; got != want {
		t.Fatalf("after Remove got %q, want %q", got, want)
	}
}

func TestApply_BlockAtEOFWithoutTrailingNewlineKeepsIt(t *testing.T) {
	// The END marker is the last line and has NO terminator: re-applying
	// must not invent one (that is the "reuse the END line's own
	// terminator" rule).
	content := "127.0.0.1 localhost\n" + BeginMarker + "\n10.9.9.9 " + Hostname + "\n" + EndMarker
	path := writeFixture(t, content)

	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	want := "127.0.0.1 localhost\n" + BeginMarker + "\n" + testIP + " " + Hostname + "\n" + EndMarker
	if got := readFile(t, path); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestApply_IdempotentSecondApplyWritesNothing(t *testing.T) {
	path := writeFixture(t, "127.0.0.1 localhost\n")

	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("first Apply: %v", err)
	}
	after := readFile(t, path)

	// Remove the backup so we can prove the second apply creates no new one.
	if err := os.Remove(path + BackupSuffix); err != nil {
		t.Fatalf("removing backup: %v", err)
	}

	res, err := Apply(path, testIP)
	if err != nil {
		t.Fatalf("second Apply: %v", err)
	}
	if res.Changed {
		t.Error("Changed = true on a no-op re-apply")
	}
	if res.Method != "" || res.BackupPath != "" {
		t.Errorf("a no-op apply reported Method=%q BackupPath=%q, want both empty", res.Method, res.BackupPath)
	}
	assertUnchanged(t, path, after)
	if _, err := os.Stat(path + BackupSuffix); err == nil {
		t.Error("a no-op apply wrote a backup")
	}
}

func TestApply_ReApplyNormalizesNonCanonicalFormatting(t *testing.T) {
	content := BeginMarker + "\n" + testIP + "   " + Hostname + "\n" + EndMarker + "\n"
	path := writeFixture(t, content)

	res, err := Apply(path, testIP)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if !res.Changed {
		t.Fatal("Changed = false, want the formatting to be normalized")
	}
	if got := readFile(t, path); got != block(testIP, "\n") {
		t.Fatalf("got %q, want the canonical block", got)
	}
}

func TestRemove_IdempotentWhenAbsent(t *testing.T) {
	const content = "127.0.0.1 localhost\n"
	path := writeFixture(t, content)

	res, err := Remove(path)
	if err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if res.Changed {
		t.Error("Changed = true with no block present")
	}
	assertUnchanged(t, path, content)
	if _, err := os.Stat(path + BackupSuffix); err == nil {
		t.Error("a no-op remove wrote a backup")
	}
}

func TestRemove_MissingFileIsANoOp(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nope")
	res, err := Remove(path)
	if err != nil {
		t.Fatalf("Remove on a missing file: %v", err)
	}
	if res.Changed {
		t.Error("Changed = true")
	}
}

func TestApply_RefusesToCreateAMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "typo-hosts")
	if _, err := Apply(path, testIP); err == nil {
		t.Fatal("Apply created a hosts file that did not exist; want a loud refusal")
	}
	if _, err := os.Stat(path); err == nil {
		t.Fatal("Apply created the file anyway")
	}
}

// utf16 encodes ASCII text as UTF-16, optionally with a byte-order mark.
// Hand-rolled rather than pulled from x/text: the point is to produce the
// exact byte pattern Notepad's "Unicode" save option writes.
func utf16(s string, bigEndian, bom bool) []byte {
	var out []byte
	if bom {
		if bigEndian {
			out = append(out, 0xFE, 0xFF)
		} else {
			out = append(out, 0xFF, 0xFE)
		}
	}
	for _, r := range s {
		lo, hi := byte(r&0xFF), byte(r>>8)
		if bigEndian {
			out = append(out, hi, lo)
		} else {
			out = append(out, lo, hi)
		}
	}
	return out
}

// TestRefusesNonPlainTextEncodings pins the guarantee that a UTF-16/32 hosts
// file is refused rather than misread. The fixtures deliberately contain a
// CONFLICTING lancache entry: byte-oriented conflict detection is blind
// through the interleaved NULs, so without this guard Apply would append a
// UTF-8 block to a UTF-16 file and report "present-correct" for something
// the resolver ignores entirely.
func TestRefusesNonPlainTextEncodings(t *testing.T) {
	const plain = "127.0.0.1 localhost\r\n10.0.0.7 " + Hostname + "\r\n"

	cases := map[string][]byte{
		"UTF-16LE with BOM":    utf16(plain, false, true),
		"UTF-16BE with BOM":    utf16(plain, true, true),
		"UTF-16LE without BOM": utf16(plain, false, false),
		"UTF-32LE with BOM":    append([]byte{0xFF, 0xFE, 0x00, 0x00}, 0x41, 0x00, 0x00, 0x00),
		"UTF-32BE with BOM":    append([]byte{0x00, 0x00, 0xFE, 0xFF}, 0x00, 0x00, 0x00, 0x41),
		"stray NUL in plain text": append([]byte("127.0.0.1 localhost\n"),
			append([]byte{0x00}, []byte("10.0.0.7 evil.example\n")...)...),
	}

	for name, content := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "hosts")
			if err := os.WriteFile(path, content, 0o644); err != nil {
				t.Fatalf("writing fixture: %v", err)
			}
			before := readFile(t, path)

			for _, op := range []struct {
				name string
				run  func() error
			}{
				{"Verify", func() error { _, err := Verify(path, testIP); return err }},
				{"Apply", func() error { _, err := Apply(path, testIP); return err }},
				{"Remove", func() error { _, err := Remove(path); return err }},
			} {
				err := op.run()
				if err == nil {
					t.Errorf("%s accepted a non-plain-text hosts file", op.name)
					continue
				}
				if !strings.Contains(err.Error(), "plain-text hosts file") {
					t.Errorf("%s error = %q, want it to name the encoding problem", op.name, err)
				}
				if !strings.Contains(err.Error(), "convert it to UTF-8") {
					t.Errorf("%s error = %q, want an actionable conversion hint", op.name, err)
				}
			}

			assertUnchanged(t, path, before)
			if _, serr := os.Stat(path + BackupSuffix); serr == nil {
				t.Error("a backup was written for a file we refused to read")
			}
		})
	}
}

func TestRefusesNonPlainTextEncodings_UTF8BOMIsStillFine(t *testing.T) {
	// A UTF-8 BOM is one harmless prefix, not an encoding change: it must NOT
	// be swept up by the refusal above.
	content := "\xEF\xBB\xBF# a comment\n127.0.0.1 localhost\n"
	path := writeFixture(t, content)
	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply rejected a UTF-8 BOM: %v", err)
	}
	if !strings.HasPrefix(readFile(t, path), content) {
		t.Error("the BOM'd prefix was not preserved byte-exactly")
	}
}

func TestRefusesSymlinkedHostsPath(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("creating symlinks on Windows needs SeCreateSymbolicLinkPrivilege")
	}
	dir := t.TempDir()
	target := filepath.Join(dir, "real-hosts")
	const content = "127.0.0.1 localhost\n"
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatalf("writing target: %v", err)
	}
	link := filepath.Join(dir, "hosts")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("cannot create a symlink here: %v", err)
	}

	for _, op := range []struct {
		name string
		run  func() error
	}{
		{"Verify", func() error { _, err := Verify(link, testIP); return err }},
		{"Apply", func() error { _, err := Apply(link, testIP); return err }},
		{"Remove", func() error { _, err := Remove(link); return err }},
	} {
		err := op.run()
		if err == nil {
			t.Errorf("%s followed a symlinked hosts path", op.name)
			continue
		}
		if !strings.Contains(err.Error(), "symbolic link") {
			t.Errorf("%s error = %q, want it to name the symlink", op.name, err)
		}
	}

	// The link must still BE a link, and its target untouched: an atomic
	// rename here would have replaced the link with a regular file.
	info, err := os.Lstat(link)
	if err != nil {
		t.Fatalf("lstat: %v", err)
	}
	if info.Mode()&os.ModeSymlink == 0 {
		t.Fatal("the symlink was replaced by a regular file")
	}
	assertUnchanged(t, target, content)
	if _, serr := os.Stat(link + BackupSuffix); serr == nil {
		t.Error("a backup was written for a path we refused to touch")
	}
}

func TestOperationsOnADirectoryGiveAnActionableError(t *testing.T) {
	dir := t.TempDir()
	for _, tc := range []struct {
		name string
		run  func() error
	}{
		{"Verify", func() error { _, err := Verify(dir, testIP); return err }},
		{"Apply", func() error { _, err := Apply(dir, testIP); return err }},
		{"Remove", func() error { _, err := Remove(dir); return err }},
	} {
		err := tc.run()
		if err == nil {
			t.Errorf("%s on a directory returned no error", tc.name)
			continue
		}
		if !strings.Contains(err.Error(), "is a directory") {
			t.Errorf("%s error = %q, want it to say the path is a directory", tc.name, err)
		}
	}
}

func TestApply_UnrelatedEntriesSurviveByteExact(t *testing.T) {
	// Deliberately awkward content: tabs, double spaces, a blank line, a
	// trailing-comment entry, and a non-ASCII comment.
	content := "127.0.0.1\tlocalhost\n" +
		"\n" +
		"10.0.0.5   nas.lan  media.lan   # Wohnzimmer-NAS (Umlaute: äöü)\n" +
		"192.168.1.1 router.lan\n"
	path := writeFixture(t, content)
	before := sha(content)

	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if got := sha(readFile(t, path)); got != before {
		t.Fatalf("round trip changed the file: sha %s -> %s", before, got)
	}
}

// ---------------------------------------------------------------------------
// Backups
// ---------------------------------------------------------------------------

func TestMutations_WriteABackupOfThePreMutationBytes(t *testing.T) {
	const content = "127.0.0.1 localhost\n"
	path := writeFixture(t, content)

	res, err := Apply(path, testIP)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if res.BackupPath != path+BackupSuffix {
		t.Errorf("BackupPath = %q, want %q", res.BackupPath, path+BackupSuffix)
	}
	if got := readFile(t, res.BackupPath); got != content {
		t.Fatalf("backup = %q, want the pre-apply bytes %q", got, content)
	}
	if res.Method != MethodRename {
		t.Errorf("Method = %q, want %q on an ordinary writable file", res.Method, MethodRename)
	}

	applied := readFile(t, path)
	if _, err := Remove(path); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if got := readFile(t, path+BackupSuffix); got != applied {
		t.Fatalf("backup after Remove = %q, want the pre-remove bytes %q", got, applied)
	}
}

// setupBackupImpossible builds the configuration that separates "the backup
// failed" from "everything failed": a hosts file that IS writable in place,
// inside a directory that rejects NEW files. os.CreateTemp and the
// <path>.steamhangar.bak both need to create a file in that directory and
// cannot; the hosts file itself can still be truncated and rewritten.
//
// Without mutate()'s abort-on-backup-failure, this is precisely the shape
// that silently modifies a system file and leaves no undo copy.
func setupBackupImpossible(t *testing.T, content string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX directory mode")
	}
	if os.Geteuid() == 0 {
		t.Skip("running as root: permission bits are not enforced")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	if err := os.WriteFile(path, []byte(content), 0o666); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	// r-xr-xr-x: no new entries may be created in this directory.
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	// Runs before t.TempDir's own cleanup (t.Cleanup is LIFO), so the
	// directory is removable again by the time the framework tries.
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	// The precondition this whole test rests on: the TARGET is writable.
	// Opened without O_TRUNC so checking it does not modify it. If this ever
	// stops holding, the tests below would pass for the wrong reason.
	f, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		t.Fatalf("precondition failed: the hosts file must still be writable in place "+
			"for this test to prove anything, got %v", err)
	}
	f.Close()
	return path
}

func TestApply_RefusesWhenTheBackupCannotBeWritten(t *testing.T) {
	const content = "127.0.0.1 localhost\n"
	path := setupBackupImpossible(t, content)

	res, err := Apply(path, testIP)
	if err == nil {
		t.Fatal("Apply modified the hosts file even though no backup could be written — " +
			"the file would have been changed with no undo copy")
	}
	if !strings.Contains(err.Error(), "backup") {
		t.Errorf("error = %q, want it to name the backup as the reason", err)
	}
	if !strings.Contains(err.Error(), "No change was made") {
		t.Errorf("error = %q, want it to state that nothing changed", err)
	}
	if res.Changed {
		t.Error("Changed = true although the operation was refused")
	}
	assertUnchanged(t, path, content)
	if _, serr := os.Stat(path + BackupSuffix); serr == nil {
		t.Error("a backup file exists after a refused apply")
	}
}

func TestRemove_RefusesWhenTheBackupCannotBeWritten(t *testing.T) {
	content := "127.0.0.1 localhost\n" + block(testIP, "\n")
	path := setupBackupImpossible(t, content)

	res, err := Remove(path)
	if err == nil {
		t.Fatal("Remove modified the hosts file even though no backup could be written")
	}
	if !strings.Contains(err.Error(), "backup") {
		t.Errorf("error = %q, want it to name the backup as the reason", err)
	}
	if res.Changed {
		t.Error("Changed = true although the operation was refused")
	}
	assertUnchanged(t, path, content)
	if _, serr := os.Stat(path + BackupSuffix); serr == nil {
		t.Error("a backup file exists after a refused remove")
	}
}

// ---------------------------------------------------------------------------
// Permission bits (Unix): the /etc/hosts-becomes-0600 trap
// ---------------------------------------------------------------------------

func TestApply_PreservesFileMode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix permission bits")
	}
	path := writeFixture(t, "127.0.0.1 localhost\n")
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	if _, err := Apply(path, testIP); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if got := info.Mode().Perm(); got != 0o644 {
		t.Fatalf("mode = %04o, want 0644 (os.CreateTemp's 0600 must not land on the hosts file)", got)
	}
}

// ---------------------------------------------------------------------------
// The in-place fallback (see writeFile's doc comment for the Windows
// evidence this mirrors on Linux)
// ---------------------------------------------------------------------------

func TestWriteFile_FallsBackToInPlaceWhenTheDirectoryRejectsTempFiles(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX directory mode; the Windows fallback path is evidenced in writeFile's doc comment")
	}
	if os.Geteuid() == 0 {
		t.Skip("running as root: permission bits are not enforced")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	if err := os.WriteFile(path, []byte("original\n"), 0o644); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	// r-xr-xr-x: no new files may be created here, but the existing file
	// is still writable - exactly the shape that forces the fallback.
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	method, truncated, err := writeFile(path, []byte("replaced\n"), 0o644)
	if err != nil {
		t.Fatalf("writeFile: %v", err)
	}
	if method != MethodInPlace {
		t.Errorf("method = %q, want %q", method, MethodInPlace)
	}
	if truncated {
		t.Error("truncated = true on a SUCCESSFUL in-place write; it must only flag a failed, half-written one")
	}
	if got := readFile(t, path); got != "replaced\n" {
		t.Errorf("content = %q, want %q", got, "replaced\n")
	}
}

func TestWriteFile_PermissionErrorIsDetectable(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	if os.Geteuid() == 0 {
		t.Skip("running as root: permission bits are not enforced")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	if err := os.WriteFile(path, []byte("original\n"), 0o444); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	_, truncated, err := writeFile(path, []byte("replaced\n"), 0o444)
	if err == nil {
		t.Fatal("writeFile succeeded against a read-only file in a read-only directory")
	}
	if truncated {
		t.Error("truncated = true although the in-place open never succeeded; " +
			"the caller would wrongly warn that the file may be half-written")
	}
	if !IsPermissionDenied(err) {
		t.Fatalf("IsPermissionDenied(%v) = false, want true", err)
	}
	if got := readFile(t, path); got != "original\n" {
		t.Fatalf("content = %q, want the file untouched", got)
	}
}

// TestWriteFile_ReportsAHalfWrittenFile is the ENOSPC case: the in-place
// fallback opens the target successfully (O_TRUNC has already emptied it) and
// only THEN fails on the write. writeFile must report truncated=true so the
// caller warns the user to restore from the backup instead of telling them
// nothing happened.
//
// /dev/full is the portable way to get a genuine ENOSPC without building a
// full filesystem: every write to it fails with ENOSPC, while open and
// truncate succeed. Nothing is modified by this test — /dev/full has no
// contents. Non-root only: as root, CreateTemp in /dev would SUCCEED and the
// rename would then replace the device node, which must never happen.
func TestWriteFile_ReportsAHalfWrittenFile(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("/dev/full is a Linux thing")
	}
	if os.Geteuid() == 0 {
		t.Skip("running as root: the temp file in /dev would be created and renamed over /dev/full")
	}
	if _, err := os.Stat("/dev/full"); err != nil {
		t.Skipf("/dev/full unavailable: %v", err)
	}

	method, truncated, err := writeFile("/dev/full", []byte("anything"), 0o644)
	if err == nil {
		t.Fatal("writeFile to /dev/full succeeded; expected ENOSPC")
	}
	if method != "" {
		t.Errorf("method = %q, want empty on failure", method)
	}
	if !truncated {
		t.Fatal("truncated = false although the in-place open succeeded and the write then failed — " +
			"the caller would wrongly report the file as unmodified")
	}

	// ...and the message the caller builds from it must say so.
	msg := writeFailureMessage("/etc/hosts", "/etc/hosts"+BackupSuffix, truncated, err).Error()
	if !strings.Contains(msg, "may be partially written") {
		t.Errorf("message = %q, want the partial-write warning", msg)
	}
}

func TestWriteFailureMessage_BothBranches(t *testing.T) {
	boom := errors.New("boom")

	intact := writeFailureMessage("/etc/hosts", "/etc/hosts.bak", false, boom).Error()
	if !strings.Contains(intact, "NOT modified") {
		t.Errorf("intact message = %q, want it to state the file was not modified", intact)
	}
	if strings.Contains(intact, "partially written") {
		t.Errorf("intact message = %q, must not warn about a partial write", intact)
	}

	damaged := writeFailureMessage("/etc/hosts", "/etc/hosts.bak", true, boom).Error()
	if !strings.Contains(damaged, "may be partially written") {
		t.Errorf("damaged message = %q, want the partial-write warning", damaged)
	}
	if !strings.Contains(damaged, "restore it by copying /etc/hosts.bak") {
		t.Errorf("damaged message = %q, want the exact recovery instruction", damaged)
	}
	if strings.Contains(damaged, "NOT modified") {
		t.Errorf("damaged message = %q, must not claim the file is unmodified", damaged)
	}
}

func TestApply_PermissionDeniedLeavesTheFileUntouched(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	if os.Geteuid() == 0 {
		t.Skip("running as root: permission bits are not enforced")
	}
	const content = "127.0.0.1 localhost\n"
	dir := t.TempDir()
	path := filepath.Join(dir, "hosts")
	if err := os.WriteFile(path, []byte(content), 0o444); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	res, err := Apply(path, testIP)
	if err == nil {
		t.Fatal("Apply succeeded without write permission")
	}
	if !IsPermissionDenied(err) {
		t.Fatalf("IsPermissionDenied(%v) = false, want true so the CLI prints the elevation hint", err)
	}
	if res.Changed {
		t.Error("Changed = true on a failed apply")
	}
	assertUnchanged(t, path, content)
}

// ---------------------------------------------------------------------------
// ValidateCacheIP / DefaultPath / ElevationHint
// ---------------------------------------------------------------------------

func TestValidateCacheIP(t *testing.T) {
	valid := []string{"192.168.1.50", "10.0.0.1", "127.0.0.1", "1.2.3.4", "255.255.255.254"}
	for _, s := range valid {
		if err := ValidateCacheIP(s); err != nil {
			t.Errorf("ValidateCacheIP(%q) = %v, want nil", s, err)
		}
	}

	invalid := map[string]string{
		"empty":            "",
		"leading space":    " 192.168.1.50",
		"trailing space":   "192.168.1.50 ",
		"leading zeros":    "010.1.1.1",
		"three octets":     "192.168.1",
		"five octets":      "1.2.3.4.5",
		"octet too large":  "192.168.1.256",
		"hostname":         "cache.lan",
		"IPv6":             "2001:db8::1",
		"IPv4-mapped IPv6": "::ffff:192.168.1.50",
		"with port":        "192.168.1.50:80",
		"CIDR":             "192.168.1.0/24",
		"unspecified":      "0.0.0.0",
		"multicast":        "224.0.0.1",
		"broadcast":        "255.255.255.255",
		"embedded newline": "192.168.1.50\n10.0.0.1 evil.example",
		"embedded CR":      "192.168.1.50\r",
		"tab":              "192.168.1.50\t",
	}
	for name, s := range invalid {
		if err := ValidateCacheIP(s); err == nil {
			t.Errorf("%s: ValidateCacheIP(%q) = nil, want an error", name, s)
		}
	}
}

func TestApply_RejectsAnInvalidCacheIPBeforeTouchingTheFile(t *testing.T) {
	const content = "127.0.0.1 localhost\n"
	path := writeFixture(t, content)
	if _, err := Apply(path, "192.168.1.50\n10.0.0.1 evil.example"); err == nil {
		t.Fatal("Apply accepted an address containing a newline - that would inject a hosts entry")
	}
	assertUnchanged(t, path, content)
}

func TestDefaultPathFor(t *testing.T) {
	cases := []struct {
		goos, systemRoot, want string
	}{
		{"windows", `C:\Windows`, `C:\Windows\System32\drivers\etc\hosts`},
		{"windows", `D:\WINNT\`, `D:\WINNT\System32\drivers\etc\hosts`},
		{"windows", "", `C:\Windows\System32\drivers\etc\hosts`},
		{"linux", "", "/etc/hosts"},
		{"darwin", "", "/etc/hosts"},
	}
	for _, tc := range cases {
		got := defaultPathFor(tc.goos, func(string) string { return tc.systemRoot })
		if got != tc.want {
			t.Errorf("defaultPathFor(%q, SystemRoot=%q) = %q, want %q",
				tc.goos, tc.systemRoot, got, tc.want)
		}
	}
}

func TestElevationHint_NamesThePlatformsElevationPath(t *testing.T) {
	const cmd = "vault-agent hosts apply --cache-ip 192.168.1.50"

	win := elevationHintFor("windows", cmd)
	if !strings.Contains(win, "Administrator") || !strings.Contains(win, cmd) {
		t.Errorf("windows hint = %q, want it to mention Administrator and the exact command", win)
	}
	if strings.Contains(win, "sudo") {
		t.Errorf("windows hint mentions sudo: %q", win)
	}

	nix := elevationHintFor("linux", cmd)
	if !strings.Contains(nix, "sudo "+cmd) {
		t.Errorf("linux hint = %q, want it to show `sudo <command>`", nix)
	}
}

// ---------------------------------------------------------------------------
// Line splitting / EOL detection (the byte-exactness foundation)
// ---------------------------------------------------------------------------

func TestSplitLines(t *testing.T) {
	cases := []struct {
		in    string
		texts []string
		eols  []string
	}{
		{"", nil, nil},
		{"a", []string{"a"}, []string{""}},
		{"a\n", []string{"a"}, []string{"\n"}},
		{"a\r\n", []string{"a"}, []string{"\r\n"}},
		{"a\nb", []string{"a", "b"}, []string{"\n", ""}},
		{"\n", []string{""}, []string{"\n"}},
		{"a\r\nb\n", []string{"a", "b"}, []string{"\r\n", "\n"}},
		{"a\rb\n", []string{"a\rb"}, []string{"\n"}}, // lone CR is content
	}
	for _, tc := range cases {
		lines := splitLines([]byte(tc.in))
		if len(lines) != len(tc.texts) {
			t.Errorf("splitLines(%q) returned %d lines, want %d", tc.in, len(lines), len(tc.texts))
			continue
		}
		for i, l := range lines {
			if l.text != tc.texts[i] || l.eol != tc.eols[i] {
				t.Errorf("splitLines(%q)[%d] = {text:%q eol:%q}, want {text:%q eol:%q}",
					tc.in, i, l.text, l.eol, tc.texts[i], tc.eols[i])
			}
		}
		// Offsets must reconstruct the input exactly.
		var rebuilt strings.Builder
		for _, l := range lines {
			rebuilt.Write([]byte(tc.in)[l.start:l.end])
		}
		if rebuilt.String() != tc.in {
			t.Errorf("offsets do not reconstruct %q, got %q", tc.in, rebuilt.String())
		}
	}
}

func TestDominantEOL(t *testing.T) {
	cases := []struct {
		in   string
		goos string
		want string
	}{
		{"a\nb\n", "linux", "\n"},
		{"a\r\nb\r\n", "linux", "\r\n"},
		{"a\r\nb\n", "linux", "\r\n"}, // tie -> CRLF
		{"a\r\nb\nc\n", "linux", "\n"},
		{"", "windows", "\r\n"},
		{"", "linux", "\n"},
		{"no newline at all", "windows", "\r\n"},
	}
	for _, tc := range cases {
		if got := dominantEOL(splitLines([]byte(tc.in)), tc.goos); got != tc.want {
			t.Errorf("dominantEOL(%q, %s) = %q, want %q", tc.in, tc.goos, got, tc.want)
		}
	}
}
