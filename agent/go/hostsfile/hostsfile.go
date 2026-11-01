// Package hostsfile manages EXACTLY ONE marker-delimited block in a
// system hosts file (WP 2.3, plan §3 "optional hosts-file mode" and §10
// deployment mode 3):
//
//	# BEGIN steamhangar-agent (managed block - do not edit inside)
//	192.168.1.50 lancache.steamcontent.com
//	# END steamhangar-agent
//
// The hostname is not ours to choose: `lancache.steamcontent.com` is
// hardcoded by Valve in the Steam client and lives on Valve's own
// steamcontent.com domain — it is the client's built-in cache-discovery
// interface, not a LanCache-project dependency (plan §3). Pointing it at
// vault-core makes the client use the cache with no DNS server involved
// at all.
//
// # Why this package is written the way it is
//
// This code modifies a file the ENTIRE machine's name resolution depends
// on, on a user's own PC, with administrator rights. Every design choice
// below favors auditability and reversibility over cleverness:
//
//   - Exactly one block, delimited by markers. Nothing outside the markers
//     is ever read for meaning or rewritten. Splicing happens on BYTE
//     OFFSETS (never "split into lines, modify, join") so the bytes before
//     and after the block are carried over verbatim — line endings,
//     trailing whitespace, exotic encodings in unrelated lines and all.
//   - Every mutation writes <path>.steamhangar.bak FIRST, containing the
//     exact pre-mutation bytes. If the backup cannot be written, no
//     mutation happens at all (fail closed).
//   - A hosts file whose markers do not form exactly one well-formed
//     BEGIN..END pair is NEVER touched — not by Apply, not by Remove.
//     We cannot identify our own block, so any edit would be a guess.
//   - An entry for the same hostname OUTSIDE our block blocks Apply. The
//     system resolver takes the FIRST match, so silently appending a
//     second entry could produce a block that is present, correct-looking,
//     and completely ineffective.
//   - No elevation logic. See the ElevationHint doc comment.
//
// # Platform scope
//
// Implemented platform-neutrally; Windows remains the documented primary
// target (plan §10 mode 3). The plan's original Windows-only rationale
// ("the Linux/Steam Deck client does not perform this lookup") was
// disproven by Phase 0 WP 0.6: the CURRENT Linux client DOES perform
// lancache discovery (3574 requests through the cache — plan §7's Linux
// checkbox). Hosts mode is therefore useful on Linux/SteamOS too, and
// nothing in this package is Windows-specific beyond the default path and
// the elevation hint wording.
package hostsfile

import (
	"bytes"
	"fmt"
	"io/fs"
	"net/netip"
	"os"
	"runtime"
	"strings"
)

// Hostname is the cache-discovery hostname the Steam client itself looks
// up. Hardcoded by Valve; it cannot be renamed (plan §3).
const Hostname = "lancache.steamcontent.com"

// Marker lines delimiting the managed block.
//
// beginPrefix/endPrefix (not the full marker strings) are what detection
// matches on, so the human-readable parenthetical in BeginMarker can be
// reworded in a future version without a block written by an older
// vault-agent becoming invisible (which would make Apply append a SECOND
// block — the exact failure this package exists to prevent).
const (
	BeginMarker = "# BEGIN steamhangar-agent (managed block - do not edit inside)"
	EndMarker   = "# END steamhangar-agent"

	beginPrefix = "# BEGIN steamhangar-agent"
	endPrefix   = "# END steamhangar-agent"
)

// legacyBeginPrefixes/legacyEndPrefixes are prefixes an OLDER vault-agent
// build may have written to a user's hosts file before the SteamVault ->
// SteamHangar product rename (WP RN-1). Detection must keep matching them,
// or a block an older agent wrote becomes invisible to a newer one: Apply
// would append a SECOND block above/below it (two markers "present" at
// once), and Remove would leave the old one orphaned in the user's system
// hosts file forever, since it could never be identified as ours again.
// steamvault-agent is the real, shipped legacy name; steamkeep-agent and
// steamsilo-agent are defensive only — both were considered as the new
// product name for a few hours the same day as the rename and dropped
// before either was ever committed to the default branch, but recognizing
// them costs nothing and closes the door on a hosts file from that window.
// Because isMarker matches a PREFIX (not the full line), Apply's rewrite
// (renderBlock, using the CURRENT BeginMarker/EndMarker) heals any of these
// on the very next apply — detection plus unconditional rewrite is the
// whole healing mechanism, there is no separate migration step.
var legacyBeginPrefixes = []string{
	"# BEGIN steamvault-agent",
	"# BEGIN steamkeep-agent",
	"# BEGIN steamsilo-agent",
}

