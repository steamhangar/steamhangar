"""Manifest parsers: pure functions turning on-disk Steam manifest bytes into
a common depot/manifest/chunks shape (WP 3.1) — the foundation for
staleness detection (ADR-0006) and manifest-diff garbage collection
(ADR-0007). This module only parses; ``vault_api.manifest_ingest`` (WP 3.2)
is what stores what these functions produce and archives the source files.

Two independent on-disk formats exist, both handled with stdlib only (no
protobuf/zip dependency beyond ``struct``/``zipfile`` — plan §9, and
confirmed sufficient by ``docs/research/phase3-manifests.md``):

1. **SteamPrefill's own temp-cache ``.bin`` files**
   (``$HOME/.cache/SteamPrefill/v1/{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin``
   — ``%LOCALAPPDATA%\\SteamPrefill\\v1`` on Windows — from
   ``SteamPrefill/Models/DepotInfo.cs``/``Manifest.cs``): a plain,
   uncompressed protobuf message. ``parse_steamprefill_bin``.
2. **Cache-stored client manifests**
   (``/cache/depot/<id>/manifest/<manifestid>/5/<requestcode>``): a ZIP
   archive with exactly one deflate entry named ``z``, containing Valve's
   "sectioned" manifest stream (PAYLOAD/METADATA/SIGNATURE/END), in which
   METADATA and PAYLOAD are themselves protobuf messages.
   ``parse_cache_manifest``.

Both return the **same shape** (``depot_id``, ``manifest_id``, ``chunks``,
``source``) via ``PrefillManifest``/``CacheManifest`` — two names for one
dataclass (see ``ParsedManifest``), because the whole point of this work
package is that garbage collection (ADR-0007) and staleness bookkeeping
(ADR-0006) can treat either source identically. Filenames in the PAYLOAD of
a cache-stored manifest are **encrypted** (need the depot decryption key,
which vault-api never has) — this module never attempts to read them.
Garbage collection only needs chunk SHAs, which are **not** encrypted, so
this is a non-limitation for our purposes, not a gap to fill later.

## Field numbers — derived from the work package spec, confirmed empirically

The field numbers below were cross-checked during this work package against
real files on this dev machine: SteamPrefill ``.bin`` manifests under
``%LOCALAPPDATA%\\SteamPrefill\\v1`` (e.g. depot 990081, 579 FileData
entries, 72283 chunks, all 40-hex) and cache-stored manifests under
``poc/cache/depot/*/manifest/`` (depot 481 reproduced the research
document's numbers exactly: METADATA ``unique_chunks=8``, 8 parsed chunks,
zero orphans against the on-disk ``chunk/`` directory, byte-exact
``cb_compressed`` against on-disk file size). One detail the research
document didn't spell out, found while probing the real bytes: the END
section marker is **4 bytes only** — a bare magic, no accompanying length
field — unlike PAYLOAD/METADATA/SIGNATURE, which are each ``u32 magic + u32
len (LE) + payload``.

``.bin`` payload (flat protobuf message):
    - field 2 (varint): manifest id
    - field 4 (varint): depot id
    - field 1 (length-delimited, repeated): ``FileData``
        - field 1 (length-delimited, repeated): ``ChunkData``
            - field 1 (length-delimited): chunk id, ASCII string, 40 hex chars
            - field 2 (varint): compressed length

Cache-stored manifest, METADATA section (protobuf):
    - field 1 (varint): depot_id
    - field 2 (varint): gid_manifest (manifest id)
    - field 7 (varint): unique_chunks — self-check: must equal the number of
      distinct chunk ids this module parses out of PAYLOAD

Cache-stored manifest, PAYLOAD section (``ContentManifestPayload`` protobuf):
    - field 1 (length-delimited, repeated): ``FileMapping``
        - field 6 (length-delimited, repeated): ``ChunkData``
            - field 1 (length-delimited): sha, 20 raw bytes -> hex-encode
            - field 5 (varint): cb_compressed (on-disk chunk file size)

## Untrusted input, secure by construction

Both formats are read from disk paths that ultimately trace back to network
content SteamPrefill or a Steam client wrote — treated as untrusted
throughout, never ``eval``, never unbounded:

- Every buffer this module parses (the whole ``.bin`` file, one section of
  the sectioned stream, one submessage) is checked against
  ``MAX_MESSAGE_SIZE`` before being parsed. **Honestly stated: peak memory
  for one parse is therefore roughly 2x ``MAX_MESSAGE_SIZE``, not 1x** — the
  raw read buffer and the slices/dict built from it (chunk map, decoded
  sections) are both live in memory at once during parsing, and the bound
  is generous enough to absorb that (WP 3.2 review carry-over: a docstring
  that implied a hard 1x ceiling would be misleading).
- Every accumulated chunk map is checked against ``MAX_CHUNK_COUNT``.
- The field reader (``_read_fields``) is **iterative, not recursive** — a
  nested submessage is parsed by calling it again on a byte slice, not by
  the function calling itself on a growing input, so there is no call-stack
  depth driven by attacker-controlled nesting to bound in the first place
  (the WP 2.1 lesson: an *actually* recursive parser needs an explicit
  depth cap or a crafted input escapes its own documented exception
  contract via ``RecursionError``. This module has no such recursion to
  begin with — nesting here is a fixed, hardcoded 2-3 levels the code walks
  explicitly, never driven by how deeply the input claims to nest).
- Every failure mode — truncated varint, an oversized declared length, a
  non-zip file, a zip missing the ``z`` entry, a missing required field, an
  id mismatch, an ``unique_chunks`` mismatch — raises this module's own
  ``ManifestParseError``. No other exception type is allowed to escape a
  public function here.
- The ZIP entry is read via a **bounded** ``read(MAX_MESSAGE_SIZE + 1)`` on
  an open stream, not ``ZipFile.read(name)`` (which decompresses the whole
  entry into memory in one call): the ZIP format's declared
  ``file_size``/``compress_size`` fields describe what the writer *claims*,
  not a hard limit enforced during decompression, so a hostile entry could
  otherwise cause unbounded inflation before this module ever gets a chance
  to check anything.
"""

