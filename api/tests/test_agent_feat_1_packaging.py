"""WP AGENT-FEAT-1: the shipped schedulers and the agent agree on 10 minutes.

Static file checks (no Docker, no PowerShell, no systemd needed). The
PowerShell scripts' syntax is checked by CI's 5.1 parse job; the Windows
task's runtime shape by the real-machine harness
``agent/packaging/windows/tests/test-install-uninstall.ps1``.

Pinned here:

1. systemd: the timer fires every 10 minutes on the clock AND once at user
   manager start (OnStartupSec=), keeps Persistent=true on its OnCalendar=
   trigger, and the service passes ``--interval 10m``.
2. Windows: install-task.ps1 defaults to 10 minutes, registers a logon trigger
   for the installing user next to the repetition trigger (repetition first),
   keeps -StartWhenAvailable and -MultipleInstances IgnoreNew, and writes the
   interval into the env file as VAULT_AGENT_REPORT_INTERVAL.
3. The Go agent mirrors the server: the version regex is the VER-1 grammar
   anchored, the interval bounds are the server's, the default is 10m.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from vault_api import _VERSION_GRAMMAR, agent_reports

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = REPO_ROOT / "agent" / "packaging" / "systemd"
WINDOWS = REPO_ROOT / "agent" / "packaging" / "windows"
GO = REPO_ROOT / "agent" / "go"


def directives(path: Path) -> list[tuple[str, str]]:
    """``(key, value)`` for every non-comment ``Key=Value`` line."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def ps_code(path: Path) -> str:
    """PowerShell source with ``#`` line comments and ``<# #>`` blocks removed,
    so a pin cannot be satisfied by documentation alone."""
    text = path.read_text(encoding="ascii")
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# --- 1. systemd -------------------------------------------------------------------


def test_timer_runs_every_ten_minutes_and_at_user_manager_start() -> None:
    timer = directives(SYSTEMD / "vault-agent-report.timer")
    assert ("OnCalendar", "*:0/10") in timer
    assert [k for k, _ in timer].count("OnCalendar") == 1
    assert ("OnStartupSec", "30s") in timer
    # OnBootSec= counts from machine boot, which has long passed when a user
    # logs in later; OnUnitActiveSec= would be a second cadence.
    assert not any(k in ("OnBootSec", "OnUnitActiveSec", "OnActiveSec") for k, _ in timer)


def test_timer_keeps_persistent_catch_up_on_its_calendar_trigger() -> None:
    timer = directives(SYSTEMD / "vault-agent-report.timer")
    assert ("Persistent", "true") in timer
    text = (SYSTEMD / "vault-agent-report.timer").read_text(encoding="utf-8")
    # The WP 2.5 reasoning (Persistent= only works with OnCalendar=) stays.
    assert 'only has an effect on timers configured with OnCalendar=' in text


def test_service_passes_the_timer_interval_to_the_agent() -> None:
    service = dict(directives(SYSTEMD / "vault-agent-report.service"))
    assert service["ExecStart"] == "%h/.local/bin/vault-agent report --interval 10m"


# --- 2. Windows -------------------------------------------------------------------


def test_install_task_defaults_to_ten_minutes() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    assert re.search(r"\[int\]\$IntervalMinutes = 10,", code)
    assert "$IntervalMinutes -lt 1 -or $IntervalMinutes -gt 1440" in code


def test_install_task_registers_repetition_then_logon_trigger() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    assert re.search(
        r"\$trigger = New-ScheduledTaskTrigger -Once -At \$startTime `\s*"
        r"-RepetitionInterval \(New-TimeSpan -Minutes \$IntervalMinutes\)",
        code,
    )
    # The logon trigger is for the installing user: the same $userId the
    # Interactive principal runs as.
    assert "$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId" in code
    assert re.search(
        r"New-ScheduledTaskPrincipal -UserId \$userId -LogonType Interactive", code
    )
    assert "-Trigger @($trigger, $logonTrigger)" in code
    # $userId must exist before the logon trigger uses it.
    assert code.index("$userId = ") < code.index("-AtLogOn -User $userId")


