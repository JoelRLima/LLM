"""Focused Windows PowerShell boundary regression tests for W18."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer" / "install.ps1"
INSTALLER_CMD = ROOT / "installer" / "install.cmd"


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
def test_installer_bootstrap_escapes_inherited_core_module_path(tmp_path: Path) -> None:
    """A contaminated parent environment cannot replace native Utility cmdlets."""

    powershell = _windows_powershell()
    _module_root, environment = _prepare_contaminated_install(tmp_path)

    results = [_run_contaminated_boundary(powershell, environment)]
    results.append(_run_installer(INSTALLER_CMD, powershell, environment))
    for result in results:
        output = (result.stdout + "\n" + result.stderr).replace("\x00", "")

        assert result.returncode != 0
        assert "divergiu do receipt" in output
        assert "CONTAMINATED_UTILITY_GetFileHash" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 boundary is Windows-only")
def test_payload_firewall_treats_transactions_as_a_path_component(tmp_path: Path) -> None:
    """Runtime transactions.py is allowed while transaction state remains blocked."""

    powershell = _windows_powershell()
    source = INSTALLER.read_text(encoding="utf-8")
    start = source.index("function Test-ReparsePoint")
    end = source.index("function Read-RegistryPathSnapshot")
    harness = tmp_path / "payload-firewall-harness.ps1"
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
