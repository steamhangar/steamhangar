from __future__ import annotations

import pytest

from vault_api import config
from vault_api.config import Settings


def test_from_env_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VAULT_API_KEY"):
        Settings.from_env()


def test_from_env_raises_when_api_key_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="VAULT_API_KEY"):
        Settings.from_env()


def test_from_env_allows_a_missing_api_key_when_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP S-1 round-2 (review S2): ``prefill_runner`` never serves HTTP and
    never authenticates a request, so it has no legitimate use for the LAN
    control-plane secret — ``require_api_key=False`` is what lets it load
    the rest of ``Settings`` (db_path, steamprefill_path, the runner
    tunables) without also needing VAULT_API_KEY injected into its
    environment."""
    monkeypatch.delenv("VAULT_API_KEY", raising=False)

    settings = Settings.from_env(require_api_key=False)

    assert settings.vault_api_key == ""


def test_from_env_still_defaults_to_requiring_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default must stay strict — only prefill_runner's explicit opt-out
    may skip this check; vault-api's own boot path (``create_app`` ->
    ``Settings.from_env()`` with no arguments) must not accidentally relax."""
    monkeypatch.delenv("VAULT_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="VAULT_API_KEY"):
        Settings.from_env()


def test_from_env_uses_defaults_when_optional_vars_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_DB_PATH", raising=False)
    monkeypatch.delenv("VAULT_CACHE_ROOT", raising=False)
    monkeypatch.delenv("VAULT_LOG_LEVEL", raising=False)

    settings = Settings.from_env()

    assert settings.vault_api_key == "some-key"
    assert settings.db_path == "./vault.db"
    assert settings.cache_root == "./cache"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("blank_value", ["", "   ", "\t"])
def test_from_env_raises_when_cache_root_is_blank(
    monkeypatch: pytest.MonkeyPatch, blank_value: str
) -> None:
    """WP 4f: an explicitly blank ``VAULT_CACHE_ROOT`` must refuse to boot
    rather than silently falling through to `os.getcwd()`-adjacent behaviour
    later (`deletion.resolve_depot_root`) or a `ValueError` deep inside a
    background sweep thread (`scheduler.compute_targets`). Unlike
    `VAULT_API_KEY`, there IS a usable default here (`./cache`) -- it only
    applies when the key is ABSENT, never when it is present-but-blank
    (`os.environ.get`'s own contract), which is the gap this guards.

    S3 (reviewer correction, 2026-08-18 review round): the realistic source
    of a present-but-blank value is NOT an unforwarded compose key -- an
    unforwarded key is simply absent from `os.environ` in the container, and
    the `./cache` default applies fine. It is a key that IS forwarded via
    `${VAULT_CACHE_ROOT}` interpolation with nothing set in `.env` (compose
    then renders `VAULT_CACHE_ROOT=` in the container's environment), or a
    bare `KEY:`/`ENV KEY=` in a derived image."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_CACHE_ROOT", blank_value)

    with pytest.raises(RuntimeError, match="VAULT_CACHE_ROOT"):
        Settings.from_env()


def test_from_env_reads_all_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("VAULT_CACHE_ROOT", "/tmp/cache")
    monkeypatch.setenv("VAULT_LOG_LEVEL", "DEBUG")

    settings = Settings.from_env()

    assert settings.db_path == "/tmp/custom.db"
    assert settings.cache_root == "/tmp/cache"
    assert settings.log_level == "DEBUG"


def test_prefill_settings_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_STEAMPREFILL_PATH",
        "VAULT_PREFILL_TIMEOUT_SECONDS",
        "VAULT_WORKER_POLL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    # No default path on purpose: a missing SteamPrefill must fail JOBS with a
    # clear message, not stop vault-api from starting (WP 1.4).
    assert settings.steamprefill_path == ""
    assert settings.prefill_timeout_seconds == 14400
    assert settings.worker_poll_seconds == 1.0


def test_prefill_settings_read_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_STEAMPREFILL_PATH", r"C:\tools\SteamPrefill.exe")
    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("VAULT_WORKER_POLL_SECONDS", "0.25")

    settings = Settings.from_env()

    assert settings.steamprefill_path == r"C:\tools\SteamPrefill.exe"
    assert settings.prefill_timeout_seconds == 60
    assert settings.worker_poll_seconds == 0.25


def test_bad_numeric_settings_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "soon")
    with pytest.raises(RuntimeError, match="VAULT_PREFILL_TIMEOUT_SECONDS"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="must be > 0"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("VAULT_WORKER_POLL_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="VAULT_WORKER_POLL_SECONDS"):
        Settings.from_env()


def test_agent_report_keep_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_AGENT_REPORT_KEEP", raising=False)
    assert Settings.from_env().agent_report_keep == 20

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "5")
    assert Settings.from_env().agent_report_keep == 5


def test_manifest_archive_dir_defaults_next_to_the_db_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import os

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_MANIFEST_ARCHIVE_DIR", raising=False)
    db_path = str(tmp_path / "sub" / "vault.db")
    monkeypatch.setenv("VAULT_DB_PATH", db_path)

    settings = Settings.from_env()

    assert settings.manifest_archive_dir == os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "manifests"
    )


def test_manifest_archive_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    override = str(tmp_path / "custom-manifests")
    monkeypatch.setenv("VAULT_MANIFEST_ARCHIVE_DIR", override)

    assert Settings.from_env().manifest_archive_dir == override


def test_manifest_keep_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_MANIFEST_KEEP", raising=False)
    assert Settings.from_env().manifest_keep == 3

    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "5")
    assert Settings.from_env().manifest_keep == 5


def test_manifest_keep_below_one_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    # minimum=1 phrases as "> 0" (_env_int's existing wording rule, same as
    # VAULT_PREFILL_TIMEOUT_SECONDS/VAULT_WORKER_POLL_SECONDS above).
    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "0")
    with pytest.raises(RuntimeError, match=r"must be > 0"):
        Settings.from_env()

    # WP 3.12: a NEGATIVE value is now refused one step earlier, by the
    # digits-only syntax rule, and its message names the smallest accepted
    # value instead of the ">" phrasing. Still a loud startup RuntimeError —
    # only the wording moved.
    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "-1")
    with pytest.raises(RuntimeError, match=r"ASCII digits only"):
        Settings.from_env()


def test_steamprefill_cache_dir_has_a_platform_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_STEAMPREFILL_CACHE_DIR", raising=False)

    settings = Settings.from_env()

    assert settings.steamprefill_cache_dir  # never blank
    assert settings.steamprefill_cache_dir.endswith(
        os.path.join("SteamPrefill", "v1")
    )


def test_steamprefill_cache_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    override = str(tmp_path / "custom-cache")
    monkeypatch.setenv("VAULT_STEAMPREFILL_CACHE_DIR", override)

    assert Settings.from_env().steamprefill_cache_dir == override


def test_scheduler_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safe default (WP 3.5): no window = vault-api schedules nothing.

    A fresh install must not start Steam logins and downloads on its own just
    because nobody read the docs yet.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_SCHEDULE_WINDOW",
        "VAULT_SCHEDULE_INTERVAL_MINUTES",
        "VAULT_SCHEDULE_CLIENT_STALE_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.schedule_window is None
    assert settings.scheduler_enabled is False
    # Plan §7 Phase 3's "every 3 h", and the documented staleness bound.
    assert settings.schedule_interval_minutes == 180
    assert settings.schedule_client_stale_days == 7


def test_schedule_window_is_parsed_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "09:00-17:00")
    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "60")
    monkeypatch.setenv("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "3")

    settings = Settings.from_env()

    assert settings.scheduler_enabled is True
    assert settings.schedule_window is not None
    assert settings.schedule_window.raw == "09:00-17:00"
    assert settings.schedule_window.overnight is False
    assert settings.schedule_interval_minutes == 60
    assert settings.schedule_client_stale_days == 3


def test_an_overnight_window_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "22:00-06:00")

    window = Settings.from_env().schedule_window

    assert window is not None and window.overnight is True


def test_a_blank_window_disables_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'unset' and 'set to spaces' must mean the same thing (a commented-out
    line in .env that kept a trailing space is not a config error)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "   ")

    assert Settings.from_env().scheduler_enabled is False


def test_a_malformed_window_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not on the first tick, hours later, inside a background thread."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for bad in ("9-5", "09:00", "09:00-09:00", "24:00-06:00", "09:00-25:00"):
        monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", bad)
        with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_WINDOW is invalid"):
            Settings.from_env()


def test_bad_schedule_numbers_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validated even with no window set, so a typo surfaces on the day it is
    made rather than the day the operator enables the scheduler."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_SCHEDULE_WINDOW", raising=False)

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "0")
    with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_INTERVAL_MINUTES"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "three hours")
    with pytest.raises(RuntimeError, match="ASCII digits only"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "180")
    monkeypatch.setenv("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "-1")
    with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_CLIENT_STALE_DAYS"):
        Settings.from_env()


def test_gc_grace_days_defaults_to_fourteen(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP 3.8b / ADR-0007 addendum A: the grace window is ON by default.

    This is the one setting in this file whose default is a *protection*, so
    the default itself is the feature: an operator who never reads the docs
    still keeps beta-branch and other store-on-miss content for a fortnight.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_GC_GRACE_DAYS", raising=False)

    assert Settings.from_env().gc_grace_days == 14

    monkeypatch.setenv("VAULT_GC_GRACE_DAYS", "30")
    assert Settings.from_env().gc_grace_days == 30


def test_gc_grace_days_zero_is_a_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike VAULT_MANIFEST_KEEP, 0 means something here: no window at all.

    It must therefore be *accepted*, not rejected as "below the floor" — and
    the executor turns it into "no predicate is constructed"
    (``gc_execute.grace_window_exclusions``).
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_GC_GRACE_DAYS", "0")

    assert Settings.from_env().gc_grace_days == 0


def test_a_bad_gc_grace_days_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a deletion-path setting must not silently become "protect
    nothing". Rejected at startup, where somebody is looking."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    # WP 3.12: a negative value is refused by the digits-only syntax rule
    # before the floor check ever runs, and its message names the smallest
    # accepted value (0) rather than using the ">=" phrasing. Still a startup
    # RuntimeError — the protection this test exists for is unchanged.
    for bad in ("-1", "-14"):
        monkeypatch.setenv("VAULT_GC_GRACE_DAYS", bad)
        with pytest.raises(
            RuntimeError, match=r"smallest accepted value is 0"
        ):
            Settings.from_env()

    for garbage in ("fourteen", "14 days", "", " ", "1.5"):
        monkeypatch.setenv("VAULT_GC_GRACE_DAYS", garbage)
        if garbage.strip() == "":
            # Blank is "unset" everywhere in this module, not an error.
            assert Settings.from_env().gc_grace_days == 14
            continue
        with pytest.raises(RuntimeError, match="ASCII digits only"):
            Settings.from_env()


def test_agent_report_keep_below_two_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diff needs the previous snapshot AND the new one — 1 is not a value.

    With keep=1 the prune inside the insert transaction would delete the
    predecessor, so every report would come back as a first report.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for bad in ("1", "0"):
        monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", bad)
        with pytest.raises(RuntimeError, match="must be >= 2"):
            Settings.from_env()

    # WP 3.12: negatives now fail the digits-only syntax rule first (the
    # message names the floor, so it is still actionable).
    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "-3")
    with pytest.raises(RuntimeError, match=r"smallest accepted value is 2"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "many")
    with pytest.raises(RuntimeError, match="ASCII digits only"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "2")
    assert Settings.from_env().agent_report_keep == 2


# ==========================================================================
# WP 3.12: strict integer parsing for EVERY integer setting
# ==========================================================================

#: Every ``_env_int``-backed setting, with the attribute it lands on and a
#: valid value. Parameterizing over the whole list is the point: the hardening
#: is a property of ``_env_int``, so a future setting that bypassed it (or a
#: caller that stopped using it) shows up as a failing row here rather than as
#: one un-hardened variable nobody checked.
INTEGER_SETTINGS = [
    ("VAULT_PREFILL_TIMEOUT_SECONDS", "prefill_timeout_seconds", "7"),
    ("VAULT_PREFILL_MAX_THREADS", "prefill_max_threads", "7"),
    ("VAULT_AGENT_REPORT_KEEP", "agent_report_keep", "7"),
    ("VAULT_MANIFEST_KEEP", "manifest_keep", "7"),
    ("VAULT_GC_GRACE_DAYS", "gc_grace_days", "7"),
    ("VAULT_SCHEDULE_INTERVAL_MINUTES", "schedule_interval_minutes", "7"),
    ("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "schedule_client_stale_days", "7"),
]

#: The four shapes Python's own ``int()`` accepts and an operator never means.
#: ``"1_0"`` is the nastiest: ``int("1_0")`` is **ten**, so it fails silently
#: rather than loudly. ``"٧"`` is ARABIC-INDIC DIGIT SEVEN — ``str.isdigit()``
#: is True for it, which is why the ASCII check has to come first.
SLOPPY_INTEGERS = [" 7 ", "+7", "-7", "1_0", "٧", "7\n", "7 ", " 7", "0x7"]


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
def test_a_plain_integer_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """The hardening must not have broken any legitimate value."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, good)

    assert getattr(Settings.from_env(), attribute) == int(good)


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
@pytest.mark.parametrize("sloppy", SLOPPY_INTEGERS)
def test_sloppy_integers_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str, sloppy: str
) -> None:
    """docs/LEARNINGS.md's ``int()`` rule, applied to every integer setting.

    Each of these would otherwise start the service with a number nobody wrote
    down — ``"1_0"`` most of all, which ``int()`` reads as ten.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, sloppy)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
def test_a_blank_integer_setting_still_means_unset(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """A stray space after ``=`` in a .env file must not fail startup — blank
    is "not configured" for every setting in this module, and always has been.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    # The dataclass field default IS the documented default for every one of
    # these settings, so compare against it rather than restating numbers here.
    default = getattr(
        Settings(vault_api_key="k", db_path="x", cache_root="y", log_level="INFO"),
        attribute,
    )

    for blank in ("", "   ", "\t"):
        monkeypatch.setenv(name, blank)
        assert getattr(Settings.from_env(), attribute) == default


