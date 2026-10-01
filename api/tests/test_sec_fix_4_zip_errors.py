"""WP SEC-FIX-4 S-4: decoder errors from a planted bad zip are ManifestParseError.

Before the fix, ``zlib.error`` / ``NotImplementedError`` / ``RuntimeError``
escaped ``parse_cache_manifest``'s documented catch contract, and one such
file in the cache crashed a whole GC run. Fixtures are built in code
(the reviewer's zprobe.py shape): a valid one-entry zip, then patched.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from vault_api.manifests import ManifestParseError, parse_cache_manifest


def _zip(
    path: Path,
    payload: bytes,
    *,
    method: int | None = None,
    flag_bits: int = 0,
    corrupt: bool = False,
) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("z", payload)
    data = bytearray(buf.getvalue())
    central = data.rfind(b"PK\x01\x02")
    if corrupt:
        # Local header is 30 bytes + the 1-byte name "z": flip the deflate start.
        for index in range(31, 39):
            data[index] ^= 0xFF
    if method is not None:
        data[8:10] = struct.pack("<H", method)
        data[central + 10 : central + 12] = struct.pack("<H", method)
    if flag_bits:
        data[6:8] = struct.pack("<H", flag_bits)
        data[central + 8 : central + 10] = struct.pack("<H", flag_bits)
    path.write_bytes(bytes(data))
    return path


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"payload": b"A" * 5000, "corrupt": True}, id="corrupt-deflate"),
        pytest.param({"payload": b"A" * 100, "method": 99}, id="unsupported-method"),
        pytest.param({"payload": b"A" * 100, "flag_bits": 1}, id="encrypted-flag"),
    ],
)
def test_a_bad_zip_entry_is_a_manifest_parse_error(tmp_path: Path, kwargs) -> None:
    path = _zip(tmp_path / "bad.zip", **kwargs)
    with pytest.raises(ManifestParseError, match="bad.zip"):
        parse_cache_manifest(str(path))
