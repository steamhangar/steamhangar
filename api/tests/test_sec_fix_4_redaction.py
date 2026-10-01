"""WP SEC-FIX-4 P-1 / N-1: secrets in operator-configured URLs stay out of logs."""

from __future__ import annotations

import logging
import types

import pytest

from vault_api import config, oracle, webhooks

SECRET = "s3cr3t"


@pytest.mark.parametrize("sep", ["/", "?", "#"])
def test_an_unencoded_delimiter_in_the_webhook_password_is_refused(sep: str) -> None:
    raw = f"https://hook:pa{sep}{SECRET}@hooks.example.net/x"
    with pytest.raises(ValueError, match="percent-encoded") as caught:
        config.validate_webhook_url(raw)
    assert SECRET not in str(caught.value)


def test_a_percent_encoded_webhook_password_is_accepted() -> None:
    raw = f"https://hook:pa%2F{SECRET}@hooks.example.net/x"
    assert config.validate_webhook_url(raw) == raw


@pytest.mark.parametrize("sep", ["/", "?", "#"])
def test_redact_url_over_redacts_an_unencoded_delimiter(sep: str) -> None:
    """Defence in depth for VAULT_WEBHOOK_URL, which startup does not validate."""
    redacted = webhooks.redact_url(f"https://hook:pa{sep}{SECRET}@hooks.example.net/x")
    assert SECRET not in redacted
    assert redacted.endswith("@hooks.example.net/x")


def test_the_oracle_error_hides_userinfo_and_query() -> None:
    url = f"http://user:{SECRET}@127.0.0.1:1/app/440?token={SECRET}"
    with pytest.raises(oracle.OracleError) as caught:
        oracle.http_fetch(url, timeout=2)
    message = str(caught.value)
    assert SECRET not in message
    assert "***@127.0.0.1:1/app/440" in message


def test_the_oracle_info_line_hides_userinfo_and_query(monkeypatch, caplog) -> None:
    monkeypatch.setattr(oracle, "http_fetch", lambda url, timeout: b"{}")
    fetch = oracle._configured_fetcher(
        types.SimpleNamespace(manifest_oracle_timeout=1)
    )
    with caplog.at_level(logging.INFO, logger="vault_api.oracle"):
        fetch(f"https://user:{SECRET}@oracle.example.net/v1/info/440?key={SECRET}")
    assert "querying https://***@oracle.example.net/v1/info/440" in caplog.text
    assert SECRET not in caplog.text


def test_url_for_log_hides_userinfo_pushed_into_the_path() -> None:
    assert SECRET not in oracle.url_for_log(f"https://u:p/{SECRET}@h/x")