from __future__ import annotations

import os
import struct
import zipfile
import zlib
from dataclasses import dataclass

#: Ceiling on any single buffer this module parses as one unit: a whole
#: ``.bin`` file, one section of the sectioned stream, or the decompressed
#: ``z`` entry. Real manifests observed on this dev machine top out at
#: ~3.4 MB (SteamPrefill depot 990081's ``.bin``); 64 MiB is generous
#: headroom while still refusing a corrupt/hostile declared size before it
#: turns into an oversized allocation.
MAX_MESSAGE_SIZE = 64 * 1024 * 1024

#: Sanity bound on how many chunks a single manifest may contribute. The
#: largest real manifest seen while researching this WP (SteamPrefill depot
#: 990081) has 72283 chunks across 579 ``FileData`` entries; two million is
#: a large multiple of that while still refusing a manifest that declares
#: (or would otherwise produce) an absurd chunk count.
MAX_CHUNK_COUNT = 2_000_000

#: Length of a chunk id / sha as a lowercase hex string (SHA-1 digest, 20
#: bytes -> 40 hex characters). Both formats' chunk ids are this shape,
#: whether they arrive on the wire as an ASCII string (``.bin``) or as raw
#: bytes to be hex-encoded (cache manifest).
CHUNK_ID_HEX_LEN = 40


class ManifestParseError(Exception):
    """Raised for any malformed, truncated, or self-inconsistent manifest
    input. Callers get exactly one exception type to catch; no other
    exception (``struct.error``, ``zipfile.BadZipFile``,
    ``UnicodeDecodeError``, ``KeyError``, a bare ``OSError``, ...) is ever
    allowed to escape a public function in this module."""


# --------------------------------------------------------------------------
# The common result shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedManifest:
    """What both parsers produce — the shape Phase 3.2 (schema/ingestion)
    and Phase 3.6 (garbage collection, ADR-0007) build on. Neither is wired
    up by this work package.

    ``chunks`` maps a 40-hex chunk id to its on-disk (compressed) byte size
    — ``cb_compressed``/``ChunkData.2``, which prior research
    (``docs/research/phase3-manifests.md``) established is byte-exact
    against the real cached chunk file size, so this map alone is enough
    for GC's reclaim-size reporting without touching the filesystem again.

    **Chunk ids are always lowercase hex, and this is load-bearing for GC**
    (WP 3.2): a future garbage-collection pass compares these ids directly
    against on-disk chunk filenames, which are themselves lowercase, so a
    parser that preserved a manifest's original casing would silently never
    match anything and delete nothing (or, worse, everything it shouldn't).
    Both parsers normalize to lowercase before a chunk id ever reaches this
    map — see ``_validate_hex_chunk_id`` (``.bin``) and ``bytes.hex()``,
    which is lowercase by construction (cache manifest).

    **Duplicate chunk ids within one manifest: last one wins**, plain ``dict``
    assignment semantics (``_add_chunk``) — a manifest that (legitimately or
    via a hostile/corrupt encoder) lists the same chunk id twice with
    different sizes ends up with whichever size was parsed last, not an
    error and not the first value. Pinned by
    ``test_bin_duplicate_chunk_id_conflicting_sizes_last_one_wins`` /
    ``test_cache_manifest_duplicate_sha_across_file_mappings_collapses_to_one``.
    """

    depot_id: int
    manifest_id: int
    chunks: dict[str, int]
    source: str


