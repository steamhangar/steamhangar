// End-to-end tests for DiscoverInstalled against a synthetic multi-
// library tmp tree, plus resilience cases (corrupt files, duplicate
// appids, missing directories). Ported 1:1 from
// agent/tests/test_discover.py.
//
// Go has no logging.warning/caplog equivalent (see discover.go's Warning
// doc comment) — every case that asserted "a warning was logged
// containing X" now asserts "a Warning in the returned slice contains
// X".
package acf

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

const modernAppmanifestTemplate = "\"AppState\"\n{\n\t\"appid\"\t\t\"%s\"\n\t\"name\"\t\t\"%s\"\n" +
	"\t\"StateFlags\"\t\t\"%d\"\n\t\"SizeOnDisk\"\t\t\"%d\"\n}\n"

func writeManifest(t *testing.T, steamappsDir, appid, name string, stateFlags, size int) {
	t.Helper()
	if err := os.MkdirAll(steamappsDir, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", steamappsDir, err)
	}
	content := fmt.Sprintf(modernAppmanifestTemplate, appid, name, stateFlags, size)
	path := filepath.Join(steamappsDir, "appmanifest_"+appid+".acf")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}

func vdfEscape(path string) string {
	return strings.ReplaceAll(path, `\`, `\\`)
}

func writeLibraryFolders(t *testing.T, mainSteamapps, mainPath string, extraPaths []string) {
	t.Helper()
	if err := os.MkdirAll(mainSteamapps, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", mainSteamapps, err)
	}
	blocks := []string{fmt.Sprintf("\t\"0\"\n\t{\n\t\t\"path\"\t\t\"%s\"\n\t}", vdfEscape(mainPath))}
	for i, p := range extraPaths {
		blocks = append(blocks, fmt.Sprintf("\t\"%d\"\n\t{\n\t\t\"path\"\t\t\"%s\"\n\t}", i+1, vdfEscape(p)))
	}
	content := "\"libraryfolders\"\n{\n" + strings.Join(blocks, "\n") + "\n}\n"
	path := filepath.Join(mainSteamapps, "libraryfolders.vdf")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}

func anyWarningContains(warnings []Warning, substr string) bool {
	for _, w := range warnings {
		if strings.Contains(w.Message, substr) {
			return true
		}
	}
	return false
}

func appIDSet(apps []InstalledApp) map[string]bool {
	set := make(map[string]bool, len(apps))
	for _, a := range apps {
		set[a.AppID] = true
	}
	return set
}

func TestDiscoverEndToEndMultiLibrary(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	secondLib := filepath.Join(tmp, "second")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	secondSteamapps := filepath.Join(secondLib, "steamapps")

	writeLibraryFolders(t, mainSteamapps, mainLib, []string{secondLib})
	writeManifest(t, mainSteamapps, "100", "Game One", 4, 111)
	writeManifest(t, mainSteamapps, "200", "Game Two", 4, 222)
	writeManifest(t, secondSteamapps, "300", "Game Three", 6, 333)

	apps, _ := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	want := map[string]bool{"100": true, "200": true, "300": true}
	if len(got) != len(want) {
		t.Fatalf("appids = %v, want %v", got, want)
	}
	for id := range want {
		if !got[id] {
			t.Fatalf("appids = %v, want %v", got, want)
		}
	}

	byID := make(map[string]InstalledApp, len(apps))
	for _, a := range apps {
		byID[a.AppID] = a
	}
	if byID["100"].LibraryPath != mainLib {
		t.Errorf("app 100 LibraryPath = %q, want %q", byID["100"].LibraryPath, mainLib)
	}
	if byID["300"].LibraryPath != secondLib {
		t.Errorf("app 300 LibraryPath = %q, want %q", byID["300"].LibraryPath, secondLib)
	}
	if !byID["300"].Installed() {
		t.Error("app 300 Installed() = false, want true (StateFlags 6 still has the installed bit)")
	}
}

func TestDiscoverSkipsCorruptManifestAndWarns(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeLibraryFolders(t, mainSteamapps, mainLib, nil)
	writeManifest(t, mainSteamapps, "100", "Good Game", 4, 111)

	corrupt := filepath.Join(mainSteamapps, "appmanifest_999.acf")
	if err := os.WriteFile(corrupt, []byte(`"AppState" { "appid" "999" "name" "Broken`), 0o644); err != nil {
		t.Fatalf("failed to write corrupt fixture: %v", err)
	}

	apps, warnings := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100}", got)
	}
	if !anyWarningContains(warnings, "999") {
		t.Fatalf("warnings = %v, want one mentioning 999", warnings)
	}
}

