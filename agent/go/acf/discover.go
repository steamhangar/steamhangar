package acf

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

// Warning is a non-fatal finding surfaced by DiscoverInstalled. Go has no
// equivalent of Python's ambient `logging.warning` convention, so instead
// of logging internally this package returns warnings to the caller, who
// decides how to surface them (stderr, a structured logger, a metrics
// counter, ...). See agent/README.md's "Resilience contract" table for
// the exact situation -> warning mapping this mirrors from the Python
// spec's discover_installed (agent/vault_agent/acf.py, removed at the
// Phase-2 close-out, WP 2.6 — see acf.go's package doc).
type Warning struct {
	Message string
}

func (w Warning) String() string { return w.Message }

// Discovery is Discover's full result. Apps and Warnings are exactly what
// DiscoverInstalled returns; the two library counters exist so a caller
// can tell "Steam was found and has nothing installed" apart from "no
// Steam library could be read at all" (WP AGENT-FIX-1 S1) - before this
// type existed both produced the same empty Apps slice, and a wrong
// --library-root therefore posted a legitimate-looking empty report that
// made vault-api drop every one of the client's games from the prefill
// set.
type Discovery struct {
	Apps     []InstalledApp
	Warnings []Warning

	// LibrariesProbed lists every distinct library path whose steamapps/
	// directory Discover tried to list, in discovery order - the paths
	// from libraryfolders.vdf, or just libraryRoot when that file was
	// missing/corrupt/empty. Meant for an actionable "checked X, Y, Z"
	// error message, never empty.
	LibrariesProbed []string

	// LibrariesRead is how many of LibrariesProbed had a steamapps/
	// directory that was actually listed successfully. Zero means Steam
	// was not found under libraryRoot at all (or every listed library is
	// currently unreadable) - NOT that nothing is installed.
	LibrariesRead int
}

// DiscoverInstalled discovers all installed apps across every Steam
// library. It is Discover with the library counters dropped, kept for
// callers that only need the app list.
func DiscoverInstalled(libraryRoot string) ([]InstalledApp, []Warning) {
	d := Discover(libraryRoot)
	return d.Apps, d.Warnings
}