#: Return type of ``parse_steamprefill_bin``. Deliberately the *same*
#: dataclass as ``CacheManifest`` (not a subclass, not a separate type with
#: matching fields): the whole point of "a common shape usable by GC" is
#: that calling code never needs to know or care which of the two on-disk
#: formats a given ``ParsedManifest`` came from — ``source`` is there for
#: logging/debugging, not for a caller to branch on.
PrefillManifest = ParsedManifest

#: Return type of ``parse_cache_manifest`` — see ``PrefillManifest`` above.
CacheManifest = ParsedManifest

#: ``ParsedManifest.source`` for each format.
SOURCE_STEAMPREFILL_BIN = "steamprefill_bin"
SOURCE_CACHE_MANIFEST = "cache_manifest"


# --------------------------------------------------------------------------
# Minimal protobuf wire reader (varint + length-delimited only, iterative)
# --------------------------------------------------------------------------

_WIRE_VARINT = 0
_WIRE_64BIT = 1
_WIRE_LENGTH_DELIMITED = 2
_WIRE_32BIT = 5

#: A protobuf varint encodes at most a 64-bit value, which never needs more
#: than 10 continuation-bearing bytes (10 * 7 = 70 bits of headroom for 64
#: significant bits). Anything longer is either corrupt or hostile input
#: designed to spin the reader — bound it explicitly rather than trusting
#: the continuation bit to stop on its own.
_MAX_VARINT_BYTES = 10


@dataclass(frozen=True)
class _Field:
    """One decoded protobuf field: ``value`` is an ``int`` for wire types
    0/1/5 (this module never interprets 1/5 numerically — 5 shows up as the
    cache manifest's CRC field, which GC does not need) and ``bytes`` for
    wire type 2 (length-delimited: a string, embedded message, or byte
    blob — the caller decides which)."""

    number: int
    wire_type: int
    value: object