var legacyEndPrefixes = []string{
	"# END steamvault-agent",
	"# END steamkeep-agent",
	"# END steamsilo-agent",
}

// BackupSuffix is appended to the hosts file path to form the backup
// written before every mutation.
const BackupSuffix = ".steamhangar.bak"

// State is the coarse answer to "what does the hosts file look like right
// now" that Verify reports.
type State string

const (
	// StateAbsent: no managed block (and no marker lines at all).
	StateAbsent State = "absent"

	// StatePresentCorrect: exactly one well-formed block containing a
	// single entry for Hostname whose address matches the expected one
	// (or any address, if no expectation was given).
	StatePresentCorrect State = "present-correct"

	// StatePresentDifferentIP: well-formed block, single valid entry for
	// Hostname, but a different address than expected. Apply heals this.
	StatePresentDifferentIP State = "present-different-ip"

	// StatePresentModified: the block boundaries are unambiguous, but its
	// INTERIOR is not the single Hostname entry we write (hand-edited,
	// extra lines, a non-IPv4 address, ...). Not a refusal case: the
	// boundaries are known, everything between them is ours, so Apply
	// rewrites it and Remove deletes it.
	//
	// NOTE: this is a fifth state beyond the four named in the WP 2.3
	// brief (absent | present-correct | present-different-ip |
	// markers-corrupt). It exists because folding "someone added a line
	// inside our block" into any of the other four would report something
	// untrue — "present-correct" would be a lie and "markers-corrupt"
	// would refuse to touch a block that is perfectly identifiable.
	StatePresentModified State = "present-modified"

	// StateMarkersCorrupt: the marker lines do not form exactly one
	// BEGIN..END pair (BEGIN without END, END without BEGIN, END before
	// BEGIN, duplicates). REFUSE TO TOUCH — Apply and Remove both return
	// a *CorruptError and write nothing.
	StateMarkersCorrupt State = "markers-corrupt"
)

// Conflict is an entry for Hostname found OUTSIDE the managed block.
type Conflict struct {
	Line          int    // 1-based line number, as an editor shows it
	Text          string // the line verbatim, without its line ending
	Address       string // the address field of that entry
	BeforeManaged bool   // true if it precedes our block (or the block is absent)
}

// Status is Verify's full report.
type Status struct {
	Path   string
	Exists bool // false if the hosts file itself is missing
	State  State

	// Address is the address found inside the managed block, or "" when
	// there is no block or its interior is not a single Hostname entry.
	Address string

	// Canonical is true when the block's interior is BYTE-IDENTICAL to
	// what Apply would write. A semantically fine but oddly formatted
	// entry ("10.0.0.1   lancache.steamcontent.com" with extra spaces) is
	// still StatePresentCorrect, but not Canonical — Apply normalizes it.
	Canonical bool

	// BlockLines is the block's interior, verbatim, line endings stripped.
	BlockLines []string

	// Conflicts lists entries for Hostname outside the managed block.
	Conflicts []Conflict

	// EOL is the file's dominant line ending ("\r\n" or "\n"), which is
	// what Apply writes inside the block.
	EOL string

	// Detail is a human-readable explanation. Always non-empty for
	// StateMarkersCorrupt and StatePresentModified.
	Detail string
}

// Present reports whether a managed block exists (in any interior state).
func (s Status) Present() bool {
	switch s.State {
	case StatePresentCorrect, StatePresentDifferentIP, StatePresentModified:
		return true
	}
	return false
}