def test_every_env_example_value_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped .env.example is the file operators copy — after tightening
    the parser, every value in it must still be accepted (WP 3.12).

    Read from the real file rather than a copy of its contents, so a future
    edit that introduces a value this parser rejects fails here.
    """
    import os

    env_example = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example"
    )
    with open(env_example, encoding="utf-8") as handle:
        lines = [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#") and "=" in line
        ]

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    seen: dict[str, str] = {}
    for line in lines:
        name, _, value = line.partition("=")
        if name == "VAULT_API_KEY":
            continue
        monkeypatch.setenv(name, value)
        seen[name] = value

    # Nothing raises: every documented value is accepted as written.
    settings = Settings.from_env()

    # Both hardened families really were exercised — otherwise a future
    # .env.example that stopped shipping the numeric settings would make this
    # test pass without testing anything.
    assert seen.keys() & {name for name, _attr, _good in INTEGER_SETTINGS}
    assert seen.keys() & {name for name, _attr in FLOAT_SETTINGS}
    assert settings.worker_poll_seconds == float(seen["VAULT_WORKER_POLL_SECONDS"])
    assert settings.size_cache_ttl_seconds == float(seen["VAULT_SIZE_CACHE_TTL"])
    assert settings.gc_grace_days == int(seen["VAULT_GC_GRACE_DAYS"])
    assert len(seen) >= 8, "the .env.example parsing above found suspiciously little"


#: WP SWEEP-1 review round 1 (Opus, blocker B1): `deploy/compose.yaml` has
#: had a `config.DEFAULT_*`-derived value pin since WP P1
#: (`test_p1_compose_env_defaults.py::EXPECTED_DEFAULTS_VAULT_API`) — this
#: file (`api/.env.example`, the bare-metal/native path `api/README.md`
#: tells operators to copy) never got the same treatment, only the
#: "parses without error" check above. That ASYMMETRY is what let this
#: file's `VAULT_SWEEP_INCLUDE_CACHED=off` / `VAULT_AUTO_GC=off` assignments
#: keep asserting the pre-ADR-0014 values, unnoticed, after `config.py`'s own
#: defaults flipped. Deliberately narrow to the two keys that actual drift
#: just hit, not a retrofit of every pre-existing line in this file — same
#: incremental-scope precedent `EXPECTED_DEFAULTS_VAULT_API` itself set
#: (grown one work package at a time, never audited wholesale in one pass).
ENV_EXAMPLE_DEFAULT_PINS: dict[str, str] = {
    "VAULT_SWEEP_INCLUDE_CACHED": "true" if config.DEFAULT_SWEEP_INCLUDE_CACHED else "false",
    "VAULT_AUTO_GC": str(config.DEFAULT_AUTO_GC),
    # WP CORE-FIX-2: deploy/.env.example's twin is pinned in
    # test_core_fix_2_max_threads.py.
    "VAULT_PREFILL_MAX_THREADS": str(config.DEFAULT_PREFILL_MAX_THREADS),
}


@pytest.mark.parametrize("env_var", sorted(ENV_EXAMPLE_DEFAULT_PINS))
def test_env_example_value_matches_config_default(env_var: str) -> None:
    """The VALUE half of the pin `test_every_env_example_value_still_parses`
    above does not provide: that test only proves every line PARSES, not
    that the shipped value is still the one `config.py` actually defaults
    to. Read from the real file (not a hand-copied literal) so a future
    edit to either side that lets them drift is what actually fails here,
    the same "derive, don't hand-copy" discipline
    `test_p1_compose_env_defaults.py` already applies to
    `deploy/compose.yaml`.
    """
    import os

    env_example = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example"
    )
    with open(env_example, encoding="utf-8") as handle:
        lines = [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#") and "=" in line
        ]
    documented: dict[str, str] = {}
    for line in lines:
        name, _, value = line.partition("=")
        documented[name] = value

    assert env_var in documented, (
        f"api/.env.example no longer has an assignment line for {env_var}, "
        "but ENV_EXAMPLE_DEFAULT_PINS above expects one."
    )
    expected = ENV_EXAMPLE_DEFAULT_PINS[env_var]
    assert documented[env_var] == expected, (
        f"api/.env.example's {env_var}={documented[env_var]!r} no longer "
        f"matches config.py's own default ({expected!r}) -- one of the two "
        "changed without the other. This is the exact asymmetry that let "
        "WP SWEEP-1's default flip go undetected here the first time "
        "(review round 1, blocker B1)."
    )


# ==========================================================================
# WP 3.12: VAULT_AUTO_GC
# ==========================================================================


def test_auto_gc_defaults_to_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation pin (WP SWEEP-1, ADR-0014): flip ``DEFAULT_AUTO_GC`` back to
    ``AUTO_GC_OFF`` and this test dies. Supersedes this test's own former
    name and assertion (``test_auto_gc_defaults_to_off`` -- "a feature that
    can delete files does not switch itself on"), which was true through
    WP 3.12 and is not the shipped default any more: the operator decided
    ``execute`` ships by default, PAIRED with
    ``DEFAULT_SWEEP_INCLUDE_CACHED`` also defaulting on (see
    ``test_sweep_include_cached_defaults_to_true`` below and
    ``docs/adr/0014-sweep-cached-and-auto-gc-default-on.md``) so that mode's
    superseded chunks are actually reclaimed rather than left to accumulate
    forever. An operator who wants the pre-ADR-0014 "GC only runs when I ask
    for it" behaviour back sets ``VAULT_AUTO_GC=off`` explicitly (env or
    ``PATCH /v1/settings``) -- that path is unchanged and still fully
    supported, see ``test_auto_gc_accepts_the_three_modes`` below.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_AUTO_GC", raising=False)

    settings = Settings.from_env()

    assert settings.auto_gc == "execute"
    assert settings.auto_gc_enabled is True
    assert settings.auto_gc_executes is True


@pytest.mark.parametrize(
    ("value", "enabled", "executes"),
    [
        ("off", False, False),
        ("dry-run", True, False),
        ("execute", True, True),
        ("EXECUTE", True, True),
        ("  Dry-Run  ", True, False),
    ],
)
def test_auto_gc_accepts_the_three_modes(
    monkeypatch: pytest.MonkeyPatch, value: str, enabled: bool, executes: bool
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_AUTO_GC", value)

    settings = Settings.from_env()

    assert settings.auto_gc_enabled is enabled
    assert settings.auto_gc_executes is executes


@pytest.mark.parametrize("bad", ["exectue", "on", "true", "1", "dry run", "delete"])
def test_a_bad_auto_gc_value_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A typo must not silently mean "off": an operator who set this believes
    automatic collection is running."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_AUTO_GC", bad)

    with pytest.raises(RuntimeError, match="VAULT_AUTO_GC must be one of"):
        Settings.from_env()


# ==========================================================================
# WP 3.12 review carry-over: the SAME strictness for the float settings
# ==========================================================================

#: Every ``_env_float``-backed setting. Same parameterize-over-the-list device
#: as INTEGER_SETTINGS above, and for the same reason.
FLOAT_SETTINGS = [
    ("VAULT_WORKER_POLL_SECONDS", "worker_poll_seconds"),
    ("VAULT_SIZE_CACHE_TTL", "size_cache_ttl_seconds"),
]

#: The accepted grammar: ASCII digits, optionally one '.' with digits on both
#: sides. Nothing else.
GOOD_FLOATS = ["60", "1.0", "0.25", "3.5", "0.5", "120"]

#: Everything ``float()`` would have swallowed. ``"nan"`` is the reason this
#: exists: ``nan <= 0`` is False, so the old range check passed it through.
SLOPPY_FLOATS = [
    " 1.5 ", "+1.5", "-1.5", "1_0", "٧", "nan", "NaN", "inf", "-inf",
    "Infinity", "abc", "1e3", "1E3", ".5", "5.", "1.2.3", "1,5", "0x1",
]


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
@pytest.mark.parametrize("good", GOOD_FLOATS)
def test_a_plain_decimal_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """Fractions are the whole point of a float setting — '3.5' must work."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, good)

    assert getattr(Settings.from_env(), attribute) == float(good)


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
@pytest.mark.parametrize("sloppy", SLOPPY_FLOATS)
def test_sloppy_floats_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, sloppy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, sloppy)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_nan_can_no_longer_slip_past_the_positive_check(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The specific hole this carry-over closes, named on its own.

    ``float("nan") <= 0`` is ``False``, so the pre-hardening guard accepted it.
    Downstream that is not harmless: a nan ``VAULT_SIZE_CACHE_TTL`` makes
    ``SizeCache``'s ``(now - computed_at) < ttl`` always false, so every request
    re-walks the whole depot tree; a nan ``VAULT_WORKER_POLL_SECONDS`` is fed
    straight to ``threading.Event.wait``.
    """
    import math

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, "nan")

    with pytest.raises(RuntimeError, match="not 'nan' or 'inf'"):
        Settings.from_env()

    # ...and the property that made it dangerous, stated so the test explains
    # itself: the old `value <= 0` guard genuinely does not catch this.
    assert (float("nan") <= 0) is False
    assert not math.isfinite(float("nan"))


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_a_digit_string_that_overflows_to_inf_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The one way ``inf`` can still get past the literal grammar: 400 digits
    is a valid decimal literal that ``float()`` rounds to infinity."""
    import math

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    huge = "9" * 400
    assert math.isinf(float(huge))  # the premise, not an assumption
    monkeypatch.setenv(name, huge)

    with pytest.raises(RuntimeError, match="too large"):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_a_blank_float_setting_still_means_unset(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    default = getattr(
        Settings(vault_api_key="k", db_path="x", cache_root="y", log_level="INFO"),
        attribute,
    )

    for blank in ("", "   ", "\t"):
        monkeypatch.setenv(name, blank)
        assert getattr(Settings.from_env(), attribute) == default


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_zero_is_still_rejected_by_the_range_check(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The pre-existing rule survives the new grammar: '0' and '0.0' are
    syntactically fine and still refused, because VAULT_SIZE_CACHE_TTL=0 would
    mean a full depot-tree walk on every request (there is deliberately no
    "disable the cache" setting)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for zero in ("0", "0.0", "0.000"):
        monkeypatch.setenv(name, zero)
        with pytest.raises(RuntimeError, match="must be > 0"):
            Settings.from_env()


# ---------------------------------------------------------------------------
# WP 3.11 (ADR-0008): the cache-event sweep settings
# ---------------------------------------------------------------------------


def _sweep_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Base environment: API key set, every sweep variable unset."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_EVENT_LOG_PATH",
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
        "VAULT_EVENT_LOG_MAX_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_event_sweep_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole feature hangs off one path, and it is empty by default."""
    _sweep_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.event_log_path == ""
    assert settings.event_sweep_enabled is False
    assert settings.miss_trigger_enabled is False
    # The other values are still populated so an operator can see what WOULD
    # happen before switching it on.
    assert settings.event_sweep_interval_minutes == 5
    assert settings.miss_trigger_cooldown_minutes == 60
    assert settings.miss_trigger_max_per_sweep == 5
    assert settings.bypass_window_days == 3
    assert settings.client_stats_keep == 48
    assert settings.event_log_max_bytes == 64 * 1024 * 1024


def test_a_blank_event_log_path_disables_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "   ")

    assert Settings.from_env().event_sweep_enabled is False


def test_the_miss_trigger_is_on_by_default_once_the_sweep_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PINNED DECISION: pointing at the log IS the opt-in (ADR-0001 hybrid)."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "/vault/logs/event.log")

    settings = Settings.from_env()

    assert settings.event_sweep_enabled is True
    assert settings.miss_trigger_enabled is True


def test_a_zero_cooldown_is_the_triggers_off_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 means OFF, deliberately -- never "no cooldown"."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "/vault/logs/event.log")
    monkeypatch.setenv("VAULT_MISS_TRIGGER_COOLDOWN_MINUTES", "0")

    settings = Settings.from_env()

    assert settings.miss_trigger_cooldown_minutes == 0
    assert settings.event_sweep_enabled is True, "statistics keep running"
    assert settings.miss_trigger_enabled is False


def test_event_log_max_bytes_zero_disables_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_MAX_BYTES", "0")

    assert Settings.from_env().event_log_max_bytes == 0


@pytest.mark.parametrize(
    "name",
    [
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
    ],
)
def test_the_sweep_settings_that_must_be_positive_reject_zero(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Two of the seven accept 0 (as an off switch); these four must not."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv(name, "0")

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(
    "name",
    [
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
        "VAULT_EVENT_LOG_MAX_BYTES",
    ],
)
@pytest.mark.parametrize("value", [" 5 ", "+5", "-5", "1_0", "٥", "1.5"])
def test_sloppy_sweep_numbers_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """The same house rule as every other numeric setting (WP 3.12)."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


# ---------------------------------------------------------------------------
# WP 3.13: generic webhook notifications
# ---------------------------------------------------------------------------


def _webhook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_WEBHOOK_URL",
        "VAULT_WEBHOOK_EVENTS",
        "VAULT_WEBHOOK_TIMEOUT_SECONDS",
        "VAULT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_webhooks_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from vault_api.config import WEBHOOK_EVENTS_ALL

    _webhook_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.webhook_url == ""
    assert settings.webhook_enabled is False
    # Still populated with the "everything" default, so turning the URL on
    # alone (no VAULT_WEBHOOK_EVENTS) sends all four events.
    assert settings.webhook_events == frozenset(WEBHOOK_EVENTS_ALL)
    assert settings.webhook_timeout_seconds == 5.0
    assert settings.vault_name == ""


def test_webhook_url_alone_enables_the_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_URL", "https://example.invalid/hook")

    settings = Settings.from_env()

    assert settings.webhook_enabled is True


def test_webhook_events_accepts_a_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done, job.error")

    settings = Settings.from_env()

    assert settings.webhook_events == {"job.done", "job.error"}


def test_webhook_events_rejects_an_unknown_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done,job.finished")

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_EVENTS"):
        Settings.from_env()


def test_webhook_events_rejects_an_empty_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray comma ('job.done,,job.error') must not silently become two
    events — it is refused loudly, the same house rule as every other
    list/enum setting in this module."""
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done,,job.error")

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_EVENTS"):
        Settings.from_env()


def test_webhook_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", "2.5")

    assert Settings.from_env().webhook_timeout_seconds == 2.5


def test_webhook_timeout_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", "0")

    with pytest.raises(RuntimeError, match="must be > 0"):
        Settings.from_env()


@pytest.mark.parametrize("sloppy", [" 5 ", "+5", "-5", "1_0", "٥", "nan", "inf"])
def test_webhook_timeout_rejects_sloppy_values(
    monkeypatch: pytest.MonkeyPatch, sloppy: str
) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", sloppy)

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_vault_name_defaults_to_empty_and_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_NAME", "  homelab  ")

    assert Settings.from_env().vault_name == "homelab"


@pytest.mark.parametrize(
    "bad",
    ["x" * (config.MAX_VAULT_NAME_LENGTH + 1), "home\tlab", "home\x1blab"],
)
def test_vault_name_is_validated_at_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """S2: VAULT_NAME gets the same bounded/printable rule as PATCH."""
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_NAME", bad)

    with pytest.raises(RuntimeError, match="VAULT_NAME"):
        Settings.from_env()


def test_vault_name_accepts_the_maximum_length_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_NAME", "h" * config.MAX_VAULT_NAME_LENGTH)

    assert Settings.from_env().vault_name == "h" * config.MAX_VAULT_NAME_LENGTH


# ==========================================================================
# Settings-API work package (ADR-0009): VAULT_SETTINGS_READONLY
# ==========================================================================


def test_settings_readonly_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    assert Settings.from_env().settings_readonly is False


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_settings_readonly_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", truthy)

    assert Settings.from_env().settings_readonly is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off", ""])
def test_settings_readonly_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", falsy)

    assert Settings.from_env().settings_readonly is False


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_settings_readonly_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", bad)

    with pytest.raises(RuntimeError, match="VAULT_SETTINGS_READONLY"):
        Settings.from_env()


# ==========================================================================
# WP 4d (plan §7 Phase 4d): VAULT_SWEEP_INCLUDE_CACHED
# ==========================================================================


def test_sweep_include_cached_defaults_to_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation pin (WP SWEEP-1, ADR-0014): flip ``DEFAULT_SWEEP_INCLUDE_CACHED``
    back to ``False`` and this test dies. Supersedes this test's own former
    name and assertion (``test_sweep_include_cached_defaults_to_off``): the
    "must be an explicit opt-in" reasoning (plan §7 Phase 4d) was correct
    for a sweep mode shipped without a paired collector, which is exactly
    what changed -- the operator decided this ships on by default, PAIRED
    with ``DEFAULT_AUTO_GC`` also defaulting to ``execute`` (see
    ``test_auto_gc_defaults_to_execute`` above and
    ``docs/adr/0014-sweep-cached-and-auto-gc-default-on.md``) so the extra
    bandwidth/disk this mode spends has its superseded chunks actually
    reclaimed rather than left to grow forever. An operator who wants the
    pre-ADR-0014 behaviour back sets ``VAULT_SWEEP_INCLUDE_CACHED=false``
    explicitly (env or ``PATCH /v1/settings``)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_SWEEP_INCLUDE_CACHED", raising=False)

    assert Settings.from_env().sweep_include_cached is True


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_sweep_include_cached_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", truthy)

    assert Settings.from_env().sweep_include_cached is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off"])
def test_sweep_include_cached_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    """``""`` was DROPPED from this parametrize list (WP SWEEP-1, ADR-0014):
    a blank value is not a "false spelling" at all, it is ``_env_bool``'s
    "unset, use the default" sentinel (``config.py``'s own docstring on that
    function). Through WP 4d this test passed for ``""`` for the WRONG
    reason -- the old default happened to BE ``False``, so "blank" and
    "false" were indistinguishable by outcome. Now that the default is
    ``True``, that coincidence is gone; see
    ``test_sweep_include_cached_blank_env_value_resolves_to_the_true_default``
    below for blank's own, now-distinct, explicit pin."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", falsy)

    assert Settings.from_env().sweep_include_cached is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_sweep_include_cached_blank_env_value_resolves_to_the_true_default(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """A PRESENT-but-blank value (e.g. a compose ``${VAULT_SWEEP_INCLUDE_
    CACHED}`` passthrough with nothing set in ``.env``) hits the exact same
    "blank means default" branch in ``_env_bool`` as the variable being
    entirely absent (``test_sweep_include_cached_defaults_to_true`` above).
    Before ADR-0014 this was unobservable as its own case because it was
    folded into ``test_sweep_include_cached_accepts_false_spellings``'s
    parametrize list and produced the same answer either way; now that the
    default is ``True`` the two paths (blank vs. an explicit false spelling)
    produce DIFFERENT answers, so this needs its own pin."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", blank)

    assert Settings.from_env().sweep_include_cached is True


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_sweep_include_cached_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", bad)

    with pytest.raises(RuntimeError, match="VAULT_SWEEP_INCLUDE_CACHED"):
        Settings.from_env()


def test_env_bool_error_names_the_original_unstripped_value_and_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N4 (reviewer nitpick, 2026-08-18 review round): the refactored
    ``_env_bool`` had started reporting the STRIPPED value in its error
    (losing the surrounding whitespace that made the typo visible) and had
    dropped the "or leave it blank for the default" hint every earlier
    version had. Both restored -- pinned here via
    ``VAULT_SETTINGS_READONLY`` (default ``False``), the field this helper
    backs, exactly as the reviewer measured it.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", " bogus ")

    with pytest.raises(RuntimeError) as excinfo:
        Settings.from_env()

    message = str(excinfo.value)
    assert "' bogus '" in message  # the ORIGINAL value, whitespace and all
    assert "blank" in message.lower()
    assert "False" in message  # the default it falls back to


# ==========================================================================
# WP 4h.0 (ADR-0010): VAULT_RELAY_EXPOSE_PLAYTIME / _LAST_PLAYED
#
# Env-only privacy gate for the Steam relay's playtime_forever /
# rtime_last_played fields (vault_api/routers/steam.py) -- see
# tests/test_relay_privacy.py for the router-level pins (field genuinely
# absent from the response, independence between the two keys, PATCH
# rejection). These are the config.py-layer pins one level below: the
# grammar and the DEFAULT_* constants each key falls back to.
# ==========================================================================


def test_relay_expose_playtime_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation pin: flip ``DEFAULT_RELAY_EXPOSE_PLAYTIME`` to ``True`` and
    this test dies -- see that constant's own docstring in config.py for the
    privacy argument (docs/PROJECT_PLAN.md Phase 4h's stance, echoing
    DEFAULT_SWEEP_INCLUDE_CACHED's house style)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_RELAY_EXPOSE_PLAYTIME", raising=False)

    assert Settings.from_env().relay_expose_playtime is False


def test_relay_expose_last_played_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same pin as above for the sibling key -- doubly justified per WP 4h.1's
    own note ("the sharper fact of the two")."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", raising=False)

    assert Settings.from_env().relay_expose_last_played is False


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_relay_expose_playtime_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_PLAYTIME", truthy)

    assert Settings.from_env().relay_expose_playtime is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off", ""])
def test_relay_expose_playtime_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_PLAYTIME", falsy)

    assert Settings.from_env().relay_expose_playtime is False


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_relay_expose_playtime_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_PLAYTIME", bad)

    with pytest.raises(RuntimeError, match="VAULT_RELAY_EXPOSE_PLAYTIME"):
        Settings.from_env()


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_relay_expose_last_played_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", truthy)

    assert Settings.from_env().relay_expose_last_played is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off", ""])
def test_relay_expose_last_played_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", falsy)

    assert Settings.from_env().relay_expose_last_played is False


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_relay_expose_last_played_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", bad)

    with pytest.raises(RuntimeError, match="VAULT_RELAY_EXPOSE_LAST_PLAYED"):
        Settings.from_env()


def test_relay_expose_playtime_and_last_played_read_independent_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wiring swap (e.g. ``relay_expose_last_played`` accidentally reading
    ``VAULT_RELAY_EXPOSE_PLAYTIME``, or both fields reading the SAME env var)
    would still pass every test above in isolation -- this moves ONLY the
    playtime var and checks BOTH fields in the same assertion, which a test
    that only ever set one var and checked its own field would not catch.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_PLAYTIME", "true")
    monkeypatch.delenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", raising=False)

    settings = Settings.from_env()
    assert settings.relay_expose_playtime is True
    assert settings.relay_expose_last_played is False


# ==========================================================================
# Settings-API work package: validate_webhook_url (used only by
# PATCH /v1/settings, NOT by Settings.from_env — see the function's own
# docstring for why no startup grammar exists for this field to reuse).
# ==========================================================================


def test_validate_webhook_url_accepts_blank_as_disabled() -> None:
    from vault_api.config import validate_webhook_url

    assert validate_webhook_url("") == ""
    assert validate_webhook_url("   ") == ""


@pytest.mark.parametrize(
    "good",
    [
        "http://example.invalid/hook",
        "https://example.invalid/hook",
        "https://user:pass@example.invalid:8443/hook?x=1",
    ],
)
def test_validate_webhook_url_accepts_http_and_https(good: str) -> None:
    from vault_api.config import validate_webhook_url

    assert validate_webhook_url(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "not a url",
        "ftp://example.invalid/hook",
        "example.invalid/hook",
        "file:///etc/passwd",
    ],
)
def test_validate_webhook_url_rejects_non_http_schemes(bad: str) -> None:
    from vault_api.config import validate_webhook_url

    with pytest.raises(ValueError):
        validate_webhook_url(bad)


# -- WP S-1 (ADR-0012): VAULT_PREFILL_MODE and the runner tuning knobs -------


def test_prefill_mode_defaults_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Byte-preservation: an install that never sets this stays on the
    behaviour that existed before this work package."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_PREFILL_MODE", raising=False)

    settings = Settings.from_env()

    assert settings.prefill_mode == "subprocess"
    assert settings.prefill_mode_queue is False


