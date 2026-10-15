"""The conftest guard: the manifest oracle never reaches the network in tests."""

from __future__ import annotations

import pytest

from vault_api import oracle


def test_oracle_http_fetch_is_refused_by_default() -> None:
    with pytest.raises(oracle.OracleError, match="without a fake"):
        oracle.http_fetch("http://example.invalid/app/440")


@pytest.mark.real_http_fetch
def test_the_marker_opts_out_to_the_real_function() -> None:
    # The real function runs its scheme check first: no network is touched.
    with pytest.raises(oracle.OracleError, match="only http/https"):
        oracle.http_fetch("file:///etc/passwd")