func TestDiscoverDuplicateAppidFirstWinsAndWarns(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	secondLib := filepath.Join(tmp, "second")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	secondSteamapps := filepath.Join(secondLib, "steamapps")

	writeLibraryFolders(t, mainSteamapps, mainLib, []string{secondLib})
	writeManifest(t, mainSteamapps, "100", "Original", 4, 111)
	writeManifest(t, secondSteamapps, "100", "Duplicate", 4, 999)

	apps, warnings := DiscoverInstalled(mainLib)

	if len(apps) != 1 {
		t.Fatalf("len(apps) = %d, want 1", len(apps))
	}
	if apps[0].Name != "Original" {
		t.Errorf("apps[0].Name = %q, want Original", apps[0].Name)
	}
	if apps[0].LibraryPath != mainLib {
		t.Errorf("apps[0].LibraryPath = %q, want %q", apps[0].LibraryPath, mainLib)
	}
	if !anyWarningContains(warnings, "duplicate appid") {
		t.Fatalf("warnings = %v, want one mentioning 'duplicate appid'", warnings)
	}
}

func TestDiscoverToleratesMissingLibraryDirectory(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	missingLib := filepath.Join(tmp, "does_not_exist_on_disk")
	mainSteamapps := filepath.Join(mainLib, "steamapps")

	writeLibraryFolders(t, mainSteamapps, mainLib, []string{missingLib})
	writeManifest(t, mainSteamapps, "100", "Good Game", 4, 111)

	apps, warnings := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100}", got)
	}
	if !anyWarningContains(warnings, missingLib) {
		t.Fatalf("warnings = %v, want one mentioning %q", warnings, missingLib)
	}
}

func TestDiscoverFallsBackWhenLibraryfoldersMissing(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	if err := os.MkdirAll(mainSteamapps, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", mainSteamapps, err)
	}
	writeManifest(t, mainSteamapps, "100", "Solo Game", 4, 111)
	// Deliberately no libraryfolders.vdf written at all.

	apps, warnings := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100}", got)
	}
	if !anyWarningContains(warnings, "libraryfolders.vdf") {
		t.Fatalf("warnings = %v, want one mentioning 'libraryfolders.vdf'", warnings)
	}
}

func TestDiscoverFallsBackWhenLibraryfoldersCorrupt(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	if err := os.MkdirAll(mainSteamapps, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", mainSteamapps, err)
	}
	corruptContent := `"libraryfolders" { "0" { "path" "unterminated`
	if err := os.WriteFile(filepath.Join(mainSteamapps, "libraryfolders.vdf"), []byte(corruptContent), 0o644); err != nil {
		t.Fatalf("failed to write corrupt libraryfolders.vdf: %v", err)
	}
	writeManifest(t, mainSteamapps, "100", "Solo Game", 4, 111)

	apps, _ := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100}", got)
	}
}

func TestDiscoverOnCompletelyEmptyRootReturnsEmptyList(t *testing.T) {
	emptyRoot := filepath.Join(t.TempDir(), "empty")
	if err := os.MkdirAll(emptyRoot, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", emptyRoot, err)
	}
	apps, _ := DiscoverInstalled(emptyRoot)
	if len(apps) != 0 {
		t.Fatalf("apps = %v, want empty", apps)
	}
}