// Discover discovers all installed apps across every Steam library and
// reports how many libraries were actually readable (see Discovery).
//
// libraryRoot is the main Steam install directory (the one that contains
// steamapps/libraryfolders.vdf — e.g. C:\Steam on Windows,
// ~/.local/share/Steam on Linux/SteamOS per ADR-0002). That file lists
// every library folder Steam knows about, including the main one itself.
//
// Tolerant by design, never returns an error for per-file problems —
// problems are reported as Warnings alongside the (possibly partial)
// result:
//   - Missing or corrupt libraryfolders.vdf falls back to treating
//     libraryRoot as the only library.
//   - Missing or corrupt appmanifest files are skipped.
//   - Missing library directories on disk are skipped.
//   - Duplicate app IDs across libraries: first one found wins, later
//     ones are skipped.
//
// Returns the list of InstalledApp in discovery order (an unreadable
// libraryRoot itself still returns an empty list and no crash, only
// warnings — mirroring the Python spec's resilience contract — with
// LibrariesRead == 0 so the caller can decide whether an empty list is
// trustworthy).
func Discover(libraryRoot string) Discovery {
	var warnings []Warning

	libraryFoldersPath := filepath.Join(libraryRoot, "steamapps", "libraryfolders.vdf")

	var libraryPaths []string
	parsed, err := ParseLibraryFoldersFile(libraryFoldersPath)
	if err != nil {
		warnings = append(warnings, Warning{fmt.Sprintf(
			"could not read/parse %s (%s); falling back to treating %s as the only library",
			libraryFoldersPath, err, libraryRoot)})
		libraryPaths = []string{libraryRoot}
	} else if len(parsed) == 0 {
		warnings = append(warnings, Warning{fmt.Sprintf(
			"%s parsed but listed no library paths; falling back to %s",
			libraryFoldersPath, libraryRoot)})
		libraryPaths = []string{libraryRoot}
	} else {
		libraryPaths = parsed
	}

	// De-duplicate while preserving order (libraryfolders.vdf shouldn't
	// list the same path twice, but tolerate it). Keyed on the NORMALISED
	// path (libraryKey), not the bytes: "D:\Games\" vs "D:\Games" or
	// "d:\games" name the same library, and a byte-exact compare used to
	// let such a pair through as two libraries, producing a "duplicate
	// appid" warning per installed game (WP AGENT-FIX-1 N3). The ORIGINAL
	// string of the first occurrence is what gets reported as
	// InstalledApp.LibraryPath - only the comparison is normalised.
	seenLibraries := map[string]bool{}
	var orderedLibraries []string
	for _, lib := range libraryPaths {
		key := libraryKey(lib, runtime.GOOS)
		if !seenLibraries[key] {
			seenLibraries[key] = true
			orderedLibraries = append(orderedLibraries, lib)
		}
	}

	var apps []InstalledApp
	seenAppIDs := map[string]string{} // appid -> library path that won
	librariesRead := 0

	for _, lib := range orderedLibraries {
		steamappsDir := filepath.Join(lib, "steamapps")

		// Deliberately os.ReadDir + manual "appmanifest_*.acf" prefix/
		// suffix matching instead of filepath.Glob: Glob applies
		// Match-style pattern parsing to EVERY segment of the joined
		// path, and on non-Windows GOOS treats '\' as an escape
		// metacharacter, and '[' '/' ']' as a character class — a library
		// path containing a literal backslash (e.g. a Windows-style path
		// surfaced while cross-testing under WSL) OR containing '[' ']'
		// (e.g. a user-chosen library folder name like "lib [beta] one")
		// silently makes Glob match nothing, no error raised. Python's
		// pathlib.Path.glob has no such trap: it only pattern-matches the
		// final path component, never earlier directory segments. A
		// plain directory listing sidesteps the whole class of bug and
		// needs no separate os.Stat "is it a directory" pre-check either
		// — a ReadDir failure is handled by the single warn-and-skip
		// branch below (which does distinguish "missing/not a directory"
		// from "permission denied" in the warning text, since those
		// call for different operator action).
		entries, readErr := os.ReadDir(steamappsDir)
		if readErr != nil {
			if errors.Is(readErr, fs.ErrPermission) {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"library path %s: permission denied reading steamapps directory, skipping", lib)})
			} else {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"library path %s has no steamapps directory, skipping", lib)})
			}
			continue
		}
		librariesRead++

		// Case-insensitive "appmanifest_*.acf" match: real Windows
		// production is the primary target (ADR-0005), and Windows
		// filesystems are case-insensitive — a manifest legitimately
		// named e.g. "AppManifest_100.ACF" (some third-party tool or a
		// manual copy/rename) must still be found. Python's reference
		// implementation gets this for free from Path.glob, which is
		// case-insensitive on Windows automatically; a Go string
		// prefix/suffix check is not, so it's done explicitly here.
		var manifestPaths []string
		for _, entry := range entries {
			name := strings.ToLower(entry.Name())
			if strings.HasPrefix(name, "appmanifest_") && strings.HasSuffix(name, ".acf") {
				manifestPaths = append(manifestPaths, filepath.Join(steamappsDir, entry.Name()))
			}
		}
		sort.Strings(manifestPaths)

		for _, manifestPath := range manifestPaths {
			app, parseErr := ParseAppManifestFile(manifestPath, lib)
			if parseErr != nil {
				// *ParseError wraps the OS error (Unwrap), so a manifest
				// that vanished between the directory listing above and
				// this read - Steam mid-uninstall/move, or a dangling
				// link - is told apart from a genuinely corrupt file: the
				// two call for different operator action (none vs. look
				// at the file). Pinned by
				// TestDiscoverWarnsVanishedManifestDistinctlyFromCorrupt.
				if errors.Is(parseErr, fs.ErrNotExist) {
					warnings = append(warnings, Warning{fmt.Sprintf(
						"manifest %s vanished between listing and reading it (Steam uninstalling/moving?), skipping", manifestPath)})
					continue
				}
				warnings = append(warnings, Warning{fmt.Sprintf(
					"skipping corrupt manifest %s: %s", manifestPath, parseErr)})
				continue
			}

			if firstLib, dup := seenAppIDs[app.AppID]; dup {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"duplicate appid %s in library %s, keeping first occurrence from %s",
					app.AppID, lib, firstLib)})
				continue
			}

			seenAppIDs[app.AppID] = lib
			apps = append(apps, app)
		}
	}

	return Discovery{
		Apps:            apps,
		Warnings:        warnings,
		LibrariesProbed: orderedLibraries,
		LibrariesRead:   librariesRead,
	}
}

// libraryKey normalises a library path for duplicate detection only:
// filepath.Clean removes trailing separators and "." segments, and on
// Windows - whose filesystems are case-insensitive, and where Steam
// itself writes drive letters in whichever case the user typed - the
// comparison is additionally case-folded. goos is a parameter (not
// runtime.GOOS read inside) so both branches are unit-testable on any
// host.
func libraryKey(path, goos string) string {
	key := filepath.Clean(path)
	if goos == "windows" {
		key = strings.ToLower(key)
	}
	return key
}