// CorruptError is returned by Apply/Remove when the markers do not form
// exactly one BEGIN..END pair. Nothing was written.
type CorruptError struct {
	Path   string
	Detail string
}

func (e *CorruptError) Error() string {
	return fmt.Sprintf(
		"%s: the steamhangar-agent markers are damaged (%s).\n"+
			"Refusing to modify the file: the managed block cannot be identified,\n"+
			"so any edit would be a guess. Fix it by hand — delete every line from\n"+
			"the %q line through the %q line — then re-run this command.",
		e.Path, e.Detail, beginPrefix, endPrefix)
}

// ConflictError is returned by Apply when an entry for Hostname exists
// outside the managed block. Nothing was written.
//
// Remove is deliberately NOT blocked by conflicts: the uninstall path
// must always work (plan §7 "clean uninstall path").
type ConflictError struct {
	Path      string
	Conflicts []Conflict
}

func (e *ConflictError) Error() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s already contains an entry for %s outside the steamhangar-agent block:\n", e.Path, Hostname)
	for _, c := range e.Conflicts {
		fmt.Fprintf(&b, "  line %d: %s\n", c.Line, c.Text)
	}
	b.WriteString(
		"Refusing to add a second entry for the same hostname: the system resolver\n" +
			"answers with the FIRST match, so a managed block added below an existing\n" +
			"entry would look correct and do nothing. Delete the line(s) above (this is\n" +
			"most likely a manual entry you added earlier) and re-run this command.")
	return b.String()
}

// Result describes what Apply/Remove did.
type Result struct {
	Path       string
	Changed    bool   // false = the file already had exactly the desired bytes
	Before     Status // the state found before any mutation
	BackupPath string // "" when nothing was written
	Method     string // MethodRename | MethodInPlace | "" when nothing was written
}

// ---------------------------------------------------------------------------
// Reading and parsing
// ---------------------------------------------------------------------------

// fileLine is one line of the raw file, kept with its byte offsets so the
// splice in render* can be done on the ORIGINAL bytes rather than by
// re-joining parsed lines (which is how line-ending and trailing-byte
// corruption sneaks into this kind of code).
type fileLine struct {
	start int    // offset of the first byte of the line
	end   int    // offset just past the line's terminator (== len(raw) at EOF)
	text  string // content without the terminator
	eol   string // "\r\n", "\n", or "" for a final line with no terminator
}

// parsed is the internal analysis of one hosts file.
type parsed struct {
	raw      []byte
	lines    []fileLine
	eol      string
	beginIdx int // index into lines, -1 if no BEGIN marker
	endIdx   int // index into lines, -1 if no END marker
	status   Status
}

// splitLines splits raw into lines, recognizing "\r\n" and "\n" as
// terminators. A lone "\r" (classic Mac OS) is NOT treated as a line
// terminator — it stays part of the line's text, which is what every
// hosts-file parser in practice (glibc, the Windows DNS client) does too.
func splitLines(raw []byte) []fileLine {
	var out []fileLine
	start := 0
	for start < len(raw) {
		nl := bytes.IndexByte(raw[start:], '\n')
		if nl < 0 {
			out = append(out, fileLine{
				start: start, end: len(raw),
				text: string(raw[start:]), eol: "",
			})
			break
		}
		textEnd := start + nl
		lineEnd := textEnd + 1
		eol := "\n"
		if textEnd > start && raw[textEnd-1] == '\r' {
			textEnd--
			eol = "\r\n"
		}
		out = append(out, fileLine{
			start: start, end: lineEnd,
			text: string(raw[start:textEnd]), eol: eol,
		})
		start = lineEnd
	}
	return out
}

// dominantEOL picks the line ending Apply writes inside the block: the
// one the file already uses in the majority of its lines. CRLF wins ties
// because a Windows hosts file that happens to contain a single stray LF
// line is still a CRLF file. A file with no line terminators at all (empty
// file, or a single unterminated line) falls back to the platform default.
func dominantEOL(lines []fileLine, goos string) string {
	crlf, lf := 0, 0
	for _, l := range lines {
		switch l.eol {
		case "\r\n":
			crlf++
		case "\n":
			lf++
		}
	}
	if crlf == 0 && lf == 0 {
		if goos == "windows" {
			return "\r\n"
		}
		return "\n"
	}
	if crlf >= lf {
		return "\r\n"
	}
	return "\n"
}

