"""D16b: the Python twins of the vault-core hook grammars, pinned by the ONE
shared table ``core/tests/fixtures/settings-grammar.tsv`` (D16 audit 3.4,
3.6; LEARNINGS "Twin config files need twin pins").

Three layers per row: the twin itself (``core_settings``), the PATCH grammar
the settings API builds on it (``settings_store.OVERRIDABLE_SPECS``), and for
``VAULT_SCHEDULE_WINDOW`` the startup path (``config._env_schedule_window``).
A failing row names which edit applies: value drift -> fix the table; grammar
drift -> change the hook AND its twin.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vault_api import config, core_settings, settings_store

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "core" / "tests" / "fixtures" / "settings-grammar.tsv"
HOOKS = REPO / "core" / "docker"


def _rows() -> list[tuple[str, str, str, str]]:
    rows = []
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, raw, verdict, normalised = line.split("\t")
        rows.append((key, "" if raw == "<blank>" else raw, verdict, normalised))
    return rows


ROWS = _rows()
CORE_ROWS = [row for row in ROWS if row[0] in core_settings.TWINS]
WINDOW_ROWS = [row for row in ROWS if row[0] == "VAULT_SCHEDULE_WINDOW"]


def _ids(rows):
    return [f"{key}={raw!r}" for key, raw, _, _ in rows]


def test_the_table_covers_every_twin_and_the_off_cases() -> None:
    keys = {row[0] for row in ROWS}
    assert set(core_settings.TWINS) | {"VAULT_SCHEDULE_WINDOW"} == keys
    for key in ("VAULT_UPSTREAM_RATE", "VAULT_UPSTREAM_RATE_WINDOW", "VAULT_UPSTREAM_EDGE",
                "VAULT_UPSTREAM_POOL_HOSTS", "VAULT_SCHEDULE_WINDOW"):
        raws = {raw: verdict for k, raw, verdict, _ in ROWS if k == key}
        assert raws["off"] == "ok", key
        assert raws["OFF"] == "refused", key
        assert raws["off,x"] == "refused", key
        assert raws[""] == "unset", key
        assert " off " in raws, key
    # Exactly the five Q7 keys accept off (audit 3.6).
    accepting = {k for k, raw, verdict, _ in ROWS if raw == "off" and verdict == "ok"}
    assert accepting == {"VAULT_UPSTREAM_RATE", "VAULT_UPSTREAM_RATE_WINDOW",
                         "VAULT_UPSTREAM_EDGE", "VAULT_UPSTREAM_POOL_HOSTS",
                         "VAULT_SCHEDULE_WINDOW"}


@pytest.mark.parametrize("key,raw,verdict,normalised", CORE_ROWS, ids=_ids(CORE_ROWS))
def test_twin_matches_the_table(key: str, raw: str, verdict: str, normalised: str) -> None:
    twin = core_settings.TWINS[key]
    if verdict == "refused":
        with pytest.raises(core_settings.CoreGrammarError):
            twin(raw)
        return
    result = twin(raw)
    if verdict == "unset":
        assert result is None
    else:
        assert result == normalised


@pytest.mark.parametrize("key,raw,verdict,normalised", CORE_ROWS, ids=_ids(CORE_ROWS))
def test_patch_grammar_matches_the_table(key: str, raw: str, verdict: str, normalised: str) -> None:
    """The settings API refuses what the hook refuses. "Not set" is not a
    PATCH value (clearing is null). The rate window has no PATCH key (Q3)."""
    spec = settings_store.CORE_SPECS_BY_ENV.get(key)
    if spec is None:
        assert key == "VAULT_UPSTREAM_RATE_WINDOW"
        return
    if verdict == "ok":
        stored = spec.parse(raw)
        # Stored canonically: the twin accepts it back with the same result.
        assert core_settings.TWINS[key](stored) == normalised
    else:
        with pytest.raises(settings_store.SettingValidationError):
            spec.parse(raw)


@pytest.mark.parametrize("key,raw,verdict,normalised", WINDOW_ROWS, ids=_ids(WINDOW_ROWS))
def test_schedule_window_startup_and_patch_match_the_table(
    monkeypatch: pytest.MonkeyPatch, key: str, raw: str, verdict: str, normalised: str
) -> None:
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", raw)
    spec = settings_store.OVERRIDABLE_SPECS["schedule_window"]
    if verdict == "refused":
        with pytest.raises(RuntimeError):
            config._env_schedule_window()
        with pytest.raises(settings_store.SettingValidationError):
            spec.parse(raw)
        return
    window = config._env_schedule_window()
    if verdict == "unset":
        # Q7: blank = not set = the shipped default window.
        assert window is not None and window.normalized == "03:00-07:00"
        return
    shown = "off" if window is None else window.normalized
    assert shown == normalised
    stored = spec.parse(raw)
    assert ("off" if stored is None else stored.normalized) == normalised


# --------------------------------------------------------------------------
# Literal pins: the twins' constants against the hook sources. A failure
# means a hook constant moved; update the twin AND the table.
# --------------------------------------------------------------------------

HOOK_CONSTANTS = [
    ("27-vault-upstream-rate.sh", "BUCKETS=1024", core_settings.RATE_MIN_TOTAL == 1024),
    ("28-vault-upstream-pool.sh", "CAP_DEFAULT=16", core_settings.UPSTREAM_CAP_DEFAULT == 16),
    ("28-vault-upstream-pool.sh", "CAP_MIN=1", core_settings.UPSTREAM_CAP_MIN == 1),
    ("28-vault-upstream-pool.sh", "CAP_MAX=64", core_settings.UPSTREAM_CAP_MAX == 64),
    ("28-vault-upstream-pool.sh", "KEEPALIVE_PER_GROUP=8", True),
    ("28-vault-upstream-pool.sh", "MAX_IDLE_TOTAL=32", core_settings.POOL_MAX_HOSTS == 32 // 8),
    ("28-vault-upstream-pool.sh", "FAMILY_1=steamcontent.com",
     core_settings.POOL_FAMILIES[0] == "steamcontent.com"),
    ("28-vault-upstream-pool.sh", "FAMILY_2=steamserver.net",
     core_settings.POOL_FAMILIES[1] == "steamserver.net"),
    ("28-vault-upstream-pool.sh", "MARKER=lancache.steamcontent.com",
     core_settings.POOL_MARKER == "lancache.steamcontent.com"),
    ("26-vault-tls-passthrough.sh", "TLS_CLIENT_DEFAULT=64", core_settings.TLS_CLIENT_DEFAULT == 64),
    ("26-vault-tls-passthrough.sh", "TLS_CLIENT_MAX=256", core_settings.TLS_CLIENT_MAX == 256),
    ("26-vault-tls-passthrough.sh", "TLS_TOTAL_DEFAULT=256", core_settings.TLS_TOTAL_DEFAULT == 256),
    ("26-vault-tls-passthrough.sh", "TLS_TOTAL_MAX=400", core_settings.TLS_TOTAL_MAX == 400),
    ("45-vault-eventlog-rotate.sh", "MIN_BYTES=1048576", core_settings.ROTATE_MIN_BYTES == 1048576),
]


@pytest.mark.parametrize("hook,line,twin_agrees", HOOK_CONSTANTS, ids=[c[1] for c in HOOK_CONSTANTS])
def test_twin_constants_match_the_hook_sources(hook: str, line: str, twin_agrees: bool) -> None:
    source = (HOOKS / hook).read_text(encoding="utf-8")
    assert re.search(rf"^{re.escape(line)}$", source, re.MULTILINE), (
        f"{hook} no longer has the line {line!r}: update core_settings.py and the table"
    )
    assert twin_agrees, f"core_settings.py disagrees with {hook}: {line}"


def test_resolver_allowlist_matches_40() -> None:
    source = (HOOKS / "40-vault-preflight.sh").read_text(encoding="utf-8")
    assert "*[!]0-9a-fA-F.:[\\ -]*)" in source
    assert set("0123456789abcdefABCDEF.:[] -") == core_settings._RESOLVER_CHARS


# --------------------------------------------------------------------------
# Cross-variable rules (audit 3.4) and the file
# --------------------------------------------------------------------------


def test_cap_floor_rule() -> None:
    core_settings.check_cap_floor({"VAULT_UPSTREAM_MAX_CONNS": "8"}, 8)
    core_settings.check_cap_floor({}, 16)  # unset cap = 16
    with pytest.raises(core_settings.CoreGrammarError, match="at least prefill_max_threads"):
        core_settings.check_cap_floor({"VAULT_UPSTREAM_MAX_CONNS": "7"}, 8)
    with pytest.raises(core_settings.CoreGrammarError):
        core_settings.check_cap_floor({}, 17)


def test_tls_order_rule() -> None:
    core_settings.check_tls_order({"VAULT_TLS_CLIENT_MAX_CONNS": "256"})  # total default 256
    with pytest.raises(core_settings.CoreGrammarError, match="must not be above"):
        core_settings.check_tls_order({"VAULT_TLS_MAX_CONNS": "32"})  # client default 64


def test_rate_window_follows_the_download_window() -> None:
    """Q3: the core rate window is the effective download window; off when
    the window is off (vault-core then caps around the clock)."""
    assert core_settings.core_file_values({}, "03:00-07:00", None) == {
        "VAULT_UPSTREAM_RATE_WINDOW": "03:00-07:00"
    }
    assert core_settings.core_file_values({}, None, None) == {
        "VAULT_UPSTREAM_RATE_WINDOW": "off"
    }


def test_file_values_keep_the_allowlist_order_and_revalidate() -> None:
    values = core_settings.core_file_values(
        {"VAULT_UPSTREAM_MAX_CONNS": "32", "VAULT_UPSTREAM_RATE": "800k"}, "22:00-06:00", 12
    )
    assert list(values) == [
        "VAULT_UPSTREAM_RATE",
        "VAULT_UPSTREAM_RATE_WINDOW",
        "VAULT_UPSTREAM_MAX_CONNS",
        "VAULT_PREFILL_MAX_THREADS",
    ]
    assert values["VAULT_PREFILL_MAX_THREADS"] == "12"
    with pytest.raises(core_settings.CoreGrammarError):
        core_settings.core_file_values({"VAULT_UPSTREAM_RATE": "0"}, None, None)


def test_writer_is_atomic_bumps_the_generation_and_skips_no_ops(tmp_path: Path) -> None:
    path = tmp_path / "core-settings.env"
    assert core_settings.write_core_settings_file(str(path), {"VAULT_UPSTREAM_RATE": "800k"}) == 1
    assert path.read_text(encoding="utf-8") == (
        "# generated by vault-api -- do not edit\nGENERATION=1\nVAULT_UPSTREAM_RATE=800k\n"
    )
    # Same content: no write, no new generation (no reload for nothing).
    assert core_settings.write_core_settings_file(str(path), {"VAULT_UPSTREAM_RATE": "800k"}) is None
    assert core_settings.write_core_settings_file(str(path), {"VAULT_UPSTREAM_RATE": "2m"}) == 2
    assert "GENERATION=2" in path.read_text(encoding="utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["core-settings.env"]


def test_writer_refuses_a_line_break_or_an_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(core_settings.CoreGrammarError):
        core_settings.render_file({"VAULT_UPSTREAM_RATE": "800k\nVAULT_RESOLVER=6.6.6.6"}, 1)
    with pytest.raises(core_settings.CoreGrammarError):
        core_settings.render_file({"VAULT_API_KEY": "x"}, 1)