// TestDiscoverSkipsInvalidUTF8ManifestAndWarns: discover-level proof of
// the UTF-8 BLOCKER fix (see appmanifest_test.go's
// TestInvalidUTF8AppmanifestFileRaisesParseError) — a manifest with a raw
// 0xE9 byte must be skipped (not silently mojibake-mangled into the
// result) and must produce a warning, exactly like any other corrupt
// manifest.
func TestDiscoverSkipsInvalidUTF8ManifestAndWarns(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeLibraryFolders(t, mainSteamapps, mainLib, nil)
	writeManifest(t, mainSteamapps, "100", "Good Game", 4, 111)

	badContent := []byte("\"AppState\"\n{\n\t\"appid\"\t\t\"999\"\n\t\"name\"\t\t\"Pok\xe9mon Cafe\"\n" +
		"\t\"StateFlags\"\t\t\"4\"\n}\n")
	if err := os.WriteFile(filepath.Join(mainSteamapps, "appmanifest_999.acf"), badContent, 0o644); err != nil {
		t.Fatalf("failed to write invalid-UTF-8 fixture: %v", err)
	}

	apps, warnings := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100} (the invalid-UTF-8 manifest must be skipped, not mangled in)", got)
	}
	if !anyWarningContains(warnings, "999") {
		t.Fatalf("warnings = %v, want one mentioning the skipped manifest (999)", warnings)
	}
}

// --------------------------------------------------------------------
// Glob-vs-ReadDir regression tests (S1, reviewer re-review): pin the
// filepath.Glob -> os.ReadDir fix in discover.go. Mutation-tested once
// during review: reverting discover.go's directory-listing block back to
// filepath.Glob made BOTH of these tests fail (0 apps found instead of
// 1), confirming they actually catch the regression; restoring the fix
// makes them green again. Not left as a toggle in the shipped code —
// this is a one-time verification, per LEARNINGS.md's mutation-testing
// discipline.
// --------------------------------------------------------------------

func TestDiscoverFindsManifestsWhenLibraryPathContainsGlobMetacharacters(t *testing.T) {
	// "lib [beta] one" contains '[' ']' -- filepath.Glob treats those as
	// character-class syntax on EVERY platform (unlike the backslash
	// quirk below, which is POSIX-only), and would try to match the
	// literal multi-character directory name as a one-character class,
	// never succeeding. A plain directory listing has no such trap.
	mainLib := filepath.Join(t.TempDir(), "lib [beta] one")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeManifest(t, mainSteamapps, "100", "Bracket Game", 4, 111)
	// Deliberately no libraryfolders.vdf: falls back to treating mainLib
	// itself as the only library, exercising the same steamappsDir
	// listing a real libraryfolders.vdf entry would go through.

	apps, warnings := DiscoverInstalled(mainLib)

	if len(apps) != 1 || apps[0].AppID != "100" {
		t.Fatalf("apps = %+v, want exactly appid 100", apps)
	}
	if anyWarningContains(warnings, "no steamapps directory") {
		t.Fatalf("unexpected warning: %v", warnings)
	}
}

func TestDiscoverFindsManifestsWhenLibraryPathContainsLiteralBackslash(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("a literal backslash is a path separator on Windows and cannot appear inside a single path component there")
	}
	// A literal backslash in a path component (e.g. a Windows-style
	// libraryfolders.vdf path surfacing on a non-Windows GOOS, see
	// discover.go's doc comment) is an escape metacharacter to
	// filepath.Glob on non-Windows, making it silently match nothing.
	mainLib := filepath.Join(t.TempDir(), `weird\name`)
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeManifest(t, mainSteamapps, "200", "Backslash Game", 4, 222)

	apps, warnings := DiscoverInstalled(mainLib)

	if len(apps) != 1 || apps[0].AppID != "200" {
		t.Fatalf("apps = %+v, want exactly appid 200", apps)
	}
	if anyWarningContains(warnings, "no steamapps directory") {
		t.Fatalf("unexpected warning: %v", warnings)
	}
}