// isMarker matches a marker line: the prefix exactly, or the prefix
// followed by a space (so the human-readable parenthetical is optional
// and free to change between versions).
func isMarker(text, prefix string) bool {
	t := strings.TrimSpace(text)
	return t == prefix || strings.HasPrefix(t, prefix+" ")
}

// isMarkerAny reports whether text matches any of the given prefixes, under
// the same rule as isMarker (exact prefix, or prefix plus a trailing
// human-readable parenthetical).
func isMarkerAny(text string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if isMarker(text, prefix) {
			return true
		}
	}
	return false
}

// hostsEntry is one parsed hosts-file entry: an address plus the
// hostnames it answers for.
type hostsEntry struct {
	address   string
	hostnames []string
}

// parseHostsEntry applies the hosts-file grammar every resolver uses:
// everything from '#' onward is a comment; the remainder is
// whitespace-separated fields, the first being the address.
func parseHostsEntry(text string) (hostsEntry, bool) {
	if i := strings.IndexByte(text, '#'); i >= 0 {
		text = text[:i]
	}
	fields := strings.Fields(text)
	if len(fields) < 2 {
		return hostsEntry{}, false
	}
	return hostsEntry{address: fields[0], hostnames: fields[1:]}, true
}

// sameHost compares two hostnames the way a resolver does: case-insensitively,
// and ignoring a single trailing dot. "lancache.steamcontent.com." is the
// fully-qualified spelling of the same name and the Windows resolver
// normalizes it, so an entry written that way shadows our block exactly like
// the dotless form would — it has to count as a conflict, not slip past.
func sameHost(a, b string) bool {
	return strings.EqualFold(strings.TrimSuffix(a, "."), strings.TrimSuffix(b, "."))
}

func (e hostsEntry) namesCacheHost() bool {
	for _, h := range e.hostnames {
		if sameHost(h, Hostname) {
			return true
		}
	}
	return false
}

// renderEntryLine is the ONE content line the managed block ever holds.
func renderEntryLine(address string) string {
	return address + " " + Hostname
}

// BlockPreview returns the three lines Apply writes, for showing the user
// exactly what went into (or is in) their hosts file. Line endings are the
// caller's business; these are bare lines.
func BlockPreview(address string) []string {
	return []string{BeginMarker, renderEntryLine(address), EndMarker}
}

// renderBlock builds the block WITHOUT a trailing line terminator; the
// caller supplies the terminator that belongs after the END marker (see
// renderApplied) so the byte that follows the block is whatever the file
// already had there.
func renderBlock(address, eol string) string {
	return BeginMarker + eol + renderEntryLine(address) + eol + EndMarker
}

// parseBytes analyses raw. expectedAddress may be "" meaning "any address
// inside the block is acceptable" — used by Remove and by `hosts status`
// when the caller did not say which address it expects.
func parseBytes(raw []byte, path, expectedAddress string, goos string) *parsed {
	lines := splitLines(raw)
	p := &parsed{
		raw:      raw,
		lines:    lines,
		eol:      dominantEOL(lines, goos),
		beginIdx: -1,
		endIdx:   -1,
	}
	p.status = Status{Path: path, Exists: true, EOL: p.eol}

	var begins, ends []int
	for i, l := range lines {
		switch {
		case isMarker(l.text, beginPrefix) || isMarkerAny(l.text, legacyBeginPrefixes):
			begins = append(begins, i)
		case isMarker(l.text, endPrefix) || isMarkerAny(l.text, legacyEndPrefixes):
			ends = append(ends, i)
		}
	}

	switch {
	case len(begins) == 0 && len(ends) == 0:
		p.status.State = StateAbsent
	case len(begins) == 1 && len(ends) == 1 && ends[0] > begins[0]:
		p.beginIdx, p.endIdx = begins[0], ends[0]
		p.classifyBlock(expectedAddress)
	default:
		p.status.State = StateMarkersCorrupt
		p.status.Detail = corruptDetail(begins, ends)
	}

	p.status.Conflicts = p.findConflicts()
	return p
}