@pytest.mark.parametrize(
    ("value", "queue"),
    [("subprocess", False), ("queue", True), ("QUEUE", True), ("  Queue  ", True)],
)
def test_prefill_mode_accepts_the_two_modes(
    monkeypatch: pytest.MonkeyPatch, value: str, queue: bool
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_PREFILL_MODE", value)

    settings = Settings.from_env()

    assert settings.prefill_mode_queue is queue


@pytest.mark.parametrize("bad", ["subproccess", "async", "true", "1", "sidecar"])
def test_a_bad_prefill_mode_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A typo here is a security-relevant misunderstanding (an operator
    believing an egress lock is in effect while vault-api quietly still runs
    SteamPrefill itself), not a cosmetic one — see docs/adr/0012-*.md."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_PREFILL_MODE", bad)

    with pytest.raises(RuntimeError, match="VAULT_PREFILL_MODE"):
        Settings.from_env()


def test_runner_tuning_knobs_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_RUNNER_HEARTBEAT_SECONDS",
        "VAULT_RUNNER_LEASE_TIMEOUT_SECONDS",
        "VAULT_RUNNER_POLL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.runner_heartbeat_seconds == 5.0
    assert settings.runner_lease_timeout_seconds == 30.0
    assert settings.runner_poll_seconds == 1.0


