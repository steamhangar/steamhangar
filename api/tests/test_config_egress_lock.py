"""WP EG-1 (ADR-0011) — ``VAULT_EGRESS_ALLOW`` parsing and the manifest-
oracle fail-loud startup check, both in ``vault_api/config.py``.

The actual egress ENFORCEMENT lives entirely in ``deploy/proxy`` (a separate
container vault-api has no visibility into) — this module tests the one
thing vault-api's own process does with this setting: parse it the same way
the proxy's entrypoint does (so a value one side would reject is not
silently accepted by the other), and refuse to boot if the manifest oracle
is turned on with its host missing from the list (the "cheapest honest
version" of a startup check the work package brief asked to be argued for,
not assumed — see ``Settings.from_env``'s own comment on ``egress_allow``
for why this lives in ``from_env`` and not ``Settings.__post_init__``:
several of this project's OWN existing tests construct ``Settings`` directly
with ``manifest_oracle`` set and no allowlist at all, in contexts that have
nothing to do with this work package, and must keep passing unchanged).
"""

from __future__ import annotations

import pytest

from vault_api.config import (
    MANIFEST_ORACLE_STEAMCMD_API,
    Settings,
)


# ==========================================================================
# 1. VAULT_EGRESS_ALLOW parsing
# ==========================================================================


def test_egress_allow_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.delenv("VAULT_EGRESS_ALLOW", raising=False)
    assert Settings.from_env().egress_allow == frozenset()
    # A directly constructed Settings (every other test fixture in this
    # suite) must default the same way, matching this project's own house
    # style for every other feature-off default (see test_oracle.py's
    # analogous assertion for manifest_oracle_enabled).
    assert (
        Settings(
            vault_api_key="k", db_path=":memory:", cache_root="c", log_level="INFO"
        ).egress_allow
        == frozenset()
    )


def test_egress_allow_parses_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "api.steamcmd.net,discord.com")
    settings = Settings.from_env()
    assert settings.egress_allow == frozenset({"api.steamcmd.net", "discord.com"})


def test_egress_allow_trims_whitespace_and_lowercases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "  API.STEAMCMD.NET , Discord.Com ")
    settings = Settings.from_env()
    assert settings.egress_allow == frozenset({"api.steamcmd.net", "discord.com"})


def test_egress_allow_drops_blank_entries_from_stray_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "api.steamcmd.net,,discord.com,")
    settings = Settings.from_env()
    assert settings.egress_allow == frozenset({"api.steamcmd.net", "discord.com"})


def test_egress_allow_a_lone_blank_value_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "   ")
    assert Settings.from_env().egress_allow == frozenset()


@pytest.mark.parametrize(
    "bad_entry",
    [
        "api.steamcmd.net/v1/info",  # a path, not a bare host
        "http://api.steamcmd.net",  # a scheme, not a bare host
        "api steamcmd net",  # internal whitespace
        "api.steamcmd.net;rm -rf /",  # shell-metacharacter injection attempt
        "évil.example.com",  # non-ASCII
        ".steamcmd.net",  # leading dot
        "steamcmd.net.",  # trailing dot
        "-steamcmd.net",  # leading hyphen
        "steamcmd.net-",  # trailing hyphen
    ],
)
def test_egress_allow_rejects_implausible_hostnames(
    monkeypatch: pytest.MonkeyPatch, bad_entry: str
) -> None:
    """Fails loudly at BOOT, not by silently dropping the bad entry (which
    would leave an operator believing a host is allowlisted when the value
    that reached this process was never valid to begin with) and not by
    passing it through unsanitized (this value is rendered verbatim into
    the proxy container's tinyproxy filter file on the OTHER side --
    deploy/proxy/docker-entrypoint.sh applies the identical character
    allowlist independently, see test_eg1_egress_lock.py's cross-file pin).
    """
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", bad_entry)
    with pytest.raises(RuntimeError, match="VAULT_EGRESS_ALLOW"):
        Settings.from_env()