// TestDiscoverFindsManifestWithUppercaseExtensionCaseInsensitively (S2):
// Windows filesystems are case-insensitive and Windows is the primary
// ADR-0005 production target; a manifest named with different casing
// (some third-party tool, or a manual copy/rename) must still be found.
func TestDiscoverFindsManifestWithUppercaseExtensionCaseInsensitively(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	if err := os.MkdirAll(mainSteamapps, 0o755); err != nil {
		t.Fatalf("failed to create %s: %v", mainSteamapps, err)
	}
	content := fmt.Sprintf(modernAppmanifestTemplate, "100", "Cased Game", 4, 111)
	// Deliberately mixed/upper case filename, unlike writeManifest's
	// always-lowercase "appmanifest_<id>.acf".
	path := filepath.Join(mainSteamapps, "AppManifest_100.ACF")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}

	apps, _ := DiscoverInstalled(mainLib)

	got := appIDSet(apps)
	if len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100} (case-insensitive filename match)", got)
	}
}

// --------------------------------------------------------------------
// WP AGENT-FIX-1 S1: Discover must report how many libraries were
// actually READ, so "Steam not found here" and "nothing installed" stop
// looking identical (both are an empty Apps slice).
// --------------------------------------------------------------------

func TestDiscover_RootWithoutSteamappsReadsZeroLibraries(t *testing.T) {
	root := filepath.Join(t.TempDir(), "not-steam")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}

	d := Discover(root)

	if len(d.Apps) != 0 {
		t.Fatalf("Apps = %v, want empty", d.Apps)
	}
	if d.LibrariesRead != 0 {
		t.Errorf("LibrariesRead = %d, want 0 (no steamapps/ anywhere)", d.LibrariesRead)
	}
	if len(d.LibrariesProbed) != 1 || d.LibrariesProbed[0] != root {
		t.Errorf("LibrariesProbed = %v, want exactly [%q] (the fallback library)", d.LibrariesProbed, root)
	}
}

func TestDiscover_EmptyButReadableLibraryCountsAsRead(t *testing.T) {
	mainLib := filepath.Join(t.TempDir(), "main")
	if err := os.MkdirAll(filepath.Join(mainLib, "steamapps"), 0o755); err != nil {
		t.Fatal(err)
	}
	// steamapps/ exists, no libraryfolders.vdf, no manifests: Steam is
	// here, it just has nothing installed - a legitimate empty report.

	d := Discover(mainLib)

	if len(d.Apps) != 0 {
		t.Fatalf("Apps = %v, want empty", d.Apps)
	}
	if d.LibrariesRead != 1 {
		t.Errorf("LibrariesRead = %d, want 1", d.LibrariesRead)
	}
}

func TestDiscover_CountsOnlyListableLibraries(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	missingLib := filepath.Join(tmp, "unplugged-drive")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeLibraryFolders(t, mainSteamapps, mainLib, []string{missingLib})
	writeManifest(t, mainSteamapps, "100", "Game", 4, 1)

	d := Discover(mainLib)

	if d.LibrariesRead != 1 {
		t.Errorf("LibrariesRead = %d, want 1 (main readable, second missing)", d.LibrariesRead)
	}
	if len(d.LibrariesProbed) != 2 || d.LibrariesProbed[0] != mainLib || d.LibrariesProbed[1] != missingLib {
		t.Errorf("LibrariesProbed = %v, want [%q %q]", d.LibrariesProbed, mainLib, missingLib)
	}
	if len(d.Apps) != 1 {
		t.Errorf("Apps = %v, want the one app from the readable library", d.Apps)
	}
}

// --------------------------------------------------------------------
// WP AGENT-FIX-1 N3: library de-duplication compares NORMALISED paths.
// --------------------------------------------------------------------