def test_runner_tuning_knobs_read_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RUNNER_HEARTBEAT_SECONDS", "1.5")
    monkeypatch.setenv("VAULT_RUNNER_LEASE_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("VAULT_RUNNER_POLL_SECONDS", "0.1")

    settings = Settings.from_env()

    assert settings.runner_heartbeat_seconds == 1.5
    assert settings.runner_lease_timeout_seconds == 10.0
    assert settings.runner_poll_seconds == 0.1


def test_bad_runner_tuning_knobs_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_RUNNER_LEASE_TIMEOUT_SECONDS", "nan")

    with pytest.raises(RuntimeError, match="VAULT_RUNNER_LEASE_TIMEOUT_SECONDS"):
        Settings.from_env()


# --------------------------------------------------------------------------
# WP API-FIX-2
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blank_value", ["", "   ", "\t"])
def test_from_env_raises_when_db_path_is_blank(
    monkeypatch: pytest.MonkeyPatch, blank_value: str
) -> None:
    """P1: ``sqlite3.connect("")`` is a private per-connection temp database,
    so a present-but-blank VAULT_DB_PATH would boot a vault-api that persists
    nothing -- refuse it like VAULT_CACHE_ROOT."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_DB_PATH", blank_value)

    with pytest.raises(RuntimeError, match="VAULT_DB_PATH must not be blank"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("var", "cap"),
    [
        ("VAULT_SCHEDULE_INTERVAL_MINUTES", config.MAX_INTERVAL_MINUTES),
        ("VAULT_SCHEDULE_CLIENT_STALE_DAYS", config.MAX_DAYS),
        ("VAULT_EVENT_SWEEP_INTERVAL_MINUTES", config.MAX_INTERVAL_MINUTES),
        ("VAULT_MISS_TRIGGER_COOLDOWN_MINUTES", config.MAX_COOLDOWN_MINUTES),
        ("VAULT_BYPASS_WINDOW_DAYS", config.MAX_DAYS),
        ("VAULT_GC_GRACE_DAYS", config.MAX_DAYS),
    ],
)
def test_from_env_refuses_a_timedelta_fed_int_over_its_cap(
    monkeypatch: pytest.MonkeyPatch, var: str, cap: int
) -> None:
    """S2: a digits-only grammar with no ceiling let ``timedelta`` overflow
    inside request handlers and the scheduler tick."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(var, str(cap + 1))

    with pytest.raises(RuntimeError, match=rf"{var} must be <= {cap}, got {cap + 1}"):
        Settings.from_env()

    # The cap itself is accepted -- a ceiling, not an off-by-one.
    monkeypatch.setenv(var, str(cap))
    Settings.from_env()


def test_parse_strict_int_maximum() -> None:
    assert config.parse_strict_int("10", maximum=10) == 10
    with pytest.raises(ValueError, match="must be <= 10, got 11"):
        config.parse_strict_int("11", maximum=10)
    # No maximum = the historical behaviour, unbounded.
    assert config.parse_strict_int("9" * 300) == int("9" * 300)
    with pytest.raises(ValueError, match="maximum below the minimum"):
        config.parse_strict_int("5", minimum=6, maximum=5)


def test_timedelta_caps_are_finite_for_the_stdlib() -> None:
    """The caps exist to keep every consumer's ``timedelta(...)`` finite."""
    from datetime import timedelta

    timedelta(minutes=config.MAX_INTERVAL_MINUTES)
    timedelta(minutes=config.MAX_COOLDOWN_MINUTES)
    timedelta(days=config.MAX_DAYS)


def test_validate_webhook_url_does_not_echo_the_raw_value() -> None:
    """P2: the 422 detail for a bad webhook URL must not carry userinfo."""
    with pytest.raises(ValueError) as excinfo:
        config.validate_webhook_url("ftp://user:s3cr3t@host/path")
    assert "s3cr3t" not in str(excinfo.value)
    assert "http or https" in str(excinfo.value)


def test_validate_webhook_url_does_not_echo_the_parsed_scheme() -> None:
    """N1: for scheme-less ``admin:s3cr3t@host`` urlsplit's "scheme" is the
    username, so the parsed scheme must not be echoed either."""
    with pytest.raises(ValueError) as excinfo:
        config.validate_webhook_url("admin:s3cr3t@host/x")
    message = str(excinfo.value)
    assert "admin" not in message
    assert "s3cr3t" not in message
    assert "http or https" in message