def test_egress_allow_accepts_a_realistic_multi_label_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Precondition sibling to the rejection cases above: a real, valid
    hostname with multiple labels and a hyphen must NOT be rejected by the
    same check that refuses the implausible ones.
    """
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "my-webhook-receiver.example.co.uk")
    settings = Settings.from_env()
    assert settings.egress_allow == frozenset({"my-webhook-receiver.example.co.uk"})


# ==========================================================================
# 2. The manifest-oracle fail-loud startup check
# ==========================================================================


def test_oracle_on_with_a_mirror_missing_from_allowlist_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Since WP UPD-1 only the DEFAULT oracle host is allowed automatically
    (tests/test_upd_1_oracle_egress.py); any other host the URL names still
    needs an allowlist entry, or vault-api refuses to boot."""
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", "https://mirror.example.net/v1/info")
    monkeypatch.delenv("VAULT_EGRESS_ALLOW", raising=False)
    with pytest.raises(RuntimeError, match="VAULT_EGRESS_ALLOW"):
        Settings.from_env()


def test_oracle_on_with_the_default_host_boots_without_an_allowlist_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.delenv("VAULT_MANIFEST_ORACLE_URL", raising=False)
    monkeypatch.delenv("VAULT_EGRESS_ALLOW", raising=False)
    assert Settings.from_env().manifest_oracle_enabled is True


def test_oracle_on_with_host_present_in_allowlist_boots_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "api.steamcmd.net")
    settings = Settings.from_env()
    assert settings.manifest_oracle == MANIFEST_ORACLE_STEAMCMD_API
    assert settings.egress_allow == frozenset({"api.steamcmd.net"})


def test_oracle_off_ignores_the_allowlist_entirely_for_this_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is gated on the oracle actually being ON — an operator who
    never turns it on should never see it mentioned, regardless of what
    VAULT_EGRESS_ALLOW does or does not contain.
    """
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "off")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", "https://mirror.example.net/v1/info")
    monkeypatch.delenv("VAULT_EGRESS_ALLOW", raising=False)
    settings = Settings.from_env()
    assert settings.manifest_oracle_enabled is False


def test_oracle_check_matches_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "API.STEAMCMD.NET")
    settings = Settings.from_env()
    assert settings.manifest_oracle_enabled is True


def test_oracle_check_follows_a_custom_mirror_url_not_the_default_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check must cross-reference the host the operator ACTUALLY
    configured (VAULT_MANIFEST_ORACLE_URL), not a hardcoded
    "api.steamcmd.net" literal — an operator pointing at their own private
    mirror (deploy/.env.example's own PRIVACY NOTE) must allowlist THAT
    host, and allowlisting the public default instead must not satisfy it.
    """
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", "https://my-private-mirror.example.net/v1/info")
    # The PUBLIC default host, deliberately NOT the configured mirror's own
    # host -- this must still fail.
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "api.steamcmd.net")
    with pytest.raises(RuntimeError, match="VAULT_EGRESS_ALLOW"):
        Settings.from_env()

    # The actually-configured mirror host -- this must succeed.
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "my-private-mirror.example.net")
    settings = Settings.from_env()
    assert settings.manifest_oracle_enabled is True


def test_oracle_check_error_message_names_the_actual_missing_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error an operator actually sees must name the REAL host it
    computed, not a generic "something is wrong" -- this is what makes the
    check actionable rather than merely loud.
    """
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd_api")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", "https://mirror.example.net/v1/info")
    monkeypatch.delenv("VAULT_EGRESS_ALLOW", raising=False)
    with pytest.raises(RuntimeError, match="mirror.example.net"):
        Settings.from_env()


def test_direct_construction_with_oracle_on_and_no_allowlist_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is deliberately NOT in ``Settings.__post_init__`` (see this
    module's own docstring) -- a directly constructed ``Settings`` with the
    oracle on and an empty allowlist must NOT raise, because this project's
    own existing tests (test_gc_execute.py's ``dataclasses.replace``,
    test_oracle.py's direct fixtures) already do exactly this and have
    nothing to do with the egress lock. Mutation-tested in the direction
    that matters: if a future edit moved the check into ``__post_init__``,
    THIS is the test that would fail first, before any of the WP EG-1-
    specific ones above needed to explain why.
    """
    settings = Settings(
        vault_api_key="k",
        db_path=":memory:",
        cache_root="c",
        log_level="INFO",
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )
    assert settings.manifest_oracle_enabled is True
    assert settings.egress_allow == frozenset()