def test_install_task_keeps_catch_up_and_no_overlap() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    assert re.search(r"New-ScheduledTaskSettingsSet `[^)]*-StartWhenAvailable", code, re.S)
    assert "-MultipleInstances IgnoreNew" in code


def test_install_task_passes_the_interval_to_the_agent() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    assert '$envLines.Add("VAULT_AGENT_REPORT_INTERVAL=${IntervalMinutes}m")' in code


def test_packaging_scripts_stay_pure_ascii() -> None:
    """docs/LEARNINGS.md (WP 2.6): non-ASCII breaks the PS 5.1 parser."""
    for path in sorted(WINDOWS.rglob("*.ps1")):
        path.read_bytes().decode("ascii")


# --- 3. Go mirrors the server ------------------------------------------------------


# Text scrapes of Go source. Every failure message says which of two edits
# applies (docs/LEARNINGS.md, text-scrape drift guards): GRAMMAR drift = the
# Go line no longer has the shape this regex reads -> widen the regex here;
# VALUE drift = the line was read but its value differs from the server's ->
# fix the Go constant (or the server, if the server is what changed).

GRAMMAR = "GRAMMAR drift: {where} no longer matches this test's extractor {rx!r}; widen the regex here if the Go code was merely reformatted"
VALUE = "VALUE drift: {where} is {got!r}, expected {want!r} (the server value, or the 10-minute user decision); fix the Go constant, this test's regex is fine"


def _scrape(path: Path, rx: str, where: str, flags: int = 0) -> str:
    match = re.search(rx, path.read_text(encoding="utf-8"), flags)
    assert match, GRAMMAR.format(where=where, rx=rx)
    return match.group(1)


def test_go_version_regex_is_the_ver1_grammar() -> None:
    where = "report.go agentVersionGrammar"
    got = _scrape(GO / "report" / "report.go", r"agentVersionGrammar = regexp\.MustCompile\(`([^`]*)`\)", where)
    want = "^" + _VERSION_GRAMMAR.pattern + "$"
    assert got == want, VALUE.format(where=where, got=got, want=want)


def test_go_interval_bounds_are_the_servers() -> None:
    report_go = GO / "report" / "report.go"
    for name, server_value in (
        ("MinReportIntervalSeconds", agent_reports.MIN_REPORT_INTERVAL_SECONDS),
        ("MaxReportIntervalSeconds", agent_reports.MAX_REPORT_INTERVAL_SECONDS),
    ):
        where = f"report.go {name}"
        expr = _scrape(report_go, rf"^\s*{name}\s*=\s*([0-9][0-9 *]*?)\s*$", where, re.M)
        got = math.prod(int(factor) for factor in expr.split("*"))
        assert got == server_value, VALUE.format(where=where, got=f"{expr} = {got}", want=server_value)
    # The server side itself, as literals (user decision, not derived).
    assert agent_reports.MIN_REPORT_INTERVAL_SECONDS == 60
    assert agent_reports.MAX_REPORT_INTERVAL_SECONDS == 24 * 60 * 60


def test_go_default_interval_is_ten_minutes() -> None:
    where = "config.go DefaultReportInterval"
    expr = _scrape(
        GO / "agentconfig" / "config.go",
        r"^const DefaultReportInterval = ([0-9]+) \* time\.Minute$",
        where,
        re.M,
    )
    assert expr == "10", VALUE.format(where=where, got=f"{expr} * time.Minute", want="10 * time.Minute")


def test_go_presence_field_names_match_the_request_model() -> None:
    from vault_api.routers.agent import InstalledReportRequest

    report_go = (GO / "report" / "report.go").read_text(encoding="utf-8")
    client_go = (GO / "client" / "client.go").read_text(encoding="utf-8")
    for name in ("agent_version", "report_interval_seconds"):
        assert name in InstalledReportRequest.model_fields
        assert f'`json:"{name},omitempty"`' in report_go
        assert f'"{name}":' in client_go  # the legacy-resend allowlist