// corruptDetail spells out exactly which marker lines were found, so the
// user can go fix the file by hand without re-reading it themselves.
func corruptDetail(begins, ends []int) string {
	describe := func(idxs []int, what string) string {
		switch len(idxs) {
		case 0:
			return "no " + what + " marker"
		case 1:
			return fmt.Sprintf("%s marker on line %d", what, idxs[0]+1)
		default:
			nums := make([]string, len(idxs))
			for i, ix := range idxs {
				nums[i] = fmt.Sprint(ix + 1)
			}
			return fmt.Sprintf("%d %s markers, on lines %s", len(idxs), what, strings.Join(nums, ", "))
		}
	}
	detail := describe(begins, "BEGIN") + "; " + describe(ends, "END")
	if len(begins) == 1 && len(ends) == 1 && ends[0] < begins[0] {
		detail += "; END comes before BEGIN"
	}
	return detail
}

// classifyBlock inspects the interior of an unambiguously delimited block.
func (p *parsed) classifyBlock(expectedAddress string) {
	interior := p.lines[p.beginIdx+1 : p.endIdx]
	p.status.BlockLines = make([]string, 0, len(interior))
	for _, l := range interior {
		p.status.BlockLines = append(p.status.BlockLines, l.text)
	}

	if len(interior) != 1 {
		p.status.State = StatePresentModified
		p.status.Detail = fmt.Sprintf(
			"the managed block holds %d lines; it should hold exactly one entry for %s",
			len(interior), Hostname)
		return
	}

	entry, ok := parseHostsEntry(interior[0].text)
	if !ok || len(entry.hostnames) != 1 || !sameHost(entry.hostnames[0], Hostname) {
		p.status.State = StatePresentModified
		p.status.Detail = fmt.Sprintf(
			"the line inside the managed block is not a single entry for %s", Hostname)
		return
	}
	if err := ValidateCacheIP(entry.address); err != nil {
		p.status.State = StatePresentModified
		p.status.Detail = fmt.Sprintf(
			"the address inside the managed block (%q) is not a plain IPv4 address", entry.address)
		return
	}

	p.status.Address = entry.address
	p.status.Canonical = interior[0].text == renderEntryLine(entry.address)

	if expectedAddress != "" && entry.address != expectedAddress {
		p.status.State = StatePresentDifferentIP
		p.status.Detail = fmt.Sprintf("the managed block points at %s, expected %s",
			entry.address, expectedAddress)
		return
	}
	p.status.State = StatePresentCorrect
	if !p.status.Canonical {
		p.status.Detail = "the entry is effective but not written the way vault-agent writes it; " +
			"re-applying normalizes the formatting"
	}
}

// findConflicts lists entries for Hostname outside the managed block.
// Commented-out lines are not entries (parseHostsEntry strips comments),
// so a line the user parked behind a '#' is correctly ignored.
func (p *parsed) findConflicts() []Conflict {
	var out []Conflict
	for i, l := range p.lines {
		if p.beginIdx >= 0 && i >= p.beginIdx && i <= p.endIdx {
			continue // inside our own block
		}
		entry, ok := parseHostsEntry(l.text)
		if !ok || !entry.namesCacheHost() {
			continue
		}
		out = append(out, Conflict{
			Line:          i + 1,
			Text:          l.text,
			Address:       entry.address,
			BeforeManaged: p.beginIdx < 0 || i < p.beginIdx,
		})
	}
	return out
}

// ---------------------------------------------------------------------------
// Rendering the desired file content
// ---------------------------------------------------------------------------