func TestLibraryKey_NormalisesSeparatorsAndDotSegments(t *testing.T) {
	base := filepath.Join("lib", "games")
	variants := []string{
		base,
		base + string(filepath.Separator),
		filepath.Join(base, "."),
		base + string(filepath.Separator) + "." + string(filepath.Separator),
	}
	for _, goos := range []string{"linux", "windows"} {
		want := libraryKey(base, goos)
		for _, v := range variants {
			if got := libraryKey(v, goos); got != want {
				t.Errorf("goos=%s: libraryKey(%q) = %q, want %q", goos, v, got, want)
			}
		}
	}
}

func TestLibraryKey_CaseFoldsOnlyOnWindows(t *testing.T) {
	if libraryKey(`D:\Games`, "windows") != libraryKey(`d:\games`, "windows") {
		t.Error("windows: expected case-insensitive keys")
	}
	if libraryKey("/home/u/Games", "linux") == libraryKey("/home/u/games", "linux") {
		t.Error("linux: expected case-SENSITIVE keys (ext4 is case-sensitive)")
	}
}

func TestDiscoverDedupesLibraryListedTwiceWithTrailingSeparator(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	// The same library twice: once clean, once with a trailing separator
	// plus "." - byte-different, same directory.
	writeLibraryFolders(t, mainSteamapps, mainLib, []string{mainLib + string(filepath.Separator) + "."})
	writeManifest(t, mainSteamapps, "100", "Game", 4, 1)

	d := Discover(mainLib)

	if len(d.LibrariesProbed) != 1 {
		t.Errorf("LibrariesProbed = %v, want the library once", d.LibrariesProbed)
	}
	if len(d.Apps) != 1 || d.Apps[0].LibraryPath != mainLib {
		t.Errorf("Apps = %+v, want one app reported under the FIRST spelling %q", d.Apps, mainLib)
	}
	if anyWarningContains(d.Warnings, "duplicate appid") {
		t.Errorf("warnings = %v, a library listed twice must not read as duplicate apps", d.Warnings)
	}
}

// --------------------------------------------------------------------
// WP AGENT-FIX-1 N2: the documented errors.Is(err, fs.ErrNotExist)
// distinction now has a caller - a manifest that vanishes between the
// directory listing and the read gets its own warning text. A dangling
// symlink is the deterministic way to produce "listed, but unreadable
// with ENOENT" without racing a deletion.
// --------------------------------------------------------------------

func TestDiscoverWarnsVanishedManifestDistinctlyFromCorrupt(t *testing.T) {
	tmp := t.TempDir()
	mainLib := filepath.Join(tmp, "main")
	mainSteamapps := filepath.Join(mainLib, "steamapps")
	writeLibraryFolders(t, mainSteamapps, mainLib, nil)
	writeManifest(t, mainSteamapps, "100", "Good Game", 4, 111)

	dangling := filepath.Join(mainSteamapps, "appmanifest_555.acf")
	if err := os.Symlink(filepath.Join(tmp, "gone.acf"), dangling); err != nil {
		t.Skipf("cannot create a symlink on this host: %v", err)
	}
	corrupt := filepath.Join(mainSteamapps, "appmanifest_999.acf")
	if err := os.WriteFile(corrupt, []byte(`"AppState" { "appid" "999"`), 0o644); err != nil {
		t.Fatal(err)
	}

	d := Discover(mainLib)

	if got := appIDSet(d.Apps); len(got) != 1 || !got["100"] {
		t.Fatalf("appids = %v, want {100}", got)
	}
	if !anyWarningContains(d.Warnings, "appmanifest_555.acf vanished") {
		t.Errorf("warnings = %v, want the vanished-manifest wording for 555", d.Warnings)
	}
	if !anyWarningContains(d.Warnings, "skipping corrupt manifest") || !anyWarningContains(d.Warnings, "appmanifest_999.acf") {
		t.Errorf("warnings = %v, want the corrupt-manifest wording for 999", d.Warnings)
	}
	for _, w := range d.Warnings {
		if strings.Contains(w.Message, "555") && strings.Contains(w.Message, "corrupt") {
			t.Errorf("warning %q calls the vanished manifest corrupt", w.Message)
		}
	}
}
