"""Focused Windows probe for W18 clean-install rollback ownership.

The probe injects an early failure into a temporary copy of the installer
script while keeping the release bundle itself unchanged.  It exercises the
production rollback path against isolated ``LOCALAPPDATA`` roots and restores
the caller's HKCU User PATH snapshot before returning.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast


class ProbeFailure(AssertionError):
    """Raised when the focused lifecycle probe observes an unsafe result."""


class _Registry(Protocol):
    """Small cross-platform contract for the Windows registry operations used here."""

    HKEY_CURRENT_USER: int
    KEY_READ: int
    KEY_SET_VALUE: int

    def OpenKey(self, key: int, sub_key: str, reserved: int, access: int) -> object:
        ...

    def QueryValueEx(self, key: object, value_name: str) -> tuple[object, int]:
        ...

    def CloseKey(self, key: object) -> None:
        ...

    def SetValueEx(self, key: object, value_name: str, reserved: int, value_type: int, value: object) -> None:
        ...

    def DeleteValue(self, key: object, value_name: str) -> None:
        ...

    def QueryInfoKey(self, key: object) -> tuple[int, int, int]:
        ...

    def DeleteKey(self, key: int, sub_key: str) -> None:
        ...


def _registry() -> _Registry:
    if os.name != "nt":
        raise ProbeFailure("Windows registry is unavailable outside Windows")
    return cast(_Registry, importlib.import_module("winreg"))


@dataclass(frozen=True)
class RegistryPathSnapshot:
    key_present: bool
    path_present: bool
    value: str | None
    kind: int | None


def _read_user_path() -> RegistryPathSnapshot:
    registry = _registry()

    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, "Environment", 0, registry.KEY_READ)
    except FileNotFoundError:
        return RegistryPathSnapshot(False, False, None, None)
    try:
        try:
            value, kind = registry.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return RegistryPathSnapshot(True, False, None, None)
        if not isinstance(value, str):
            raise ProbeFailure("HKCU Environment\\Path is not a string")
        return RegistryPathSnapshot(True, True, value, kind)
    finally:
        registry.CloseKey(key)


def _restore_user_path(snapshot: RegistryPathSnapshot) -> None:
    registry = _registry()

    if snapshot.key_present:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, "Environment", 0, registry.KEY_SET_VALUE)
        try:
            if snapshot.path_present:
                if snapshot.value is None or snapshot.kind is None:
                    raise ProbeFailure("invalid original User PATH snapshot")
                registry.SetValueEx(key, "Path", 0, snapshot.kind, snapshot.value)
            else:
                try:
                    registry.DeleteValue(key, "Path")
                except FileNotFoundError:
                    pass
        finally:
            registry.CloseKey(key)
        return

    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, "Environment", 0, registry.KEY_SET_VALUE)
    except FileNotFoundError:
        return
    try:
        try:
            registry.DeleteValue(key, "Path")
        except FileNotFoundError:
            pass
        remaining = registry.QueryInfoKey(key)[1]
    finally:
        registry.CloseKey(key)
    if remaining == 0:
        try:
            registry.DeleteKey(registry.HKEY_CURRENT_USER, "Environment")
        except FileNotFoundError:
            pass


def _powershell() -> str:
    candidate = shutil.which("powershell.exe")
    if candidate:
        return candidate
    fallback = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if fallback.is_file():
        return str(fallback)
    raise ProbeFailure("Windows PowerShell 5.1 executable was not found")


def _injected_installer(source: Path, destination: Path, *, create_unknown: bool) -> Path:
    text = source.read_text(encoding="utf-8")
    marker = "    Ensure-InstallLayout $paths\n"
    if text.count(marker) != 2:
        raise ProbeFailure("installer injection marker is not unique to install/uninstall transactions")
    injected = ""
    if create_unknown:
        injected += (
            '        $unknown = Join-Path $paths.InstallRoot "unknown-focused-probe.txt"\n'
            '        [IO.File]::WriteAllText($unknown, "unknown-content")\n'
        )
    injected += '        throw "W18_FOCUSED_EARLY_FAILURE"\n'
    replacement = f"{marker}{injected}"
    destination.write_text(text.replace(marker, replacement, 1), encoding="utf-8", newline="\n")
    return destination


def _run_installer(
    *,
    script: Path,
    bundle_root: Path,
    local_appdata: Path,
    cwd: Path,
    powershell: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(local_appdata)
    return subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-BundleRoot",
            str(bundle_root),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def _expect_injected_failure(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode == 0:
        raise ProbeFailure(f"{label} unexpectedly succeeded")
    combined = f"{result.stdout}\n{result.stderr}"
    if "W18_FOCUSED_EARLY_FAILURE" not in combined:
        raise ProbeFailure(f"{label} did not reach the injected failure: {combined[-2000:]}")
    return combined


def _install_root(local_appdata: Path) -> Path:
    return local_appdata / "local-llm-agent" / "install"


def _probe_absent_root(
    *, source: Path, bundle_root: Path, workspace: Path, powershell: str, cwd: Path
) -> dict[str, str]:
    local_appdata = workspace / "absent-root" / "José Local"
    local_appdata.mkdir(parents=True)
    script = _injected_installer(source, workspace / "inject-early.ps1", create_unknown=False)
    result = _run_installer(
        script=script,
        bundle_root=bundle_root,
        local_appdata=local_appdata,
        cwd=cwd,
        powershell=powershell,
    )
    _expect_injected_failure(result, "clean install with absent root")
    root = _install_root(local_appdata)
    if root.exists():
        raise ProbeFailure(f"absent clean-install root survived rollback: {root}")
    return {"status": "passed", "root": str(root), "rollback": "root-absent"}


def _probe_preexisting_root(
    *, source: Path, bundle_root: Path, workspace: Path, powershell: str, cwd: Path
) -> dict[str, str]:
    local_appdata = workspace / "preexisting-root" / "José Local"
    root = _install_root(local_appdata)
    marker = root / "transactions" / "preexisting-marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("preexisting", encoding="utf-8")
    script = _injected_installer(source, workspace / "inject-early.ps1", create_unknown=False)
    result = _run_installer(
        script=script,
        bundle_root=bundle_root,
        local_appdata=local_appdata,
        cwd=cwd,
        powershell=powershell,
    )
    _expect_injected_failure(result, "preexisting-root clean-install failure")
    if not root.is_dir() or marker.read_text(encoding="utf-8") != "preexisting":
        raise ProbeFailure("preexisting install root/state was not preserved")
    return {"status": "passed", "root": str(root), "rollback": "preexisting-preserved"}


def _probe_unknown_content(
    *, source: Path, bundle_root: Path, workspace: Path, powershell: str, cwd: Path
) -> dict[str, str]:
    local_appdata = workspace / "unknown-content" / "José Local"
    local_appdata.mkdir(parents=True)
    script = _injected_installer(source, workspace / "inject-unknown.ps1", create_unknown=True)
    result = _run_installer(
        script=script,
        bundle_root=bundle_root,
        local_appdata=local_appdata,
        cwd=cwd,
        powershell=powershell,
    )
    combined = _expect_injected_failure(result, "unknown-content clean-install failure")
    root = _install_root(local_appdata)
    unknown = root / "unknown-focused-probe.txt"
    if not root.is_dir() or unknown.read_text(encoding="utf-8") != "unknown-content":
        raise ProbeFailure("unknown root content was removed during rollback")
    if "conteudo nao reconhecido" not in combined:
        raise ProbeFailure("unknown root content was not reported by rollback cleanup")
    return {"status": "passed", "root": str(root), "rollback": "unknown-preserved-and-reported"}


def _probe_committed_root_guard(*, source: Path, bundle_root: Path, workspace: Path, powershell: str, cwd: Path) -> dict[str, str]:
    text = source.read_text(encoding="utf-8")
    marker = "$lease = $null\ntry {\n"
    if text.count(marker) != 1:
        raise ProbeFailure("installer main-entry marker is not unique")
    probe_code = r'''
$probeRoot = Join-Path ([IO.Path]::GetTempPath()) ("w18-cleanup-function-" + [Guid]::NewGuid().ToString("N"))
function New-TestPaths {
    param([string]$Root)
    return [pscustomobject]@{
        InstallRoot = $Root
        VersionsRoot = Join-Path $Root "versions"
        BinRoot = Join-Path $Root "bin"
        TransactionsRoot = Join-Path $Root "transactions"
    }
}
function Assert-TestCondition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
try {
    $absentRoot = Join-Path $probeRoot "absent"
    $absentPaths = New-TestPaths $absentRoot
    New-Item -ItemType Directory -Path $absentPaths.VersionsRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $absentPaths.BinRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $absentPaths.TransactionsRoot -Force | Out-Null
    Remove-CreatedInstallScaffolding $absentPaths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $false })
    Assert-TestCondition (-not (Test-Path -LiteralPath $absentRoot)) "absent root was not removed by the production cleanup function"

    $preexistingRoot = Join-Path $probeRoot "preexisting"
    $preexistingPaths = New-TestPaths $preexistingRoot
    $preexistingMarker = Join-Path $preexistingPaths.TransactionsRoot "preexisting-marker.txt"
    New-Item -ItemType Directory -Path $preexistingPaths.TransactionsRoot -Force | Out-Null
    [IO.File]::WriteAllText($preexistingMarker, "preexisting")
    Remove-CreatedInstallScaffolding $preexistingPaths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $true })
    Assert-TestCondition (Test-Path -LiteralPath $preexistingMarker -PathType Leaf) "preexisting root was removed by the production cleanup function"

    $unknownRoot = Join-Path $probeRoot "unknown"
    $unknownPaths = New-TestPaths $unknownRoot
    $unknownMarker = Join-Path $unknownRoot "unknown.txt"
    New-Item -ItemType Directory -Path $unknownPaths.VersionsRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $unknownPaths.BinRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $unknownPaths.TransactionsRoot -Force | Out-Null
    [IO.File]::WriteAllText($unknownMarker, "unknown")
    Remove-CreatedInstallScaffolding $unknownPaths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $false })
    Assert-TestCondition (Test-Path -LiteralPath $unknownMarker -PathType Leaf) "unknown root content was removed by the production cleanup function"

    $rootObject = Join-Path $probeRoot "root-object"
    [IO.File]::WriteAllText($rootObject, "unknown-root-object")
    $rootObjectPaths = New-TestPaths $rootObject
    Remove-CreatedInstallScaffolding $rootObjectPaths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $false })
    Assert-TestCondition (Test-Path -LiteralPath $rootObject -PathType Leaf) "unknown install-root object was removed by the production cleanup function"

    $committedRoot = Join-Path $probeRoot "committed"
    $committedPaths = New-TestPaths $committedRoot
    $committedCandidate = Join-Path $committedPaths.VersionsRoot ("w18-" + ("a" * 32))
    $committedMarker = Join-Path $committedCandidate "committed.marker"
    New-Item -ItemType Directory -Path $committedCandidate -Force | Out-Null
    [IO.File]::WriteAllText($committedMarker, "committed")
    Remove-CreatedInstallScaffolding $committedPaths ([pscustomobject]@{ operation = "install"; prior_install_root_present = $false })
    Assert-TestCondition (Test-Path -LiteralPath $committedMarker -PathType Leaf) "committed candidate was removed by the production cleanup function"
    Write-Output "W18_CLEANUP_FUNCTION_PASS"
}
finally {
    if (Test-Path -LiteralPath $probeRoot) { Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
'''
    replacement = f"{probe_code}\nreturn\n{marker}"
    script = workspace / "probe-cleanup-function.ps1"
    script.write_text(text.replace(marker, replacement, 1), encoding="utf-8", newline="\n")
    result = _run_installer(
        script=script,
        bundle_root=bundle_root,
        local_appdata=workspace / "unused-localappdata",
        cwd=cwd,
        powershell=powershell,
    )
    if result.returncode != 0 or "W18_CLEANUP_FUNCTION_PASS" not in result.stdout:
        combined = f"{result.stdout}\n{result.stderr}"
        raise ProbeFailure(f"committed/cleanup guard probe failed: {combined[-3000:]}")
    return {"status": "passed", "guard": "committed-candidate-preserved"}


def run_probe(bundle_root: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "skipped", "reason": "Windows-only focused probe"}
    bundle_root = bundle_root.resolve()
    source = bundle_root / "install.ps1"
    if not source.is_file() or not (bundle_root / "release-manifest.json").is_file():
        raise ProbeFailure("bundle root must contain the v003 installer and release manifest")
    powershell = _powershell()
    original_path = _read_user_path()
    with tempfile.TemporaryDirectory(prefix="w18-install-root-cleanup-") as raw:
        workspace = Path(raw)
        cwd = workspace / "outside cwd é"
        cwd.mkdir()
        try:
            scenarios = {
                "clean_absent_root": _probe_absent_root(
                    source=source,
                    bundle_root=bundle_root,
                    workspace=workspace,
                    powershell=powershell,
                    cwd=cwd,
                ),
                "preexisting_root": _probe_preexisting_root(
                    source=source,
                    bundle_root=bundle_root,
                    workspace=workspace,
                    powershell=powershell,
                    cwd=cwd,
                ),
                "unknown_content": _probe_unknown_content(
                    source=source,
                    bundle_root=bundle_root,
                    workspace=workspace,
                    powershell=powershell,
                    cwd=cwd,
                ),
                "committed_install": _probe_committed_root_guard(
                    source=source,
                    bundle_root=bundle_root,
                    workspace=workspace,
                    powershell=powershell,
                    cwd=cwd,
                ),
            }
            return {"status": "passed", "scenarios": scenarios}
        finally:
            current_path = _read_user_path()
            if current_path != original_path:
                _restore_user_path(original_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True, help="extracted v003 bundle root")
    parser.add_argument("--summary-json", type=Path, help="write bounded probe evidence")
    args = parser.parse_args(argv)
    try:
        result = run_probe(args.bundle_dir)
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        print(f"W18 install-root cleanup probe failed: {exc}")
        if args.summary_json:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 1
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if result["status"] == "skipped":
        print("W18_INSTALL_ROOT_CLEANUP=SKIP (Windows only)")
    else:
        print("W18_INSTALL_ROOT_CLEANUP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