// renderApplied returns the bytes the file should hold once the managed
// block names address.
//
// Trailing-newline handling, stated explicitly because it is the one
// place this package cannot be perfectly round-trip-exact:
//
//   - Block ABSENT, file does not end in a newline: a line terminator is
//     inserted first, otherwise the file's last line would be glued to our
//     BEGIN marker. The resulting file ends with a terminator after the END
//     marker. Remove cannot later tell that inserted terminator apart from
//     one the file always had, so a file that had NO final newline before
//     Apply comes back from Remove with one. That single byte is the only
//     documented deviation from byte-exact round-tripping (and it makes the
//     file more POSIX-correct, not less).
//   - Block PRESENT: the terminator that followed the old END marker is
//     reused verbatim — including the empty one when the block sat at EOF
//     with no final newline. Everything after the block is copied byte for
//     byte.
func (p *parsed) renderApplied(address string) []byte {
	block := renderBlock(address, p.eol)

	if p.beginIdx < 0 {
		var out bytes.Buffer
		out.Write(p.raw)
		if len(p.raw) > 0 && p.raw[len(p.raw)-1] != '\n' {
			out.WriteString(p.eol)
		}
		out.WriteString(block)
		out.WriteString(p.eol)
		return out.Bytes()
	}

	beginLine := p.lines[p.beginIdx]
	endLine := p.lines[p.endIdx]

	var out bytes.Buffer
	out.Write(p.raw[:beginLine.start])
	out.WriteString(block)
	out.WriteString(endLine.eol) // "" when the block ended at EOF unterminated
	out.Write(p.raw[endLine.end:])
	return out.Bytes()
}

// renderRemoved returns the bytes the file should hold with the managed
// block deleted: everything before the BEGIN line, then everything from
// the byte after the END line's terminator onward. Both halves are copied
// verbatim from the original.
func (p *parsed) renderRemoved() []byte {
	if p.beginIdx < 0 {
		return p.raw
	}
	beginLine := p.lines[p.beginIdx]
	endLine := p.lines[p.endIdx]

	out := make([]byte, 0, len(p.raw))
	out = append(out, p.raw[:beginLine.start]...)
	out = append(out, p.raw[endLine.end:]...)
	return out
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

// load reads and parses path. A missing file is reported as
// (nil, Status{Exists:false}, nil) rather than an error, so `hosts status`
// can describe it instead of failing.
func load(path, expectedAddress string) (*parsed, Status, error) {
	// Lstat, not Stat: a symlink has to be recognized AS a symlink (see the
	// refusal below), and a symlink pointing at a directory must not be
	// reported as an ordinary directory.
	info, serr := os.Lstat(path)
	switch {
	case serr != nil && os.IsNotExist(serr):
		return nil, Status{
			Path:   path,
			Exists: false,
			State:  StateAbsent,
			EOL:    dominantEOL(nil, runtime.GOOS),
			Detail: "the hosts file does not exist",
		}, nil

	case serr != nil:
		return nil, Status{Path: path}, serr

	case info.Mode()&fs.ModeSymlink != 0:
		// Refusing beats following. The atomic write REPLACES the path with a
		// new file, which would silently turn the link into a regular file and
		// lose whatever managed it — /etc/hosts is a symlink into the store on
		// NixOS and into generated config in some container images, and
		// neither `hosts remove` nor the .bak can put a link back. Operating on
		// the link TARGET (filepath.EvalSymlinks) would be the alternative;
		// refusal is chosen for v1 because it cannot surprise anyone, and
		// because on those systems the real fix is to edit whatever GENERATES
		// the file, not the generated output.
		target, rerr := os.Readlink(path)
		if rerr != nil {
			target = "?"
		}
		return nil, Status{Path: path}, fmt.Errorf(
			"%s is a symbolic link (-> %s). Refusing to modify it: writing here would "+
				"replace the link with a regular file and lose whatever manages it "+
				"(this is how /etc/hosts works on NixOS and in some container images). "+
				"Point --hosts-path at the real file, or add the entry wherever that "+
				"file is generated from", path, target)

	case info.IsDir():
		// Checked up front so a --hosts-path typo that lands on a directory
		// gets a sentence the user can act on, instead of the platform's raw
		// read error ("is a directory" on Linux, "Access is denied." on
		// Windows — the latter would send them hunting for a permission
		// problem that isn't there).
		return nil, Status{Path: path}, fmt.Errorf(
			"%s is a directory, not a hosts file — check --hosts-path "+
				"(this platform's system hosts file is %s)", path, DefaultPath())
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) { // raced with a delete between Lstat and here
			return nil, Status{
				Path:   path,
				Exists: false,
				State:  StateAbsent,
				EOL:    dominantEOL(nil, runtime.GOOS),
				Detail: "the hosts file does not exist",
			}, nil
		}
		return nil, Status{Path: path}, err
	}
	if err := validateEncoding(raw, path); err != nil {
		return nil, Status{Path: path}, err
	}
	p := parseBytes(raw, path, expectedAddress, runtime.GOOS)
	return p, p.status, nil
}