def _read_varint(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    """Read one varint from ``buf[pos:end]``. Returns ``(value, new_pos)``.

    Raises ``ManifestParseError`` on truncation (continuation bit set at
    the last available byte) or on a varint longer than
    ``_MAX_VARINT_BYTES`` (corrupt/hostile input) — never lets the loop run
    past ``end``.
    """
    result = 0
    shift = 0
    start = pos
    while True:
        if pos - start >= _MAX_VARINT_BYTES:
            raise ManifestParseError(
                f"varint starting at offset {start} exceeds {_MAX_VARINT_BYTES} bytes"
            )
        if pos >= end:
            raise ManifestParseError(f"truncated varint starting at offset {start}")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7


def _read_fields(buf: bytes, start: int, end: int, *, context: str) -> list[_Field]:
    """Iteratively parse a flat sequence of protobuf fields in ``buf[start:end]``.

    Supports exactly the wire types the two manifest formats use: 0
    (varint), 2 (length-delimited — string/bytes/embedded message) and 5
    (32-bit fixed, present only as the cache manifest's CRC field, which
    this module reads past but never interprets). Wire type 1 (64-bit
    fixed) is accepted the same defensive way in case a future Steam
    manifest revision adds one; any *other* wire type value is a hard
    ``ManifestParseError`` (protobuf defines only these).

    **Not recursive.** A nested submessage is a length-delimited field
    whose ``value`` is itself a ``bytes`` slice — a caller that needs to
    look inside it calls ``_read_fields`` again on that slice. Nesting
    depth in both formats is small and fixed by this module's own code (at
    most: top message -> FileData/FileMapping -> ChunkData), never driven
    by how deeply an attacker-controlled input claims to nest, so unlike a
    genuine recursive-descent parser (``agent/vault_agent/acf.py``'s
    KeyValues reader) there is no stack depth here that untrusted input
    could grow.

    Every buffer handed to this function — including every nested slice —
    is checked against ``MAX_MESSAGE_SIZE`` before parsing.
    """
    if end - start > MAX_MESSAGE_SIZE:
        raise ManifestParseError(
            f"{context}: message of {end - start} bytes exceeds the "
            f"{MAX_MESSAGE_SIZE}-byte bound"
        )

    fields: list[_Field] = []
    pos = start
    while pos < end:
        tag, pos = _read_varint(buf, pos, end)
        field_number = tag >> 3
        wire_type = tag & 0x7

        if wire_type == _WIRE_VARINT:
            value, pos = _read_varint(buf, pos, end)
        elif wire_type == _WIRE_LENGTH_DELIMITED:
            length, pos = _read_varint(buf, pos, end)
            if length > MAX_MESSAGE_SIZE:
                raise ManifestParseError(
                    f"{context}: length-delimited field {field_number} at offset {pos} "
                    f"declares {length} bytes, exceeding the {MAX_MESSAGE_SIZE}-byte bound"
                )
            if pos + length > end:
                raise ManifestParseError(
                    f"{context}: length-delimited field {field_number} at offset {pos} "
                    f"declares {length} bytes but only {end - pos} remain"
                )
            value = buf[pos : pos + length]
            pos += length
        elif wire_type == _WIRE_32BIT:
            if pos + 4 > end:
                raise ManifestParseError(
                    f"{context}: truncated 32-bit field {field_number} at offset {pos}"
                )
            value = buf[pos : pos + 4]
            pos += 4
        elif wire_type == _WIRE_64BIT:
            if pos + 8 > end:
                raise ManifestParseError(
                    f"{context}: truncated 64-bit field {field_number} at offset {pos}"
                )
            value = buf[pos : pos + 8]
            pos += 8
        else:
            raise ManifestParseError(
                f"{context}: unsupported protobuf wire type {wire_type} at offset {pos}"
            )

        fields.append(_Field(field_number, wire_type, value))

    return fields


def _first_varint(fields: list[_Field], number: int, what: str, *, context: str) -> int:
    """The first varint-typed value of field ``number``, or raise."""
    for field in fields:
        if field.number == number and field.wire_type == _WIRE_VARINT:
            return field.value  # type: ignore[return-value]
    raise ManifestParseError(f"{context}: missing required field {number} ({what})")


def _all_submessages(fields: list[_Field], number: int) -> list[bytes]:
    """Every length-delimited value of field ``number``, in order."""
    return [
        field.value  # type: ignore[misc]
        for field in fields
        if field.number == number and field.wire_type == _WIRE_LENGTH_DELIMITED
    ]


def _first_bytes(fields: list[_Field], number: int, what: str, *, context: str) -> bytes:
    """The first length-delimited (bytes) value of field ``number``, or raise."""
    for field in fields:
        if field.number == number and field.wire_type == _WIRE_LENGTH_DELIMITED:
            return field.value  # type: ignore[return-value]
    raise ManifestParseError(f"{context}: missing required field {number} ({what})")


def _validate_hex_chunk_id(raw: str, *, context: str) -> str:
    lowered = raw.lower()
    if len(lowered) != CHUNK_ID_HEX_LEN or any(c not in "0123456789abcdef" for c in lowered):
        raise ManifestParseError(
            f"{context}: chunk id {raw!r} is not a {CHUNK_ID_HEX_LEN}-character hex string"
        )
    return lowered


def _add_chunk(chunks: dict[str, int], chunk_id: str, size: int, *, context: str) -> None:
    chunks[chunk_id] = size
    if len(chunks) > MAX_CHUNK_COUNT:
        raise ManifestParseError(
            f"{context}: exceeds the {MAX_CHUNK_COUNT}-chunk sanity bound"
        )


# --------------------------------------------------------------------------
# Format 1: SteamPrefill's own temp-cache .bin files
# --------------------------------------------------------------------------

#: Ordered filename segments per the ``{originalAppId}_{containingAppId}_
#: {depotId}_{manifestId}.bin`` contract (``SteamPrefill/Models/DepotInfo.cs``).
_BIN_FILENAME_SEGMENTS = ("originalAppId", "containingAppId", "depotId", "manifestId")


def parse_bin_filename(path: str) -> tuple[int, int, int, int]:
    """Parse a SteamPrefill temp-cache filename into its four ids.

    Returns ``(original_app_id, containing_app_id, depot_id, manifest_id)``.

    **Public (WP 3.2):** ``parse_steamprefill_bin`` below discards
    ``original_app_id``/``containing_app_id`` after using them for the
    corruption cross-check, because they aren't part of the common
    ``ParsedManifest`` shape GC needs. ``vault_api.manifest_ingest`` calls
    this function directly to recover ``containing_app_id`` — the
    shared-depot attribution signal (research doc: e.g.
    ``107100_228980_229002_...bin`` = app 107100 pulling a depot that
    "belongs" to app 228980) — without duplicating the filename contract.

    **Strict ASCII-digit parsing (``docs/LEARNINGS.md``): every segment
    must be exactly ``isascii() and isdigit()``, not "whatever ``int()``
    accepts".** ``int()`` happily parses ``" 441 "`` (surrounding
    whitespace), ``"+441"`` (a leading sign), ``"1_0"`` (underscore digit
    separators -> 10) and non-ASCII digit characters (e.g. Arabic-Indic
    ``"٤٤١"``) — none of which SteamPrefill ever writes into this filename,
    and on a value that feeds a corruption cross-check against the parsed
    payload, a segment that odd means the file (or its name) is wrong and
    should be rejected, not silently normalized. This mirrors the same rule
    already applied to depot/app ids in ``vault_api/deletion.py``'s
    ``coerce_positive_id`` and ``agent/vault_agent/acf.py``'s
    ``_parse_strict_uint``.
    """
    name = os.path.basename(path)
    if not name.endswith(".bin"):
        raise ManifestParseError(f"{name!r} does not have a .bin extension")

    stem = name[: -len(".bin")]
    parts = stem.split("_")
    if len(parts) != len(_BIN_FILENAME_SEGMENTS):
        raise ManifestParseError(
            f"{name!r} does not match the "
            "{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin filename "
            f"contract (expected {len(_BIN_FILENAME_SEGMENTS)} underscore-separated "
            f"fields, found {len(parts)})"
        )

    values: list[int] = []
    for label, part in zip(_BIN_FILENAME_SEGMENTS, parts):
        if not part or not (part.isascii() and part.isdigit()):
            raise ManifestParseError(
                f"{name!r}: {label} segment {part!r} is not a plain ASCII digit string"
            )
        values.append(int(part))

    return values[0], values[1], values[2], values[3]


def parse_bin_payload(
    path: str,
    *,
    filename_depot_id: int,
    filename_manifest_id: int,
) -> "PrefillManifest":
    """Parse a SteamPrefill-**format** ``.bin`` manifest whose filename this
    function does not itself interpret.

    **Why this exists (WP 3.7).** ``parse_steamprefill_bin`` below is the
    ingestion-path entry point and insists on SteamPrefill's own four-segment
    ``{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin`` filename
    contract. But ``vault_api.manifest_archive`` deliberately re-names its
    durable copies to ``{depotid}_{manifestid}.bin`` (two segments, keyed on
    what GC actually looks up), so garbage collection reading its own archive
    would be rejected by that contract for a file this project wrote itself.
    This function parses the same bytes with the same reader and takes the
    expected ids as **arguments** instead of scraping them out of the name —
    the caller (``vault_api.gc``) knows which (depot, manifest) pair it asked
    for and passes them, so the corruption cross-check is preserved rather
    than dropped.

    ``filename_depot_id``/``filename_manifest_id`` are **required keyword
    arguments with no default** (WP 3.8, closing a WP 3.7 review nitpick).
    They started out optional, which made "give up the corruption
    cross-check" something a caller could do by simply not typing it — the
    quietest possible way to lose a safety property, and one no reviewer can
    see at a call site. Every caller knows which (depot, manifest) pair it
    asked for: ``parse_steamprefill_bin`` scrapes them out of SteamPrefill's
    four-segment filename, and ``vault_api.gc`` has them from
    ``depot_manifests``. The comparisons below are correspondingly
    unconditional — the same rule ``deletion.delete_app_depots`` applies to
    its ``co_owners`` argument: passing ``None`` explicitly is not a second,
    quieter spelling of "skip the check", it simply fails the parse (``id !=
    None`` is true), which is the fail-closed direction.

    Raises ``ManifestParseError`` for: a file that can't be read, an empty
    file, a file bigger than ``MAX_MESSAGE_SIZE``, any malformed/truncated
    protobuf content, a missing required field, an id mismatch against the
    expected ids, or more chunks than ``MAX_CHUNK_COUNT``. No other exception
    escapes.
    """
    context = f"steamprefill .bin {path!r}"

    try:
        with open(path, "rb") as handle:
            buf = handle.read(MAX_MESSAGE_SIZE + 1)
    except OSError as exc:
        raise ManifestParseError(f"cannot read {path}: {exc}") from exc

    if len(buf) > MAX_MESSAGE_SIZE:
        raise ManifestParseError(f"{context}: exceeds the {MAX_MESSAGE_SIZE}-byte bound")
    if not buf:
        raise ManifestParseError(f"{context}: empty file")

    top_fields = _read_fields(buf, 0, len(buf), context=context)
    manifest_id = _first_varint(top_fields, 2, "manifest id", context=context)
    depot_id = _first_varint(top_fields, 4, "depot id", context=context)

    if depot_id != filename_depot_id:
        raise ManifestParseError(
            f"{context}: payload depot id {depot_id} does not match filename depot id "
            f"{filename_depot_id} (corruption signal)"
        )
    if manifest_id != filename_manifest_id:
        raise ManifestParseError(
            f"{context}: payload manifest id {manifest_id} does not match filename "
            f"manifest id {filename_manifest_id} (corruption signal)"
        )

    chunks: dict[str, int] = {}
    for file_data_bytes in _all_submessages(top_fields, 1):
        file_data_fields = _read_fields(
            file_data_bytes, 0, len(file_data_bytes), context=context
        )
        for chunk_bytes in _all_submessages(file_data_fields, 1):
            chunk_fields = _read_fields(chunk_bytes, 0, len(chunk_bytes), context=context)
            raw_chunk_id = _first_bytes(chunk_fields, 1, "chunk id", context=context)
            try:
                chunk_id_text = raw_chunk_id.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ManifestParseError(f"{context}: chunk id is not ASCII: {exc}") from exc
            chunk_id = _validate_hex_chunk_id(chunk_id_text, context=context)
            compressed_length = _first_varint(
                chunk_fields, 2, "chunk compressed length", context=context
            )
            _add_chunk(chunks, chunk_id, compressed_length, context=context)

    return ParsedManifest(
        depot_id=depot_id,
        manifest_id=manifest_id,
        chunks=chunks,
        source=SOURCE_STEAMPREFILL_BIN,
    )


def parse_steamprefill_bin(path: str) -> "PrefillManifest":
    """Parse one SteamPrefill temp-cache ``.bin`` manifest file.

    Validates the filename contract, parses the protobuf payload (manifest
    id, depot id, and every chunk id/compressed-length pair nested under
    the repeated ``FileData``/``ChunkData`` fields), and **cross-checks the
    filename's depot/manifest ids against the payload's** — a mismatch is
    treated as a corruption signal and raises ``ManifestParseError``, per
    this work package's scope.

    Raises ``ManifestParseError`` for: a filename that doesn't match the
    contract, a file that can't be read, an empty file, a file bigger than
    ``MAX_MESSAGE_SIZE``, any malformed/truncated protobuf content, a
    missing required field, an id mismatch between filename and payload, or
    more chunks than ``MAX_CHUNK_COUNT``. No other exception escapes.

    The payload parsing itself lives in ``parse_bin_payload`` above (split out
    in WP 3.7 so GC can read ``vault_api.manifest_archive``'s deliberately
    differently-named copies); this function is exactly "the filename contract
    plus that parse", unchanged in behaviour.
    """
    original_app_id, containing_app_id, filename_depot_id, filename_manifest_id = (
        parse_bin_filename(path)
    )
    del original_app_id, containing_app_id  # not part of the common GC shape (item 4)

    return parse_bin_payload(
        path,
        filename_depot_id=filename_depot_id,
        filename_manifest_id=filename_manifest_id,
    )


# --------------------------------------------------------------------------
# Format 2: cache-stored client manifests (ZIP -> sectioned stream -> protobuf)
# --------------------------------------------------------------------------

_ZIP_ENTRY_NAME = "z"

#: How many names to show in the "no 'z' entry" error before truncating (WP
#: 3.2 review carry-over — see ``_read_zip_entry``'s caller).
_NAMELIST_ERROR_PREVIEW = 10

_MAGIC_PAYLOAD = 0x71F617D0
_MAGIC_METADATA = 0x1F4812BE
_MAGIC_SIGNATURE = 0x1B81B817
_MAGIC_END = 0x32C415AB

_SECTION_HEADER = struct.Struct("<II")  # magic, length — both little-endian u32
_END_MAGIC_STRUCT = struct.Struct("<I")


def _read_sections(data: bytes, *, context: str) -> dict[int, bytes]:
    """Split the sectioned manifest stream into ``{magic: payload_bytes}``.

    Each section is ``u32 magic + u32 length (LE) + <length> bytes`` — the
    **one exception is the END marker**, which is a bare 4-byte magic with
    no length or payload (confirmed empirically against real cache-stored
    manifests while writing this parser; the research document didn't spell
    this out). The loop always advances by at least 4 bytes per iteration
    (the magic alone terminates it on END), so it cannot spin — bounded
    overall by ``len(data)``, which is itself bounded by ``MAX_MESSAGE_SIZE``
    at the call site.

    Truncated input (missing END marker, a section whose declared length
    runs past the end of the buffer, a dangling 1-3 byte magic) raises
    ``ManifestParseError`` rather than returning a partial result.

    **A repeated section magic (e.g. two METADATA sections): last one wins.**
    ``sections[magic] = ...`` is a plain dict assignment executed once per
    section encountered, in stream order, so a later section with the same
    magic silently overwrites an earlier one rather than raising or keeping
    the first. Pinned by
    ``test_cache_manifest_duplicate_metadata_section_last_one_wins``.
    """
    sections: dict[int, bytes] = {}
    pos = 0
    n = len(data)

    while True:
        if pos + 4 > n:
            raise ManifestParseError(
                f"{context}: truncated section magic at offset {pos} ({n - pos} bytes left)"
            )
        (magic,) = _END_MAGIC_STRUCT.unpack_from(data, pos)
        if magic == _MAGIC_END:
            return sections

        if pos + _SECTION_HEADER.size > n:
            raise ManifestParseError(
                f"{context}: truncated section length for magic {magic:#x} at offset {pos}"
            )
        _magic_again, length = _SECTION_HEADER.unpack_from(data, pos)
        pos += _SECTION_HEADER.size

        if length > MAX_MESSAGE_SIZE:
            raise ManifestParseError(
                f"{context}: section {magic:#x} declares {length} bytes, exceeding the "
                f"{MAX_MESSAGE_SIZE}-byte bound"
            )
        if pos + length > n:
            raise ManifestParseError(
                f"{context}: section {magic:#x} declares {length} bytes but only "
                f"{n - pos} remain"
            )

        sections[magic] = data[pos : pos + length]
        pos += length


def _read_zip_entry(path: str) -> bytes:
    """Read the single ``z`` entry of a cache-stored manifest ZIP, bounded.

    Deliberately **not** ``ZipFile.read(name)``: that decompresses the
    whole entry into memory before this module gets a chance to check
    anything, trusting the archive's own declared sizes. Reading through
    ``ZipExtFile`` with an explicit ``read(MAX_MESSAGE_SIZE + 1)`` instead
    bounds actual memory use regardless of what the entry's ``file_size``/
    ``compress_size`` header fields claim.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                info = archive.getinfo(_ZIP_ENTRY_NAME)
            except KeyError as exc:
                # WP 3.2 review carry-over (from the WP 3.1 review): a real
                # zip can list tens of thousands of names, and this exception
                # message used to embed the FULL namelist — a 50k-entry zip
                # produced a ~2 MB exception message, and WP 3.2's ingestion
                # logs this per bad file it encounters. Bounded to 10 names +
                # the total count instead.
                names = archive.namelist()
                shown = names[:_NAMELIST_ERROR_PREVIEW]
                omitted = len(names) - len(shown)
                suffix = "" if omitted <= 0 else f", ... ({omitted} more)"
                raise ManifestParseError(
                    f"{path!r}: zip has no {_ZIP_ENTRY_NAME!r} entry "
                    f"(found {len(names)} entries, first {len(shown)}: {shown!r}{suffix})"
                ) from exc
            with archive.open(info) as entry:
                data = entry.read(MAX_MESSAGE_SIZE + 1)
    except zipfile.BadZipFile as exc:
        raise ManifestParseError(f"{path!r}: not a valid zip file: {exc}") from exc
    except OSError as exc:
        raise ManifestParseError(f"cannot read {path!r}: {exc}") from exc
    except (
        zlib.error,  # corrupt deflate stream
        EOFError,  # truncated compressed stream (bz2/lzma)
        NotImplementedError,  # unsupported compression method
        RuntimeError,  # encrypted entry ("password required")
        ValueError,  # e.g. lzma/bz2 decoder errors on a lying header
    ) as exc:
        # WP SEC-FIX-4 (S-4): these escaped the documented catch contract and
        # crashed a whole GC run on one bad file planted in the cache.
        raise ManifestParseError(
            f"{path!r}: unreadable zip entry ({type(exc).__name__}: {exc})"
        ) from exc

    if len(data) > MAX_MESSAGE_SIZE:
        raise ManifestParseError(
            f"{path!r}: {_ZIP_ENTRY_NAME!r} entry exceeds the {MAX_MESSAGE_SIZE}-byte bound"
        )
    return data


def parse_cache_manifest(path: str) -> "CacheManifest":
    """Parse one cache-stored client manifest
    (``/cache/depot/<id>/manifest/<manifestid>/5/<requestcode>``).

    Opens the ZIP, reads its single ``z`` entry (bounded — see
    ``_read_zip_entry``), splits the sectioned stream into METADATA/PAYLOAD/
    SIGNATURE, parses METADATA for ``depot_id``/``gid_manifest``/
    ``unique_chunks``, parses PAYLOAD's ``FileMapping`` -> ``ChunkData``
    entries for chunk sha/``cb_compressed`` pairs, and **self-checks the
    parsed chunk count against METADATA's ``unique_chunks``** (ADR-0007) —
    a mismatch raises ``ManifestParseError`` as a corruption signal, exactly
    like the filename/payload cross-check in ``parse_steamprefill_bin``.

    Filenames inside PAYLOAD's ``FileMapping`` entries are Valve-encrypted
    (need the depot decryption key, which vault-api never holds) and are
    **never read** by this function — garbage collection only needs chunk
    SHAs, which are not encrypted, so this is not a limitation for this
    project's purposes.

    Raises ``ManifestParseError`` for: a file that isn't a valid zip, a zip
    without a ``z`` entry, an empty or oversized ``z`` entry, a truncated or
    malformed sectioned stream, a missing METADATA or PAYLOAD section, a
    missing required protobuf field, a chunk sha that isn't 20 bytes, or a
    parsed-chunk-count / ``unique_chunks`` mismatch. No other exception
    escapes.
    """
    context = f"cache manifest {path!r}"

    data = _read_zip_entry(path)
    if not data:
        raise ManifestParseError(f"{context}: empty manifest stream")

    sections = _read_sections(data, context=context)

    if _MAGIC_METADATA not in sections:
        raise ManifestParseError(f"{context}: missing METADATA section")
    if _MAGIC_PAYLOAD not in sections:
        raise ManifestParseError(f"{context}: missing PAYLOAD section")
    # SIGNATURE (_MAGIC_SIGNATURE) is present in real files but unused here —
    # GC needs neither the signature nor any field it would authenticate.

    metadata_fields = _read_fields(
        sections[_MAGIC_METADATA], 0, len(sections[_MAGIC_METADATA]), context=context
    )
    depot_id = _first_varint(metadata_fields, 1, "depot_id", context=context)
    manifest_id = _first_varint(metadata_fields, 2, "gid_manifest", context=context)
    unique_chunks = _first_varint(metadata_fields, 7, "unique_chunks", context=context)

    payload_fields = _read_fields(
        sections[_MAGIC_PAYLOAD], 0, len(sections[_MAGIC_PAYLOAD]), context=context
    )

    chunks: dict[str, int] = {}
    for file_mapping_bytes in _all_submessages(payload_fields, 1):
        file_mapping_fields = _read_fields(
            file_mapping_bytes, 0, len(file_mapping_bytes), context=context
        )
        for chunk_bytes in _all_submessages(file_mapping_fields, 6):
            chunk_fields = _read_fields(chunk_bytes, 0, len(chunk_bytes), context=context)
            sha = _first_bytes(chunk_fields, 1, "chunk sha", context=context)
            if len(sha) != 20:
                raise ManifestParseError(
                    f"{context}: chunk sha is {len(sha)} bytes, expected 20 (SHA-1)"
                )
            cb_compressed = _first_varint(
                chunk_fields, 5, "cb_compressed", context=context
            )
            _add_chunk(chunks, sha.hex(), cb_compressed, context=context)

    if len(chunks) != unique_chunks:
        raise ManifestParseError(
            f"{context}: parsed {len(chunks)} unique chunk(s) but METADATA declares "
            f"unique_chunks={unique_chunks} (corruption signal)"
        )

    return ParsedManifest(
        depot_id=depot_id,
        manifest_id=manifest_id,
        chunks=chunks,
        source=SOURCE_CACHE_MANIFEST,
    )
