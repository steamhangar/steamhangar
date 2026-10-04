"""WP AGENT-FIX-2: static pins for the Windows packaging fixes.

The behaviour itself is tested by
``agent/packaging/windows/tests/test-packaging-unit.ps1``, which CI executes
under Windows PowerShell 5.1 (``powershell-syntax`` job). These pins run
everywhere (no PowerShell needed) and guard the parts a refactor could drop
silently:

1. run-vault-agent.ps1 no longer redirects the native agent with ``*>>``
   (UTF-16 + NativeCommandError) and uses Start-Process with both streams
   redirected to files.
2. install-task.ps1 defines the helpers the unit test lifts out by name,
   resolves the library root before the env file is written, and prints
   only where the API key came from.
3. CI actually executes the unit test, under Windows PowerShell 5.1.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS = REPO_ROOT / "agent" / "packaging" / "windows"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def ps_code(path: Path) -> str:
    """PowerShell source without ``#`` line comments and ``<# #>`` blocks."""
    text = path.read_text(encoding="ascii")
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_wrapper_does_not_stream_redirect_the_agent() -> None:
    code = ps_code(WINDOWS / "run-vault-agent.ps1")
    assert "*>>" not in code
    assert "2>&1" not in code
    assert re.search(
        r"Start-Process -FilePath \$AgentPath -ArgumentList \"report\"[^\n]*-Wait -PassThru `\s*"
        r"-RedirectStandardOutput \$stdoutFile -RedirectStandardError \$stderrFile",
        code,
    )
    assert "$exitCode = $proc.ExitCode" in code
    # Bytes are copied, not decoded and re-encoded.
    assert "[System.IO.File]::ReadAllBytes($SourcePath)" in code


def test_install_defines_the_helpers_the_unit_test_lifts() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    for name in ("Get-EnvFileValue", "Get-RegistrySteamPath", "ConvertTo-SteamLibraryRoot", "Resolve-LibraryRoot"):
        assert len(re.findall(rf"^function {re.escape(name)} \{{", code, re.M)) == 1, name
    assert '"HKCU:\\Software\\Valve\\Steam" -Name "SteamPath"' in code


def test_install_resolves_library_root_before_writing_env_and_before_stop() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    resolve = code.index("$libraryRootChoice = Resolve-LibraryRoot")
    # Validation phase: before the Stop preference (docs/LEARNINGS.md).
    assert resolve < code.index('$ErrorActionPreference = "Stop"')
    write = code.index('$envLines.Add("VAULT_AGENT_LIBRARY_ROOT=$($libraryRootChoice.Root)")')
    assert resolve < write
    assert "if ($libraryRootChoice.WriteToEnv)" in code


def test_install_never_prints_the_api_key() -> None:
    code = ps_code(WINDOWS / "install-task.ps1")
    assert '$apiKeySource = "kept from existing env.txt"' in code
    assert 'Write-Host "  API key         : $apiKeySource"' in code
    for line in code.splitlines():
        if "Write-Host" in line or "Write-Warning" in line or "Write-Output" in line:
            assert "$resolvedApiKey" not in line and "$existingApiKey" not in line, line
    # ACL-before-content stays: icacls runs before the content is written.
    assert code.index("icacls $envFilePath /inheritance:r") < code.index(
        "Set-Content -LiteralPath $envFilePath -Value $envLines -Encoding utf8"
    )


def test_ci_executes_the_packaging_unit_test_under_windows_powershell() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (WINDOWS / "tests" / "test-packaging-unit.ps1").is_file()
    assert re.search(
        r"shell: powershell\n\s+run: \.\\agent\\packaging\\windows\\tests\\test-packaging-unit\.ps1",
        ci,
    )