// validateEncoding refuses anything that is not byte-oriented plain text.
//
// This is a correctness guard, not pedantry. Every function in this package
// treats the file as bytes-with-newlines: in UTF-16 each ASCII character is
// followed by a NUL, so "10.0.0.7 lancache.steamcontent.com" does not match
// anything the conflict scanner looks for, marker detection fails, and Apply
// would happily append a UTF-8 block to a UTF-16 file — producing a
// mixed-encoding file that the resolver ignores while vault-agent reports
// "present-correct". Refusing loudly is the only honest answer.
//
// A UTF-8 BOM is fine and deliberately not rejected: it is one harmless
// prefix on the first line and everything downstream still works.
func validateEncoding(raw []byte, path string) error {
	const hint = "Refusing to touch it — convert it to UTF-8 or plain ANSI first " +
		"(Windows Notepad: File > Save as... > Encoding: UTF-8; " +
		"Linux: iconv -f UTF-16 -t UTF-8 hosts -o hosts)"

	// UTF-32LE's BOM starts with UTF-16LE's, and UTF-32BE's starts with NUL
	// bytes, so the order of these cases is load-bearing.
	switch {
	case bytes.HasPrefix(raw, []byte{0xFF, 0xFE, 0x00, 0x00}):
		return fmt.Errorf("%s begins with a UTF-32 (little-endian) byte-order mark, "+
			"so it is not a plain-text hosts file. %s", path, hint)
	case bytes.HasPrefix(raw, []byte{0x00, 0x00, 0xFE, 0xFF}):
		return fmt.Errorf("%s begins with a UTF-32 (big-endian) byte-order mark, "+
			"so it is not a plain-text hosts file. %s", path, hint)
	case bytes.HasPrefix(raw, []byte{0xFF, 0xFE}):
		return fmt.Errorf("%s begins with a UTF-16 (little-endian) byte-order mark, "+
			"so it is not a plain-text hosts file. %s", path, hint)
	case bytes.HasPrefix(raw, []byte{0xFE, 0xFF}):
		return fmt.Errorf("%s begins with a UTF-16 (big-endian) byte-order mark, "+
			"so it is not a plain-text hosts file. %s", path, hint)
	case bytes.IndexByte(raw, 0x00) >= 0:
		return fmt.Errorf("%s contains NUL bytes, so it is not a plain-text hosts file "+
			"(most likely UTF-16 saved without a byte-order mark). %s", path, hint)
	}
	return nil
}

// Verify reports the current state of path without modifying anything.
//
// expectedAddress may be "" ("any address in the block is acceptable"),
// in which case a well-formed block is reported as StatePresentCorrect
// and its address is available in Status.Address.
func Verify(path, expectedAddress string) (Status, error) {
	if expectedAddress != "" {
		if err := ValidateCacheIP(expectedAddress); err != nil {
			return Status{Path: path}, err
		}
	}
	_, st, err := load(path, expectedAddress)
	return st, err
}

