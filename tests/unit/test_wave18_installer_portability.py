"""Focused Windows PowerShell boundary regression tests for W18."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer" / "install.ps1"
INSTALLER_CMD = ROOT / "installer" / "install.cmd"
DISPOSABLE_ROOT = ROOT / ".tmp"
EVIDENCE_ROOT = DISPOSABLE_ROOT / "W23_N1_R1_evidence"


def _record_evidence(name: str, evidence: dict[str, object]) -> None:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_ROOT / f"{name}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def installer_tmp_root() -> Iterator[Path]:
    root = DISPOSABLE_ROOT / f"installer-r2-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        resolved_root = root.resolve()
        resolved_parent = DISPOSABLE_ROOT.resolve()
        if resolved_root.parent != resolved_parent:
            raise AssertionError(f"refusing cleanup outside .tmp: {resolved_root}")
        shutil.rmtree(resolved_root, ignore_errors=False)


def _windows_powershell() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not executable.is_file():
        pytest.skip("Windows PowerShell 5.1 executable is unavailable")
    return executable


def _prepare_contaminated_install(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    module_root = tmp_path / "contaminated-modules"
    utility_root = module_root / "Microsoft.PowerShell.Utility"
    utility_root.mkdir(parents=True)
    (utility_root / "Microsoft.PowerShell.Utility.psd1").write_text(
        "\n".join(
            (
                "@{",
                "    RootModule = 'Microsoft.PowerShell.Utility.psm1'",
                "    ModuleVersion = '7.0.0'",
                "    CompatiblePSEditions = @('Core')",
                "    PowerShellVersion = '7.0'",
                "    FunctionsToExport = @('Get-FileHash', 'ConvertFrom-Json', 'ConvertTo-Json')",
                "    CmdletsToExport = @()",
                "}",
                "",
            )
        ),
        encoding="utf-8",
    )
    (utility_root / "Microsoft.PowerShell.Utility.psm1").write_text(
        "Import-Module -Name ([IO.Path]::Combine($PSHOME, 'Modules', "
        "'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) -Force\n"
        "function Get-FileHash { throw 'CONTAMINATED_UTILITY_GetFileHash' }\n"
        "function ConvertFrom-Json {\n"
        "    param([Parameter(ValueFromPipeline = $true)] [object]$InputObject)\n"
        "    process { Microsoft.PowerShell.Utility\\ConvertFrom-Json -InputObject $InputObject }\n"
        "}\n"
        "function ConvertTo-Json {\n"
        "    param([Parameter(ValueFromPipeline = $true)] [object]$InputObject, [int]$Depth = 2, [switch]$Compress)\n"
        "    process {\n"
        "        if ($Compress) { Microsoft.PowerShell.Utility\\ConvertTo-Json -InputObject $InputObject -Depth $Depth -Compress }\n"
        "        else { Microsoft.PowerShell.Utility\\ConvertTo-Json -InputObject $InputObject -Depth $Depth }\n"
        "    }\n"
        "}\n"
        "Export-ModuleMember -Function Get-FileHash, ConvertFrom-Json, ConvertTo-Json\n",
        encoding="utf-8",
    )

    profile_root = tmp_path / "profile"
    install_root = profile_root / "local-llm-agent" / "install"
    bin_root = install_root / "bin"
    (install_root / "versions").mkdir(parents=True)
    bin_root.mkdir()
    (install_root / "transactions").mkdir()
    launcher = bin_root / "llm-agent.cmd"
    launcher.write_bytes(b"@echo off\r\n")
    receipt = {
        "schema_version": "W18-INSTALL-RECEIPT-V3",
        "status": "committed",
        "install_root": str(install_root),
        "candidate_id": "w18-" + "c" * 32,
        "source": {
            "status": "uncommitted_candidate",
            "base_commit": "a" * 40,
            "commit": None,
            "tree": "b" * 40,
        },
        "application": {
            "version": "0.2.0rc1",
            "wheel": "local_llm_agent-0.2.0rc1-py3-none-any.whl",
            "wheel_sha256": "5" * 64,
        },
        "payload": {
            "path": "payload-windows-x64.zip",
            "sha256": "1" * 64,
            "inventory": "payload-files.json",
            "inventory_sha256": "2" * 64,
        },
        "runtime_lock_sha256": "3" * 64,
        "embedded_python": {
            "implementation": "cpython",
            "exact_version": "3.12.14",
            "platform": "windows-x86_64",
            "build_identity": "cpython-3.12.14-windows-x86_64-none",
            "build_date": "20260901",
        },
        "stable_launcher": {"path": str(launcher), "sha256": "0" * 64},
        "owned_path": str(bin_root),
        "previous_candidate_id": "",
        "installed_at": "2026-09-17T00:00:00Z",
    }
    (install_root / "current-install.json").write_text(json.dumps(receipt), encoding="utf-8")

    environment = os.environ.copy()
    for key in list(environment):
        if key.casefold() == "psmodulepath":
            del environment[key]
    environment["LOCALAPPDATA"] = str(profile_root)
    environment["PSModulePath"] = str(module_root)
    return module_root, environment


def _run_installer(
    script: Path, powershell: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    command: str | list[str]
    if script.suffix.casefold() == ".cmd":
        comspec = environment.get("ComSpec") or str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
        command = f'"{comspec}" /d /c call "{script}" -Operation Uninstall'
    else:
        command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Operation",
            "Uninstall",
        ]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_isolated_uninstall(
    powershell: Path, local_app_data: Path
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.casefold() == "psmodulepath":
            del environment[key]
    environment["LOCALAPPDATA"] = str(local_app_data)
    return _run_installer(INSTALLER, powershell, environment)


def _run_ancestry_harness(
    powershell: Path, root: Path, local_app_data: Path, scenario: str
) -> subprocess.CompletedProcess[str]:
    installer_literal = "'" + str(INSTALLER).replace("'", "''") + "'"
    local_literal = "'" + str(local_app_data).replace("'", "''") + "'"
    script = root / "installer-r2-harness.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$sourcePath = " + installer_literal + "\n"
        "$tokens = $null; $parseErrors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)\n"
        "if ($parseErrors.Count) { throw 'installer parser failed' }\n"
        "$wanted = @('Test-ReparsePoint','Assert-InstallPathAncestrySafe','Assert-ContainedPath',"
        "'Assert-DirectorySafe','Get-InstallPaths','Assert-InstallRootLayout','Ensure-InstallLayout','Remove-OwnedTree',"
        "'Read-CurrentJournal','Read-JsonDocument','Assert-ExactProperties','Assert-NonEmptyString',"
        "'Assert-Sha256','New-JournalDocument','Assert-Journal','Recover-IncompleteJournal',"
        "'Remove-JournalAndTransaction')\n"
        "$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $wanted -contains $node.Name }, $true)\n"
        "Invoke-Expression (($functions | ForEach-Object { $_.Extent.Text }) -join \"`n\")\n"
        "$env:LOCALAPPDATA = " + local_literal + "\n"
        + scenario,
        encoding="utf-8-sig",
    )
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_contaminated_boundary(
    powershell: Path, environment: dict[str, str], script: Path = INSTALLER
) -> subprocess.CompletedProcess[str]:
    installer_literal = "'" + str(script).replace("'", "''") + "'"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$management = [IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', "
        "'Microsoft.PowerShell.Management.psd1'); "
        "Import-Module -Name $management -Force -ErrorAction Stop; "
        "function global:Add-Type { param([string]$AssemblyName); "
        "[Reflection.Assembly]::LoadWithPartialName($AssemblyName) | Out-Null }; "
        "function global:Write-Error { param([object]$Message); "
        "[Console]::Error.WriteLine([string]$Message) }; "
        "$PSModuleAutoloadingPreference = 'None'; "
        "foreach ($module in @(Get-Module -Name Microsoft.PowerShell.Utility)) { "
        "Remove-Module -ModuleInfo $module -Force -ErrorAction Stop }; "
        f"& {installer_literal} -Operation Uninstall"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_command,
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 boundary is Windows-only")
def test_installer_bootstrap_escapes_inherited_core_module_path(installer_tmp_root: Path) -> None:
    """A contaminated parent environment cannot replace native Utility cmdlets."""

    powershell = _windows_powershell()
    _module_root, environment = _prepare_contaminated_install(installer_tmp_root)

    results = [_run_contaminated_boundary(powershell, environment)]
    results.append(_run_installer(INSTALLER_CMD, powershell, environment))
    for result in results:
        output = (result.stdout + "\n" + result.stderr).replace("\x00", "")

        assert result.returncode != 0
        assert "divergiu do receipt" in output
        assert "CONTAMINATED_UTILITY_GetFileHash" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 boundary is Windows-only")
def test_payload_firewall_treats_transactions_as_a_path_component(installer_tmp_root: Path) -> None:
    """Runtime transactions.py is allowed while transaction state remains blocked."""

    powershell = _windows_powershell()
    source = INSTALLER.read_text(encoding="utf-8")
    start = source.index("function Test-ReparsePoint")
    end = source.index("function Read-RegistryPathSnapshot")
    harness = installer_tmp_root / "payload-firewall-harness.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + source[start:end]
        + r'''
function New-PayloadFile {
    param([string]$Root, [string]$Relative)

    $path = Join-Path $Root ($Relative -replace '/', '\')
    $parent = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [IO.File]::WriteAllText($path, "fixture")
}

function New-PayloadCandidate {
    param([string]$Root, [string[]]$Extras)

    foreach ($relative in @(
        "runtime/python.exe",
        "bin/llm-agent.cmd",
        "app/launcher.py"
    ) + @($Extras)) {
        New-PayloadFile $Root $relative
    }
}

function Assert-PayloadRejected {
    param([string]$Root)

    try {
        Assert-PayloadFirewall $Root
    }
    catch {
        if ($_.Exception.Message -notlike "*transactions*") { throw }
        return
    }
    throw "expected payload firewall rejection for $Root"
}

$allowed = Join-Path $PSScriptRoot "allowed"
New-PayloadCandidate $allowed @(
    "runtime/Lib/site-packages/agent/engineering/transactions.py",
    "foo/mytransactions.py",
    "foo/transactions_helper.py"
)
Assert-PayloadFirewall $allowed

$rejected = Join-Path $PSScriptRoot "rejected"
New-PayloadCandidate $rejected @("runtime/transactions/state.json")
Assert-PayloadRejected $rejected
''',
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    assert result.returncode == 0, output


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
@pytest.mark.parametrize(
    "junction_relative",
    ("local-llm-agent", r"local-llm-agent\install", r"local-llm-agent\install\versions"),
)
def test_ancestor_junction_fails_before_redirected_install_creation(
    installer_tmp_root: Path, junction_relative: str
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    local_app_data.mkdir(parents=True)
    external.mkdir()
    if "\\" in junction_relative:
        (local_app_data / Path(junction_relative).parent).mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    junction = local_app_data / junction_relative
    target = external if junction_relative == "local-llm-agent" else external
    scenario = (
        "$junction = '" + str(junction).replace("'", "''") + "'\n"
        "$target = '" + str(target).replace("'", "''") + "'\n"
        "try { New-Item -ItemType Junction -Path $junction -Target $target -ErrorAction Stop | Out-Null } "
        "catch { [Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "try { $paths = Get-InstallPaths; Ensure-InstallLayout $paths; throw 'R2_NOT_REJECTED' } catch { "
        "if ($_.Exception.Message -eq 'R2_NOT_REJECTED') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }\n"
        "if (Test-Path -LiteralPath (Join-Path $target 'install')) { throw 'REDIRECTED_INSTALL_CREATED' }\n"
        "'R2_REJECTED_BEFORE_REDIRECTED_WRITE'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE" in output:
        pytest.skip(f"junction creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "R2_REJECTED_BEFORE_REDIRECTED_WRITE" in output
    assert sentinel.read_bytes() == b"keep-me"
    assert not (external / "install").exists()
    assert sorted(path.name for path in external.iterdir()) == ["sentinel.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_clean_layout_and_nested_recovery_path_reject_redirection(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    local_app_data.mkdir(parents=True)
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    scenario = (
        "$paths = Get-InstallPaths; Ensure-InstallLayout $paths\n"
        "foreach ($directory in @($paths.InstallRoot,$paths.VersionsRoot,$paths.BinRoot,$paths.TransactionsRoot)) { "
        "if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'CLEAN_LAYOUT_MISSING' } }\n"
        "$tx = Join-Path $paths.TransactionsRoot 'w18-install-20990101000000000-aaaaaaaaaaaa'; "
        "New-Item -ItemType Directory -Path $tx | Out-Null\n"
        "$backup = Join-Path $tx 'candidate-backup'; $target = '" + str(external).replace("'", "''") + "'\n"
        "try { New-Item -ItemType Junction -Path $backup -Target $target -ErrorAction Stop | Out-Null } "
        "catch { [Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "foreach ($operation in @('containment','cleanup')) { try { "
        "if ($operation -eq 'containment') { Assert-ContainedPath $paths.TransactionsRoot $backup } "
        "else { Remove-OwnedTree $paths.TransactionsRoot $backup }; "
        "throw 'R2_NESTED_BYPASS' } catch { if ($_.Exception.Message -eq 'R2_NESTED_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point|link-like') { throw } } }\n"
        "'CLEAN_LAYOUT_PASS_NESTED_RECOVERY_REJECTED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE" in output:
        pytest.skip(f"junction creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "CLEAN_LAYOUT_PASS_NESTED_RECOVERY_REJECTED" in output
    assert sentinel.read_bytes() == b"keep-me"
    assert sorted(path.name for path in external.iterdir()) == ["sentinel.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 recovery validation is Windows-only")
def test_recovery_refuses_junctioned_transactions_root_before_journal_read(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    local_app_data.mkdir(parents=True)
    external.mkdir()
    journal = external / "current.journal.json"
    journal.write_bytes(b'{"external":"sentinel"}')
    scenario = (
        "$paths = Get-InstallPaths; Ensure-InstallLayout $paths\n"
        "Remove-Item -LiteralPath $paths.TransactionsRoot -Force\n"
        "try { New-Item -ItemType Junction -Path $paths.TransactionsRoot -Target '"
        + str(external).replace("'", "''")
        + "' -ErrorAction Stop | Out-Null } catch { "
        "[Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "try { Recover-IncompleteJournal $paths; throw 'R2_RECOVERY_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'R2_RECOVERY_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }; 'RECOVERY_REJECTED_BEFORE_READ'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE" in output:
        pytest.skip(f"junction creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "RECOVERY_REJECTED_BEFORE_READ" in output
    assert journal.read_bytes() == b'{"external":"sentinel"}'
    assert sorted(path.name for path in external.iterdir()) == ["current.journal.json"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_file_in_install_ancestor_fails_closed(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    local_app_data = installer_tmp_root / "local-app-data"
    local_app_data.mkdir()
    (local_app_data / "local-llm-agent").write_bytes(b"not a directory")
    result = _run_ancestry_harness(
        powershell,
        installer_tmp_root,
        local_app_data,
        "try { Get-InstallPaths | Out-Null; throw 'R2_FILE_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'R2_FILE_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'not a directory') { throw } }; 'FILE_ANCESTOR_REJECTED'\n",
    )
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    assert result.returncode == 0, output
    assert "FILE_ANCESTOR_REJECTED" in output


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_localappdata_reparse_root_is_rejected(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    base.mkdir()
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    scenario = (
        "try { New-Item -ItemType Junction -Path $env:LOCALAPPDATA -Target '"
        + str(external).replace("'", "''")
        + "' -ErrorAction Stop | Out-Null } catch { "
        "[Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "try { Get-InstallPaths | Out-Null; throw 'R2_LOCALAPPDATA_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'R2_LOCALAPPDATA_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }; 'LOCALAPPDATA_REPARSE_REJECTED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE" in output:
        pytest.skip(f"junction creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "LOCALAPPDATA_REPARSE_REJECTED" in output
    assert sentinel.read_bytes() == b"keep-me"
    assert sorted(path.name for path in external.iterdir()) == ["sentinel.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestor validation is Windows-only")
def test_reparse_point_above_localappdata_is_rejected(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    base.mkdir()
    profile_link = base / "profile-anchor"
    local_app_data = profile_link / "local-app-data"
    external = base / "external-target"
    external_local = external / "local-app-data"
    external_local.mkdir(parents=True)
    sentinel = external_local / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    scenario = (
        "try { New-Item -ItemType Junction -Path '"
        + str(profile_link).replace("'", "''")
        + "' -Target '"
        + str(external).replace("'", "''")
        + "' -ErrorAction Stop | Out-Null } catch { "
        "[Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "try { Get-InstallPaths | Out-Null; throw 'R2_PARENT_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'R2_PARENT_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }; 'PARENT_REPARSE_REJECTED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE" in output:
        pytest.skip(f"junction creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "PARENT_REPARSE_REJECTED" in output
    assert sentinel.read_bytes() == b"keep-me"
    assert sorted(path.name for path in external.iterdir()) == ["local-app-data"]
    _record_evidence(
        "N1-E-unsafe-existing-required-ancestor",
        {
            "junction_creation": "success",
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "expected_rejection": "PARENT_REPARSE_REJECTED",
            "external_sentinel_sha256": hashlib.sha256(sentinel.read_bytes()).hexdigest(),
            "external_localappdata_only": sorted(path.name for path in external.iterdir()) == ["local-app-data"],
        },
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 symbolic-link validation is Windows-only")
def test_symbolic_link_ancestor_is_rejected_when_creation_is_available(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    local_app_data.mkdir(parents=True)
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    link = local_app_data / "local-llm-agent"
    scenario = (
        "$link = '" + str(link).replace("'", "''") + "'\n"
        "$target = '" + str(external).replace("'", "''") + "'\n"
        "try { New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null } "
        "catch { [Console]::Error.WriteLine('SYMLINK_UNAVAILABLE: ' + $_.Exception.GetType().Name); exit 77 }\n"
        "try { Get-InstallPaths | Out-Null; throw 'R2_SYMLINK_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'R2_SYMLINK_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }; 'SYMLINK_ANCESTOR_REJECTED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "SYMLINK_UNAVAILABLE" in output:
        pytest.skip(f"symbolic-link creation unavailable without elevated permissions: {output.strip()}")
    assert result.returncode == 0, output
    assert "SYMLINK_ANCESTOR_REJECTED" in output
    assert sentinel.read_bytes() == b"keep-me"
    assert sorted(path.name for path in external.iterdir()) == ["sentinel.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 production entry point is Windows-only")
def test_n1_a_missing_localappdata_fails_before_mutation(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    required_parent = base / "profile" / "AppData"
    required_parent.mkdir(parents=True)
    local_app_data = required_parent / "Local"
    sentinel = base / "external-sentinel.txt"
    sentinel.write_bytes(b"n1-a-sentinel")
    sentinel_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()

    result = _run_isolated_uninstall(powershell, local_app_data)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    product_root = local_app_data / "local-llm-agent"
    _record_evidence(
        "N1-A-missing-localappdata",
        {
            "scenario": "full installer Uninstall entry point with a missing synthetic LOCALAPPDATA",
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "required_component": "Local",
            "localappdata_exists_after": local_app_data.exists(),
            "product_root_exists_after": product_root.exists(),
            "external_sentinel_sha256_before_after": [sentinel_hash, hashlib.sha256(sentinel.read_bytes()).hexdigest()],
            "external_sentinel_preserved": sentinel.read_bytes() == b"n1-a-sentinel",
        },
    )

    assert result.returncode == 1, output
    assert "required LOCALAPPDATA ancestry does not exist" in " ".join(output.split())
    assert "".join(str(local_app_data).split()).casefold() in "".join(output.split()).casefold()
    assert not local_app_data.exists()
    assert not product_root.exists()
    assert not (product_root / "install").exists()
    assert not (product_root / "install" / "transactions" / "current.journal.json").exists()
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == sentinel_hash


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 production entry point is Windows-only")
def test_n1_b_missing_mandatory_profile_ancestor_fails_before_creation(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    base.mkdir()
    missing_profile = base / "profile-anchor"
    local_app_data = missing_profile / "AppData" / "Local"
    sentinel = base / "external-sentinel.txt"
    sentinel.write_bytes(b"n1-b-sentinel")
    sentinel_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()

    result = _run_isolated_uninstall(powershell, local_app_data)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    product_root = local_app_data / "local-llm-agent"
    _record_evidence(
        "N1-B-missing-mandatory-ancestor",
        {
            "scenario": "full installer Uninstall entry point with a missing intermediate profile component",
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "missing_component": "profile-anchor",
            "missing_profile_exists_after": missing_profile.exists(),
            "localappdata_exists_after": local_app_data.exists(),
            "product_root_exists_after": product_root.exists(),
            "external_sentinel_sha256_before_after": [sentinel_hash, hashlib.sha256(sentinel.read_bytes()).hexdigest()],
            "external_sentinel_preserved": sentinel.read_bytes() == b"n1-b-sentinel",
        },
    )

    assert result.returncode == 1, output
    assert "required LOCALAPPDATA ancestry does not exist" in " ".join(output.split())
    assert "".join(str(missing_profile).split()).casefold() in "".join(output.split()).casefold()
    assert not missing_profile.exists()
    assert not local_app_data.exists()
    assert not product_root.exists()
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == sentinel_hash


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_n1_c_missing_product_descendants_are_creatable_after_localappdata(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    local_app_data = installer_tmp_root / "synthetic-profile" / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    install_root = local_app_data / "local-llm-agent" / "install"
    assert not install_root.exists()
    scenario = (
        "$paths = Get-InstallPaths; Ensure-InstallLayout $paths\n"
        "foreach ($directory in @($paths.InstallRoot,$paths.VersionsRoot,$paths.BinRoot,$paths.TransactionsRoot)) { "
        "if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'N1C_LAYOUT_MISSING' } }\n"
        "'N1C_PRODUCT_DESCENDANTS_CREATED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    _record_evidence(
        "N1-C-missing-product-descendants",
        {
            "scenario": "real Get-InstallPaths and Ensure-InstallLayout functions with existing LOCALAPPDATA",
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "product_layout_created": install_root.is_dir()
            and (install_root / "versions").is_dir()
            and (install_root / "bin").is_dir()
            and (install_root / "transactions").is_dir(),
        },
    )
    assert result.returncode == 0, output
    assert "N1C_PRODUCT_DESCENDANTS_CREATED" in output
    assert install_root.is_dir()
    assert (install_root / "versions").is_dir()
    assert (install_root / "bin").is_dir()
    assert (install_root / "transactions").is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_n1_d_existing_safe_ancestry_remains_accepted(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    local_app_data = installer_tmp_root / "profile with spaces" / "AppData" / "Local"
    install_root = local_app_data / "local-llm-agent" / "install"
    for directory in (
        install_root / "versions",
        install_root / "bin",
        install_root / "transactions",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    result = _run_ancestry_harness(
        powershell,
        installer_tmp_root,
        local_app_data,
        "$paths = Get-InstallPaths; Ensure-InstallLayout $paths; "
        "Assert-InstallPathAncestrySafe ($paths.InstallRoot.ToUpperInvariant()) -RequireDirectory; "
        "$localVolume = [IO.Path]::GetPathRoot($env:LOCALAPPDATA); "
        "$otherVolume = [IO.Path]::GetPathRoot($PSHOME); "
        "if ($localVolume.Equals($otherVolume, [StringComparison]::OrdinalIgnoreCase)) { "
        "throw 'N1D_DIFFERENT_VOLUME_UNAVAILABLE' }; "
        "$foreignPath = Join-Path $otherVolume 'w23-boundary-probe\\local-llm-agent\\install'; "
        "Assert-InstallPathAncestrySafe $foreignPath -RequireDirectory; "
        "'N1D_SAFE_ANCESTRY_AND_DRIVE_BOUNDARY_ACCEPTED'\n",
    )
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    _record_evidence(
        "N1-D-existing-safe-ancestry",
        {
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "install_root_exists": install_root.is_dir(),
            "different_drive_boundary_marker": "N1D_SAFE_ANCESTRY_AND_DRIVE_BOUNDARY_ACCEPTED" in output,
        },
    )
    assert result.returncode == 0, output
    assert "N1D_SAFE_ANCESTRY_AND_DRIVE_BOUNDARY_ACCEPTED" in output
    assert install_root.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_n1_f_file_in_mandatory_ancestry_fails_closed(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    profile_anchor = installer_tmp_root / "profile-anchor-file"
    profile_anchor.write_bytes(b"must-remain-a-file")
    local_app_data = profile_anchor / "AppData" / "Local"
    result = _run_ancestry_harness(
        powershell,
        installer_tmp_root,
        local_app_data,
        "try { Get-InstallPaths | Out-Null; throw 'N1F_FILE_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'N1F_FILE_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'not a directory') { throw } }; 'N1F_FILE_REJECTED'\n",
    )
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    _record_evidence(
        "N1-F-file-in-mandatory-ancestry",
        {
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "file_sha256": hashlib.sha256(profile_anchor.read_bytes()).hexdigest(),
            "localappdata_exists": local_app_data.exists(),
            "product_root_exists": (local_app_data / "local-llm-agent").exists(),
        },
    )
    assert result.returncode == 0, output
    assert "N1F_FILE_REJECTED" in output
    assert profile_anchor.read_bytes() == b"must-remain-a-file"
    assert not local_app_data.exists()
    assert not (local_app_data / "local-llm-agent").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 ancestry validation is Windows-only")
def test_n1_g_inspection_failure_is_not_treated_as_missing(installer_tmp_root: Path) -> None:
    powershell = _windows_powershell()
    local_app_data = installer_tmp_root / "profile" / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    failure_component = local_app_data
    scenario = (
        "$script:inspectionFailurePath = '" + str(failure_component).replace("'", "''") + "'\n"
        "function global:Get-Item {\n"
        "param([string]$LiteralPath,[switch]$Force,[string]$ErrorAction='Continue')\n"
        "if ($LiteralPath -ieq $script:inspectionFailurePath) { "
        "throw [System.UnauthorizedAccessException]::new('controlled inspection failure') }\n"
        "Microsoft.PowerShell.Management\\Get-Item -LiteralPath $LiteralPath -Force:$Force -ErrorAction $ErrorAction\n"
        "}\n"
        "try { Get-InstallPaths | Out-Null; throw 'N1G_INSPECTION_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'N1G_INSPECTION_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'could not be inspected') { throw } }; 'N1G_INSPECTION_FAILED_CLOSED'\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    _record_evidence(
        "N1-G-inspection-failure",
        {
            "injected_component": "LOCALAPPDATA",
            "injected_exception": "System.UnauthorizedAccessException",
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "product_root_exists": (local_app_data / "local-llm-agent").exists(),
        },
    )
    assert result.returncode == 0, output
    assert "N1G_INSPECTION_FAILED_CLOSED" in output
    assert not (local_app_data / "local-llm-agent").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 committed recovery is Windows-only")
def test_r1_committed_recovery_cleanup_with_nested_junction_and_unexpected_file(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "profile" / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    external = base / "external-target"
    external.mkdir()
    sentinel = external / "sentinel.bin"
    sentinel.write_bytes(b"external-transaction-cleanup-sentinel\x00\x01")
    sentinel_hash_before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    sentinel_listing_before = sorted(path.name for path in external.iterdir())
    sibling_name = "unrelated-transaction-sibling.bin"
    sibling_bytes = b"preserve-sibling"
    transaction_id = "w18-install-20990101000000000-aaaaaaaaaaaa"
    transaction_root = local_app_data / "local-llm-agent" / "install" / "transactions" / transaction_id
    nested_link = transaction_root / "candidate-stage" / "external-link"
    unexpected_file = transaction_root / "unexpected-regular-file.bin"
    journal_file = local_app_data / "local-llm-agent" / "install" / "transactions" / "current.journal.json"
    unrelated_sibling = local_app_data / "local-llm-agent" / "install" / "transactions" / sibling_name
    scenario = (
        "$script:JournalSchemaVersion = 1\n"
        "$script:JournalStates = @('PREPARED','STAGED','LAUNCHER_PROMOTED','PATH_MUTATED',"
        "'FRESH_SHELL_VERIFIED','COMMITTED','ROLLING_BACK','ROLLED_BACK','FAILED_RECOVERY')\n"
        "$paths = Get-InstallPaths; Ensure-InstallLayout $paths\n"
        "$transactionId = '" + transaction_id + "'\n"
        "$transactionRoot = Join-Path $paths.TransactionsRoot $transactionId\n"
        "$candidateId = 'w18-" + "c" * 32 + "'\n"
        "$candidatePath = Join-Path $paths.VersionsRoot $candidateId\n"
        "$candidateStage = Join-Path $transactionRoot 'candidate-stage'\n"
        "New-Item -ItemType Directory -Path $candidateStage -Force | Out-Null\n"
        "$external = '" + str(external).replace("'", "''") + "'\n"
        "$nestedLink = Join-Path $candidateStage 'external-link'\n"
        "try { New-Item -ItemType Junction -Path $nestedLink -Target $external -ErrorAction Stop | Out-Null } "
        "catch { [Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message); exit 77 }\n"
        "[IO.File]::WriteAllBytes((Join-Path $transactionRoot 'unexpected-regular-file.bin'), [byte[]](1,2,3,4))\n"
        "$sibling = Join-Path $paths.TransactionsRoot '" + sibling_name + "'\n"
        "[IO.File]::WriteAllBytes($sibling, [Text.Encoding]::UTF8.GetBytes('preserve-sibling'))\n"
        "$priorPath = [pscustomobject]@{ key_present=$false; present=$false; value=$null; kind=$null }\n"
        "$journal = New-JournalDocument 'install' $paths $true $transactionId $transactionRoot $candidateId "
        "$candidatePath $candidateStage '' ('1'*64) ('2'*64) $priorPath $false '' '' $false '' '' ''\n"
        "$journal.state = 'COMMITTED'\n"
        "$json = ($journal | ConvertTo-Json -Depth 40) + \"`n\"\n"
        "[IO.File]::WriteAllText($paths.JournalPath, $json, [Text.Encoding]::UTF8)\n"
        "try { Recover-IncompleteJournal $paths; 'R1_CLEANUP_RETURN=SUCCESS' } catch { "
        "'R1_CLEANUP_RETURN=REJECTED'; 'R1_CLEANUP_ERROR=' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE:" in output:
        _record_evidence(
            "R1-committed-cleanup-junction-unavailable",
            {
                "scenario": "actual Recover-IncompleteJournal COMMITTED cleanup",
                "exit_code": result.returncode,
                "stdout_stderr": output,
                "junction_creation": "unavailable",
                "external_sentinel_sha256": sentinel_hash_before,
            },
        )
        pytest.skip(output.strip())

    cleanup_succeeded = "R1_CLEANUP_RETURN=SUCCESS" in output
    sentinel_exists_after = sentinel.exists()
    sentinel_hash_after = hashlib.sha256(sentinel.read_bytes()).hexdigest() if sentinel_exists_after else None
    external_listing_after = sorted(path.name for path in external.iterdir()) if external.exists() else None
    transaction_exists_after = transaction_root.exists()
    junction_exists_after = os.path.lexists(nested_link)
    unexpected_exists_after = unexpected_file.exists()
    journal_exists_after = journal_file.exists()
    sibling_exists_after = unrelated_sibling.exists()
    sibling_hash_after = hashlib.sha256(unrelated_sibling.read_bytes()).hexdigest() if sibling_exists_after else None
    evidence = {
        "scenario": "actual Recover-IncompleteJournal on a schema-valid COMMITTED journal",
        "exit_code": result.returncode,
        "stdout_stderr": output,
        "cleanup_result": "SUCCESS" if cleanup_succeeded else "REJECTED",
        "junction_created": True,
        "nested_junction_exists_after": junction_exists_after,
        "transaction_root_exists_after": transaction_exists_after,
        "unexpected_regular_file_exists_after": unexpected_exists_after,
        "journal_exists_after": journal_exists_after,
        "external_target_observation": "no read-traversal instrumentation; mutation checked by sentinel/listing",
        "external_target_listing_before_after": [sentinel_listing_before, external_listing_after],
        "external_sentinel_sha256_before_after": [sentinel_hash_before, sentinel_hash_after],
        "external_sentinel_survived_unchanged": sentinel_exists_after and sentinel_hash_after == sentinel_hash_before,
        "unrelated_sibling_exists_after": sibling_exists_after,
        "unrelated_sibling_sha256_after": sibling_hash_after,
        "all_test_paths_under_disposable_root": all(
            path.resolve().is_relative_to(installer_tmp_root.resolve())
            for path in (base, external, transaction_root, journal_file, unrelated_sibling)
        ),
    }
    _record_evidence("R1-committed-cleanup", evidence)

    assert result.returncode == 0, output
    assert "R1_CLEANUP_RETURN=" in output
    assert evidence["all_test_paths_under_disposable_root"] is True
    assert evidence["external_sentinel_survived_unchanged"] is True, json.dumps(evidence, indent=2)
    assert external_listing_after == sentinel_listing_before
    assert sibling_exists_after
    assert sibling_hash_after == hashlib.sha256(sibling_bytes).hexdigest()
    if cleanup_succeeded:
        assert not transaction_exists_after
        assert not junction_exists_after
        assert not unexpected_exists_after
        assert not journal_exists_after
    else:
        assert transaction_exists_after
        assert junction_exists_after
        assert unexpected_exists_after
        assert journal_exists_after


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 static-junction reproducer is Windows-only")
def test_n1_h_original_static_install_junction_reproducer_remains_rejected(
    installer_tmp_root: Path,
) -> None:
    powershell = _windows_powershell()
    base = installer_tmp_root / uuid.uuid4().hex
    local_app_data = base / "local-app-data"
    external = base / "external-target"
    local_app_data.mkdir(parents=True)
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"original-r2-sentinel")
    sentinel_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    scenario = (
        "$junction = '" + str(local_app_data / "local-llm-agent").replace("'", "''") + "'\n"
        "$target = '" + str(external).replace("'", "''") + "'\n"
        "try { New-Item -ItemType Junction -Path $junction -Target $target -ErrorAction Stop | Out-Null } "
        "catch { [Console]::Error.WriteLine('JUNCTION_UNAVAILABLE: ' + $_.Exception.GetType().FullName); exit 77 }\n"
        "try { $paths = Get-InstallPaths; Ensure-InstallLayout $paths; throw 'N1H_R2_BYPASS' } catch { "
        "if ($_.Exception.Message -eq 'N1H_R2_BYPASS') { throw }; "
        "if ($_.Exception.Message -notmatch 'reparse point') { throw } }; 'N1H_STATIC_JUNCTION_REJECTED'\n"
        "if (Test-Path -LiteralPath (Join-Path $target 'install')) { throw 'N1H_REDIRECTED_INSTALL_CREATED' }\n"
    )
    result = _run_ancestry_harness(powershell, installer_tmp_root, local_app_data, scenario)
    output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
    if result.returncode == 77 or "JUNCTION_UNAVAILABLE:" in output:
        _record_evidence(
            "N1-H-static-junction-unavailable",
            {"exit_code": result.returncode, "stdout_stderr": output, "sentinel_sha256": sentinel_hash},
        )
        pytest.skip(output.strip())
    _record_evidence(
        "N1-H-original-static-junction",
        {
            "exit_code": result.returncode,
            "stdout_stderr": output,
            "sentinel_sha256_before_after": [sentinel_hash, hashlib.sha256(sentinel.read_bytes()).hexdigest()],
            "redirected_install_created": (external / "install").exists(),
        },
    )
    assert result.returncode == 0, output
    assert "N1H_STATIC_JUNCTION_REJECTED" in output
    assert not (external / "install").exists()
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == sentinel_hash