// Apply makes the managed block name cacheIP, idempotently: absent it is
// appended, present it is replaced IN PLACE (its position in the file is
// preserved), already-correct it is left completely alone (no write, no
// backup — Result.Changed is false).
//
// Refuses, writing nothing, when:
//   - the hosts file does not exist (a typo'd --hosts-path must fail
//     loudly, not silently create a file nothing reads);
//   - the markers are corrupt (*CorruptError);
//   - an entry for Hostname exists outside the block (*ConflictError).
//
// Everything outside the markers is preserved byte for byte.
func Apply(path, cacheIP string) (Result, error) {
	if err := ValidateCacheIP(cacheIP); err != nil {
		return Result{Path: path}, err
	}
	p, st, err := load(path, cacheIP)
	if err != nil {
		return Result{Path: path}, err
	}
	res := Result{Path: path, Before: st}

	if !st.Exists {
		return res, fmt.Errorf(
			"%s does not exist; refusing to create it. Check the path (--hosts-path) "+
				"— on this platform the system hosts file is %s", path, DefaultPath())
	}
	if st.State == StateMarkersCorrupt {
		return res, &CorruptError{Path: path, Detail: st.Detail}
	}
	if len(st.Conflicts) > 0 {
		return res, &ConflictError{Path: path, Conflicts: st.Conflicts}
	}

	desired := p.renderApplied(cacheIP)
	if bytes.Equal(desired, p.raw) {
		return res, nil // already exactly right: no write, no backup
	}

	backupPath, method, err := mutate(path, p.raw, desired)
	res.BackupPath, res.Method = backupPath, method
	if err != nil {
		return res, err
	}
	res.Changed = true
	return res, nil
}

// Remove deletes exactly the managed block (the clean uninstall path,
// plan §7). Idempotent: with no block present it reports Changed=false
// and writes nothing at all.
//
// Refuses only on corrupt markers. A conflicting entry elsewhere in the
// file does NOT block removal — uninstall must always work.
func Remove(path string) (Result, error) {
	p, st, err := load(path, "")
	if err != nil {
		return Result{Path: path}, err
	}
	res := Result{Path: path, Before: st}

	if !st.Exists {
		return res, nil // nothing to remove from a file that isn't there
	}
	if st.State == StateMarkersCorrupt {
		return res, &CorruptError{Path: path, Detail: st.Detail}
	}
	if !st.Present() {
		return res, nil
	}

	desired := p.renderRemoved()
	if bytes.Equal(desired, p.raw) {
		return res, nil
	}

	backupPath, method, err := mutate(path, p.raw, desired)
	res.BackupPath, res.Method = backupPath, method
	if err != nil {
		return res, err
	}
	res.Changed = true
	return res, nil
}

// ValidateCacheIP enforces "plain IPv4 address, nothing else" — the same
// stance vault-dns takes for CACHE_IP (dns/docker-entrypoint.sh), and the
// LEARNINGS rule from WP 1.9: a value substituted verbatim into a
// line-oriented config file must be validated first, or a value carrying a
// newline injects arbitrary directives. Here that would be arbitrary hosts
// entries — i.e. the ability to redirect any hostname on the machine.
//
// Stricter than the vault-dns shell validator in one respect: netip
// rejects leading zeros ("010.1.1.1"), which are ambiguous (some resolvers
// read them as octal). That is deliberate.
func ValidateCacheIP(s string) error {
	if s == "" {
		return fmt.Errorf("cache IP is required (the LAN IPv4 address of the machine running vault-core)")
	}
	if strings.TrimSpace(s) != s {
		return fmt.Errorf("cache IP %q must not have leading or trailing whitespace", s)
	}
	addr, err := netip.ParseAddr(s)
	if err != nil {
		return fmt.Errorf("cache IP %q is not a plain IPv4 address (e.g. 192.168.1.50): %w", s, err)
	}
	if addr.Is4In6() {
		return fmt.Errorf("cache IP %q is an IPv4-mapped IPv6 address; write it as a plain IPv4 address instead", s)
	}
	if !addr.Is4() {
		return fmt.Errorf(
			"cache IP %q is an IPv6 address; hosts mode is IPv4-only on purpose "+
				"(an AAAA answer lets IPv6-capable clients bypass the cache — plan §3/§10)", s)
	}
	if addr.IsUnspecified() {
		return fmt.Errorf("cache IP %q is the unspecified address, not a reachable cache server", s)
	}
	if addr.IsMulticast() {
		return fmt.Errorf("cache IP %q is a multicast address, not a reachable cache server", s)
	}
	if s == "255.255.255.255" {
		return fmt.Errorf("cache IP %q is the broadcast address, not a reachable cache server", s)
	}
	return nil
}
