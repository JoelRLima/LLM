"""Verify the W18 installed product on a disposable Windows user profile.

This is deliberately separate from ``verify_installed_package.py``.  The
existing script proves application behaviour after a clean wheel install; this
script proves the W18 installer boundary, fresh-shell reconstruction, path
ownership, lifecycle, and preservation.  It never publishes and it does not
replace the user's final deterministic acceptance gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

if os.name == "nt":
    import winreg as _winreg

    winreg: Any = _winreg
else:  # pragma: no cover - exercised by the cross-platform skip path.
    winreg = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.runtime.paths import AppPaths  # noqa: E402
from distribution.payload import (  # noqa: E402
    PayloadValidationError,
    make_inventory,
    payload_path_leakage_reason,
    render_inventory,
    validate_inventory,
)
from distribution.release_manifest import candidate_id as derive_candidate_id  # noqa: E402
from installer.path_semantics import (  # noqa: E402
    PathSnapshot,
    equivalent_segment,
    reconstructed_persistent_path,
    remove_owned_segment,
)


class ProductVerificationError(RuntimeError):
    """Raised when installed-product acceptance cannot prove a contract."""


@dataclass(frozen=True)
class RegistryPathSnapshot:
    present: bool
    value: str | None
    kind: int | None
    kind_name: str | None
    key_present: bool

    def semantic(self) -> PathSnapshot:
        return PathSnapshot(
            present=self.present,
            value=self.value,
            kind=self.kind_name,
            key_present=self.key_present,
        )


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class FileSnapshot:
    """Read-only existence and content snapshot for a safety guard."""

    path: Path
    present: bool
    sha256: str | None


@dataclass
class ProductContext:
    bundle_root: Path
    install_script: Path
    uninstall_script: Path
    install_root: Path
    before_user: RegistryPathSnapshot
    before_machine: str | None
    working: Path
    outside: Path
    config: Path
    sentinels: tuple[Path, ...]
    report: dict[str, Any]
    application_home: Path | None = None
    app_paths: AppPaths | None = None
    real_user_config: FileSnapshot | None = None
    original_user: RegistryPathSnapshot | None = None


@dataclass(frozen=True)
class InstalledFacts:
    candidate_id: str
    candidate_python: Path
    stable: Path
    after_install_user: RegistryPathSnapshot


@dataclass
class RunningCandidate:
    """A verifier-owned installed runtime process holding a candidate lease."""

    process: subprocess.Popen[str]
    candidate_id: str


@dataclass(frozen=True)
class ActiveUninstallSnapshot:
    receipt_path: Path
    stable: Path
    versions: Path
    transactions: Path
    receipt_bytes: bytes
    stable_bytes: bytes
    candidate_digests: Mapping[str, str]
    transaction_state: Mapping[str, str]
    user_path: RegistryPathSnapshot
    machine_path: str | None
    candidate_id: str
    facts: InstalledFacts


_FAULT_SEAM_MARKERS = {
    "A47": "W18_TEST_FAULT_A47_REACHED",
    "A48": "W18_TEST_FAULT_A48_REACHED",
}


def _registry_path_snapshot(root: Any = None, subkey: str = r"Environment") -> RegistryPathSnapshot:
    if winreg is None:
        raise ProductVerificationError("Windows Registry is unavailable on this platform")
    if root is None:
        root = winreg.HKEY_CURRENT_USER
    try:
        key = winreg.OpenKey(root, subkey, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return RegistryPathSnapshot(False, None, None, None, False)
    try:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return RegistryPathSnapshot(False, None, None, None, True)
        if not isinstance(value, str):
            raise ProductVerificationError("HKCU Environment\\Path is not a string")
        if kind == winreg.REG_EXPAND_SZ:
            kind_name = "ExpandString"
        elif kind == winreg.REG_SZ:
            kind_name = "String"
        else:
            raise ProductVerificationError(f"unsupported HKCU Environment\\Path kind: {kind}")
        return RegistryPathSnapshot(True, value, kind, kind_name, True)
    finally:
        winreg.CloseKey(key)


def _machine_path() -> str | None:
    if winreg is None:
        raise ProductVerificationError("Windows Registry is unavailable on this platform")
    snapshot = _registry_path_snapshot(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    return snapshot.value


def _powershell_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    path = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not path.is_file():
        raise ProductVerificationError(f"Windows PowerShell 5.1 is missing: {path}")
    return str(path)


def _clean_child_environment(path_value: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONPATH",
        "PYTHONHOME",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PIP_CONFIG_FILE",
        "UV_CONFIG_FILE",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "UV_PYTHON_DOWNLOADS_JSON_URL",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        environment.pop(name, None)
    environment["PATH"] = path_value
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run(command: Sequence[str], *, cwd: Path, environment: Mapping[str, str]) -> CommandResult:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(tuple(command), completed.returncode, completed.stdout, completed.stderr)


def _require_success(result: CommandResult, label: str) -> CommandResult:
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        # The installer child-process diagnostic already has bounded, redacted
        # tails and must remain intact in the persisted acceptance summary.
        bounded_detail = detail if "child step:" in detail else detail[-3000:]
        raise ProductVerificationError(f"{label} failed ({result.returncode}): {bounded_detail}")
    return result


def _run_ps_file(
    script: Path,
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> CommandResult:
    return _run(
        (_powershell_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments),
        cwd=cwd,
        environment=environment,
    )


def _run_ps_command(
    script: str,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> CommandResult:
    return _run(
        (_powershell_path(), "-NoProfile", "-NonInteractive", "-Command", script),
        cwd=cwd,
        environment=environment,
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductVerificationError(f"{label} is missing or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ProductVerificationError(f"{label} must be an object: {path}")
    return value


def _install_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ProductVerificationError("LOCALAPPDATA is unavailable")
    return Path(local).resolve() / "local-llm-agent" / "install"


def _assert_owned_path_count(raw: str | None, owned: str, expected: int) -> None:
    environment = {key: value for key, value in os.environ.items()}
    segments = (raw or "").split(";") if raw else []
    count = sum(equivalent_segment(segment, owned, environment) for segment in segments)
    if count != expected:
        raise ProductVerificationError(f"expected {expected} W18 PATH owners, observed {count}: {raw!r}")


def _fresh_shell(
    *,
    install_root: Path,
    candidate_python: Path,
    forbidden_path: Path,
    machine_path: str | None,
    user_path: RegistryPathSnapshot,
    cwd: Path,
    probe_home: Path,
) -> dict[str, Any]:
    persistent_path = reconstructed_persistent_path(machine_path, user_path.semantic(), os.environ)
    environment = _clean_child_environment(persistent_path)
    probe_script = cwd / "w18-fresh-shell.ps1"
    probe_script.write_text(
        r'''param(
    [string]$ExpectedLauncher,
    [string]$ExpectedVersion,
    [string]$ProbeHome,
    [string]$ProbeCwd,
    [string]$CandidatePython,
    [string]$ForbiddenPath
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProbeCwd
$command = Get-Command -Name "llm-agent" -CommandType Application -ErrorAction Stop
$resolved = [System.IO.Path]::GetFullPath([string]$command.Source)
if (-not $resolved.Equals([System.IO.Path]::GetFullPath($ExpectedLauncher), [System.StringComparison]::OrdinalIgnoreCase)) { throw "wrong launcher: $resolved" }
$version = ((& $resolved --version 2>&1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne ("llm-agent " + $ExpectedVersion)) { throw "wrong version: $version" }
$help = ((& $resolved --help 2>&1) | Out-String)
if ($LASTEXITCODE -ne 0 -or $help -notmatch "(?i)usage:") { throw "help failed" }
& $resolved config init --home $ProbeHome | Out-Null
if ($LASTEXITCODE -ne 0) { throw "config init failed" }
$doctorText = ((& $resolved doctor --json --home $ProbeHome --workspace $ProbeCwd 2>&1) | Out-String)
if ($LASTEXITCODE -ne 0) { throw "doctor failed: $doctorText" }
$doctor = $doctorText | ConvertFrom-Json
if ($doctor.readiness.offline_ready -ne $true) { throw "doctor was not offline-ready" }
$originCode = "import agent,importlib.metadata,pathlib,sys; print(str(pathlib.Path(agent.__file__).resolve())); print(str(pathlib.Path(sys.executable).resolve())); print(agent.__version__); print(importlib.metadata.version('local-llm-agent')); print('\\n'.join(sys.path))"
$origin = ((& $CandidatePython -c $originCode 2>&1) | Out-String)
$originLines = @($origin -split "`r?`n" | Where-Object { $_.Trim() })
if ($LASTEXITCODE -ne 0 -or $originLines.Count -lt 4 -or
    $originLines[2].Trim() -ne "0.2.0rc1" -or $originLines[3].Trim() -ne "0.2.0rc1" -or
    $origin -match [regex]::Escape($ForbiddenPath)) { throw "checkout leaked into installed origin" }
[Console]::Out.Write("W18_FRESH_SHELL_PASS")
''',
        encoding="utf-8",
    )
    result = _run_ps_file(
        probe_script,
        (
            str(install_root / "bin" / "llm-agent.exe"),
            "0.2.0rc1",
            str(probe_home),
            str(cwd),
            str(candidate_python),
            str(forbidden_path),
        ),
        cwd=cwd,
        environment=environment,
    )
    _require_success(result, "fresh-shell acceptance")
    if result.stdout.strip() != "W18_FRESH_SHELL_PASS":
        raise ProductVerificationError(f"fresh-shell marker missing: {result.stdout!r}")
    return {
        "status": "passed",
        "cwd": str(cwd),
        "path": persistent_path,
        "probe_home": str(probe_home),
        "candidate_python": str(candidate_python),
    }


def _make_invalid_bundle(bundle_root: Path, destination: Path) -> Path:
    shutil.copytree(bundle_root, destination)
    manifest_path = destination / "release-manifest.json"
    document = _load_json(manifest_path, "release manifest")
    application = document.get("application")
    if not isinstance(application, dict):
        raise ProductVerificationError("canonical bundle manifest has no application object")
    application["wheel_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _state_sentinels(app_paths: AppPaths) -> tuple[Path, list[Path]]:
    """Return preservation paths from the verifier-owned application authority."""

    roots = (
        app_paths.config_dir,
        app_paths.global_dir,
        app_paths.workspaces_dir,
        app_paths.cache_dir,
        app_paths.log_dir,
    )
    config = app_paths.config_file
    sentinels = [config]
    for root in roots[1:]:
        sentinels.append(root / "w18-preservation-sentinel.txt")
    return config, sentinels


def _real_default_windows_config() -> Path:
    """Resolve the real default Windows config path without selecting app state."""

    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ProductVerificationError("APPDATA is unavailable for real-config safety proof")
    return (Path(appdata).resolve() / "local-llm-agent" / "config" / "config.json").resolve()


def _snapshot_file(path: Path, label: str) -> FileSnapshot:
    """Read a file snapshot without creating, replacing, or renaming anything."""

    resolved = path.resolve()
    if path.is_symlink() and not path.exists():
        raise ProductVerificationError(f"{label} is a broken link-like path: {path}")
    if not path.exists():
        return FileSnapshot(resolved, False, None)
    if not path.is_file():
        raise ProductVerificationError(f"{label} exists but is not a regular file: {path}")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise ProductVerificationError(f"{label} could not be read: {path}") from exc
    return FileSnapshot(resolved, True, digest)


def _assert_file_snapshot_unchanged(snapshot: FileSnapshot, label: str) -> None:
    current = _snapshot_file(snapshot.path, label)
    if current.present != snapshot.present or current.sha256 != snapshot.sha256:
        raise ProductVerificationError(
            f"{label} changed during acceptance: {snapshot.path}"
        )


def _write_preservation_state(config: Path, sentinels: Sequence[Path]) -> None:
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        raise ProductVerificationError(f"acceptance refuses to overwrite pre-existing config: {config}")
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "api_url": "http://127.0.0.1:8080/v1/chat/completions",
                "model": "default",
                "default_model_profile": "local_8gb",
                "model_profiles": {
                    "local_8gb": {
                        "provider": "openai_compatible",
                        "base_url": "http://127.0.0.1:8080/v1",
                        "model": "default",
                    }
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    for sentinel in sentinels[1:]:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("W18 preservation evidence\n", encoding="utf-8")


def _assert_preservation(sentinels: Sequence[Path]) -> None:
    for sentinel in sentinels:
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != (
            json.dumps(
                {
                    "schema_version": 1,
                    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
                    "model": "default",
                    "default_model_profile": "local_8gb",
                    "model_profiles": {
                        "local_8gb": {
                            "provider": "openai_compatible",
                            "base_url": "http://127.0.0.1:8080/v1",
                            "model": "default",
                        }
                    },
                },
                sort_keys=True,
            )
            + "\n"
            if sentinel == sentinels[0]
            else "W18 preservation evidence\n"
        ):
            raise ProductVerificationError(f"uninstall did not preserve user state: {sentinel}")


def _prepare_context(bundle_root: Path) -> ProductContext:
    if os.name != "nt":
        raise ProductVerificationError("W18 installed-product acceptance is Windows-only")
    bundle_root = bundle_root.resolve()
    install_script = bundle_root / "install.ps1"
    uninstall_script = bundle_root / "uninstall.ps1"
    if not install_script.is_file() or not uninstall_script.is_file():
        raise ProductVerificationError("bundle must be an extracted release bundle root")
    install_root = _install_root()
    if install_root.exists():
        raise ProductVerificationError(f"acceptance root already exists; refusing to touch it: {install_root}")
    before_user = _registry_path_snapshot()
    before_machine = _machine_path()
    working = Path(tempfile.mkdtemp(prefix="w18-installed-product-"))
    outside = working / "Jose Teste é outside cwd"
    outside.mkdir()
    application_home = _v3_application_home(working, install_root)
    app_paths = AppPaths.discover(app_home=application_home)
    _v3_assert_app_paths(application_home, app_paths)
    config, sentinels = _state_sentinels(app_paths)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "bundle_root": str(bundle_root),
        "install_root": str(install_root),
        "scenarios": {},
    }
    return ProductContext(
        bundle_root=bundle_root,
        install_script=install_script,
        uninstall_script=uninstall_script,
        install_root=install_root,
        before_user=before_user,
        before_machine=before_machine,
        working=working,
        outside=outside,
        config=config,
        sentinels=tuple(sentinels),
        report=report,
    )


def _hostile_environment(context: ProductContext) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "VIRTUAL_ENV": str(ROOT / ".venv"),
            "CONDA_PREFIX": str(ROOT),
            "PYTHONPATH": str(ROOT),
            "PIP_INDEX_URL": "https://invalid.example/simple",
            "PIP_CONFIG_FILE": str(context.working / "hostile-pip.conf"),
            "UV_CONFIG_FILE": str(context.working / "hostile-uv.toml"),
            "UV_PYTHON": str(sys.executable),
            "LLM_AGENT_HOME": str(context.application_home),
        }
    )
    (context.working / "hostile-pip.conf").write_text(
        "index-url = https://invalid.example/simple\n", encoding="utf-8"
    )
    (context.outside / "uv.toml").write_text("python-preference = \"system\"\n", encoding="utf-8")
    (context.outside / ".python-version").write_text("3.10\n", encoding="utf-8")
    return environment


def _run_installer(context: ProductContext, script: Path, environment: Mapping[str, str], label: str) -> None:
    result = _run_ps_file(script, (), cwd=context.outside, environment=environment)
    _require_success(result, label)


def _assert_clean_install(context: ProductContext, environment: Mapping[str, str]) -> InstalledFacts:
    _run_installer(context, context.install_script, environment, "clean W18 install")
    receipt = _load_json(context.install_root / "current-install.json", "current-install receipt")
    candidate_id = str(receipt.get("candidate_id", ""))
    candidate = context.install_root / "versions" / candidate_id
    candidate_python = candidate / "venv" / "Scripts" / "python.exe"
    stable = context.install_root / "bin" / "llm-agent.exe"
    if not candidate_id.startswith("w18-") or not candidate_python.is_file() or not stable.is_file():
        raise ProductVerificationError("receipt/candidate/stable launcher layout is incomplete")
    if _machine_path() != context.before_machine:
        raise ProductVerificationError("Machine PATH changed during W18 install")
    after_install_user = _registry_path_snapshot()
    _assert_owned_path_count(after_install_user.value, str(context.install_root / "bin"), 1)
    context.report["scenarios"]["clean_install"] = {
        "status": "passed",
        "candidate_id": candidate_id,
        "candidate_python": str(candidate_python),
        "stable_launcher": str(stable),
    }
    return InstalledFacts(candidate_id, candidate_python, stable, after_install_user)


def _verify_fresh_shell(context: ProductContext, facts: InstalledFacts) -> None:
    context.report["scenarios"]["fresh_shell"] = _fresh_shell(
        install_root=context.install_root,
        candidate_python=facts.candidate_python,
        forbidden_path=ROOT,
        machine_path=context.before_machine,
        user_path=facts.after_install_user,
        cwd=context.outside,
        probe_home=context.working / "fresh-home",
    )


def _verify_same_candidate_reinstall(
    context: ProductContext, environment: Mapping[str, str]
) -> tuple[bytes, RegistryPathSnapshot]:
    receipt_path = context.install_root / "current-install.json"
    receipt_before = receipt_path.read_bytes()
    path_before = _registry_path_snapshot()
    _run_installer(context, context.install_script, environment, "same-candidate reinstall")
    if receipt_path.read_bytes() != receipt_before:
        raise ProductVerificationError("same-candidate reinstall rewrote committed receipt")
    path_after = _registry_path_snapshot()
    if path_after.value != path_before.value or path_after.kind != path_before.kind:
        raise ProductVerificationError("same-candidate reinstall changed raw User PATH")
    context.report["scenarios"]["same_candidate_reinstall"] = {"status": "passed", "idempotent": True}
    return receipt_before, path_before


def _verify_invalid_upgrade(
    context: ProductContext,
    environment: Mapping[str, str],
    receipt_before: bytes,
    path_before: RegistryPathSnapshot,
) -> None:
    with tempfile.TemporaryDirectory(prefix="w18-invalid-bundle-") as invalid_raw:
        invalid_bundle = _make_invalid_bundle(context.bundle_root, Path(invalid_raw) / "bundle")
        invalid_result = _run_ps_file(
            invalid_bundle / "install.ps1",
            (),
            cwd=context.outside,
            environment=environment,
        )
        if invalid_result.returncode == 0:
            raise ProductVerificationError("invalid B bundle unexpectedly installed")
    receipt_path = context.install_root / "current-install.json"
    if receipt_path.read_bytes() != receipt_before:
        raise ProductVerificationError("invalid B changed the active receipt")
    current_path = _registry_path_snapshot()
    if current_path.value != path_before.value or current_path.kind != path_before.kind:
        raise ProductVerificationError("invalid B changed raw User PATH")
    context.report["scenarios"]["invalid_upgrade_before_promotion"] = {"status": "passed"}


def _assert_uninstalled(context: ProductContext) -> None:
    after_uninstall = _registry_path_snapshot()
    if after_uninstall.value != context.before_user.value or after_uninstall.kind != context.before_user.kind:
        raise ProductVerificationError("uninstall did not restore unrelated raw User PATH representation")
    if _machine_path() != context.before_machine:
        raise ProductVerificationError("Machine PATH changed during uninstall")
    if (context.install_root / "bin" / "llm-agent.exe").exists() or context.install_root.exists():
        raise ProductVerificationError("uninstall left W18 install runtime or stable launcher")
    _assert_preservation(context.sentinels)


def _verify_uninstall_preservation_and_reinstall(
    context: ProductContext, environment: Mapping[str, str]
) -> None:
    _write_preservation_state(context.config, context.sentinels)
    _run_installer(context, context.uninstall_script, environment, "uninstall")
    _assert_uninstalled(context)
    context.report["scenarios"]["uninstall_preservation"] = {
        "status": "passed",
        "sentinels": [str(path) for path in context.sentinels],
    }
    _run_installer(context, context.install_script, environment, "reinstall after uninstall")
    post_reinstall = _registry_path_snapshot()
    _assert_owned_path_count(post_reinstall.value, str(context.install_root / "bin"), 1)
    context.report["scenarios"]["reinstall_after_uninstall"] = {
        "status": "passed",
        "preserved_config": context.config.is_file(),
    }
    _run_installer(context, context.uninstall_script, environment, "final uninstall")
    _assert_preservation(context.sentinels)
    context.report["scenarios"]["final_cleanup"] = {"status": "passed"}


def verify_installed_product(bundle_root: Path, *, keep: bool = False) -> dict[str, Any]:
    context = _prepare_context(bundle_root)
    installed = False
    try:
        hostile_environment = _hostile_environment(context)
        facts = _assert_clean_install(context, hostile_environment)
        installed = True
        _verify_fresh_shell(context, facts)
        receipt_before, path_before = _verify_same_candidate_reinstall(context, hostile_environment)
        _verify_invalid_upgrade(context, hostile_environment, receipt_before, path_before)
        _verify_uninstall_preservation_and_reinstall(context, hostile_environment)
        installed = False
        context.report["status"] = "passed"
        return context.report
    finally:
        if installed and not keep:
            try:
                cleanup_environment = _clean_child_environment(
                    reconstructed_persistent_path(_machine_path(), _registry_path_snapshot().semantic(), os.environ)
                )
                _run_ps_file(
                    context.uninstall_script,
                    (),
                    cwd=context.outside,
                    environment=cleanup_environment,
                )
            except Exception as exc:
                context.report["cleanup_error"] = str(exc)
        if not keep:
            shutil.rmtree(context.working, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True, help="extracted W18 release bundle root")
    parser.add_argument("--keep", action="store_true", help="keep disposable acceptance state for diagnosis")
    parser.add_argument("--summary-json", type=Path, help="write bounded JSON evidence")
    args = parser.parse_args(argv)
    destination = args.summary_json
    if os.name != "nt":
        result = {
            "schema_version": 1,
            "status": "skipped",
            "reason": "W18 installed-product acceptance is Windows-only",
        }
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("W18_INSTALLED_PRODUCT=SKIP (Windows only)")
        return 0
    try:
        result = verify_installed_product(args.bundle_dir, keep=args.keep)
    except ProductVerificationError as exc:
        result = {"schema_version": 1, "status": "failed", "error": str(exc)}
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"W18 installed-product verification failed: {exc}", file=sys.stderr)
        return 1
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("W18_INSTALLED_PRODUCT=PASS")
    return 0


if False and __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# W18 v003 acceptance implementation
# ---------------------------------------------------------------------------

def _v3_clean_child_environment(
    path_value: str,
    application_home: Path,
) -> dict[str, str]:
    """Build a clean child environment with an explicit application authority."""

    scrub = {
        "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE",
        "PIP_CONFIG_FILE", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST",
        "PIP_FIND_LINKS", "PIP_NO_INDEX", "UV_CONFIG_FILE", "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR",
        "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "UV_INSECURE_HOST",
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "LLM_AGENT_HOME",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    }
    checkout = str(ROOT).casefold()
    environment: dict[str, str] = {}
    for name, value in os.environ.items():
        if name.upper() in scrub or name.upper().startswith("W18_"):
            continue
        if checkout in value.casefold() or ".venv" in value.casefold():
            continue
        environment[name] = value
    environment["PATH"] = path_value
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["LLM_AGENT_HOME"] = str(application_home.resolve())
    return environment


def _v3_assert_no_checkout(values: Sequence[str], label: str) -> None:
    needle = str(ROOT).casefold()
    for value in values:
        if needle in str(value).casefold():
            raise ProductVerificationError(f"{label} contains source checkout: {value}")


def _v3_fresh_shell(
    *,
    stable: Path,
    candidate: Path,
    machine_path: str | None,
    user_path: RegistryPathSnapshot,
    cwd: Path,
    application_home: Path,
) -> dict[str, Any]:
    selected_home = application_home.resolve()
    persistent_path = reconstructed_persistent_path(machine_path, user_path.semantic(), os.environ)
    environment = _v3_clean_child_environment(persistent_path, selected_home)
    script = cwd / "w18-v003-fresh-shell.ps1"
    script.write_text(
        r'''param(
    [string]$ExpectedLauncher,
    [string]$CandidateRoot,
    [string]$ProbeHome,
    [string]$ProbeCwd,
    [string]$ForbiddenPath
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProbeCwd
if ($env:PYTHONPATH -or $env:PYTHONHOME -or $env:VIRTUAL_ENV) { throw "hostile Python environment leaked" }
if (-not $env:LLM_AGENT_HOME -or -not ([System.IO.Path]::GetFullPath($env:LLM_AGENT_HOME)).Equals([System.IO.Path]::GetFullPath($ProbeHome), [System.StringComparison]::OrdinalIgnoreCase)) { throw "application home authority was not propagated" }
$command = Get-Command -Name "llm-agent" -CommandType Application -ErrorAction Stop
$resolved = [System.IO.Path]::GetFullPath([string]$command.Source)
if (-not $resolved.Equals([System.IO.Path]::GetFullPath($ExpectedLauncher), [System.StringComparison]::OrdinalIgnoreCase)) { throw "wrong stable launcher: $resolved" }
$version = ((& $resolved --version 2>&1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne "llm-agent 0.2.0rc1") { throw "wrong version: $version" }
$help = ((& $resolved --help 2>&1) | Out-String)
if ($LASTEXITCODE -ne 0 -or $help -notmatch "(?i)usage:") { throw "help failed" }
& $resolved config init --home $ProbeHome | Out-Null
if ($LASTEXITCODE -ne 0) { throw "config init failed" }
$doctorText = ((& $resolved doctor --json --home $ProbeHome --workspace $ProbeCwd 2>&1) | Out-String)
if ($LASTEXITCODE -ne 0) { throw "doctor failed: $doctorText" }
$doctor = $doctorText | ConvertFrom-Json
if ($doctor.readiness.offline_ready -ne $true) { throw "doctor was not offline-ready" }
$candidatePython = [System.IO.Path]::GetFullPath((Join-Path $CandidateRoot "runtime\python.exe"))
$originCode = "import agent,importlib.metadata,json,pathlib,platform,sys; print(json.dumps({'agent':str(pathlib.Path(agent.__file__).resolve()),'exe':str(pathlib.Path(sys.executable).resolve()),'agent_version':agent.__version__,'distribution_version':importlib.metadata.version('local-llm-agent'),'python':platform.python_version(),'path':list(sys.path)}))"
$originText = ((& $candidatePython -c $originCode 2>&1) | Out-String)
if ($LASTEXITCODE -ne 0) { throw "import origin failed: $originText" }
$origin = $originText | ConvertFrom-Json
if ($origin.agent_version -ne "0.2.0rc1" -or $origin.distribution_version -ne "0.2.0rc1" -or $origin.python -ne "3.12.14") { throw "runtime identity failed" }
if (-not ([System.IO.Path]::GetFullPath($origin.exe)).Equals($candidatePython, [System.StringComparison]::OrdinalIgnoreCase)) { throw "host Python executable used" }
if (-not ([System.IO.Path]::GetFullPath($origin.agent)).StartsWith([System.IO.Path]::GetFullPath((Join-Path $CandidateRoot "runtime")) + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "agent import escaped payload" }
if ($originText -match [regex]::Escape($ForbiddenPath) -or ($origin.path -join "`n") -match [regex]::Escape($ForbiddenPath)) { throw "checkout leaked into installed origin" }
[Console]::Out.Write("W18_V003_FRESH_SHELL_PASS")
''',
        encoding="utf-8",
    )
    selected_home.mkdir(parents=True, exist_ok=True)
    result = _run_ps_file(
        script,
        (str(stable), str(candidate), str(selected_home), str(cwd), str(ROOT)),
        cwd=cwd,
        environment=environment,
    )
    _require_success(result, "v003 fresh-shell acceptance")
    if result.stdout.strip() != "W18_V003_FRESH_SHELL_PASS":
        raise ProductVerificationError(f"v003 fresh-shell marker missing: {result.stdout!r}")
    _v3_assert_no_checkout(tuple(environment.values()), "fresh-shell environment")
    return {
        "status": "passed",
        "cwd": str(cwd),
        "path": persistent_path,
        "application_home": str(selected_home),
        "probe_home": str(selected_home),
        "candidate_python": str(candidate / "runtime" / "python.exe"),
    }


def _v3_payload_inventory(candidate: Path) -> dict[str, dict[str, Any]]:
    inventory = _load_json(candidate / "payload-files.json", "payload inventory")
    try:
        validated = validate_inventory(inventory)
    except PayloadValidationError as exc:
        if str(exc).startswith("forbidden payload path:"):
            raise ProductVerificationError(f"forbidden candidate payload path: {exc}") from exc
        raise ProductVerificationError(f"candidate payload inventory is invalid: {exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    for entry in validated["files"]:
        path = str(entry["path"])
        key = path.casefold()
        if key in result:
            raise ProductVerificationError(f"case-colliding candidate payload path: {path}")
        result[key] = entry
    return result


def _v3_assert_inventory_files(candidate: Path, inventory: Mapping[str, Mapping[str, Any]]) -> None:
    for _key, entry in inventory.items():
        path = candidate / Path(str(entry["path"]).replace("/", os.sep))
        if not path.is_file() or path.is_symlink():
            raise ProductVerificationError(f"candidate payload member missing/link-like: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != int(entry["size"]) or digest != str(entry["sha256"]):
            raise ProductVerificationError(f"candidate payload hash/size mismatch: {path}")


def _v3_expected_prefixes(inventory: Mapping[str, Mapping[str, Any]]) -> set[str]:
    expected_prefixes: set[str] = set()
    for entry in inventory.values():
        parts = str(entry["path"]).casefold().split("/")
        expected_prefixes.update("/".join(parts[:index]) for index in range(1, len(parts)))
    return expected_prefixes


def _v3_assert_candidate_directory(
    path: Path,
    relative: str,
    expected_prefixes: set[str],
) -> None:
    structural_reason = payload_path_leakage_reason(relative, inventoried=True)
    if structural_reason:
        raise ProductVerificationError(f"build/install artifact leaked into candidate: {path} ({structural_reason})")
    if relative.casefold() not in expected_prefixes:
        raise ProductVerificationError(f"candidate contains unexpected member not in payload inventory: {path}")


def _v3_assert_candidate_file(
    path: Path,
    relative: str,
    expected: set[str],
    metadata: set[str],
) -> None:
    if not path.is_file():
        raise ProductVerificationError(f"candidate contains a non-regular member: {path}")
    key = relative.casefold()
    metadata_keys = {item.casefold() for item in metadata}
    if key in metadata_keys:
        if relative not in metadata:
            raise ProductVerificationError(f"candidate metadata has unexpected casing: {path}")
        return
    structural_reason = payload_path_leakage_reason(relative, inventoried=key in expected)
    if structural_reason:
        raise ProductVerificationError(f"build/install artifact leaked into candidate: {path} ({structural_reason})")
    if key not in expected:
        raise ProductVerificationError(f"candidate contains unexpected member not in payload inventory: {path}")


def _v3_assert_candidate_members(
    candidate: Path,
    inventory: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Reject structural build leakage and every non-inventoried candidate file."""

    inventory = inventory if inventory is not None else _v3_payload_inventory(candidate)
    expected = set(inventory)
    metadata = {"payload-files.json", "manifest.json", "acceptance.json"}
    expected_prefixes = _v3_expected_prefixes(inventory)

    for path in sorted(candidate.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise ProductVerificationError(f"candidate contains a symlink: {path}")
        relative = path.relative_to(candidate).as_posix()
        if path.is_dir():
            _v3_assert_candidate_directory(path, relative, expected_prefixes)
            continue
        _v3_assert_candidate_file(path, relative, expected, metadata)


def _v3_assert_candidate_layout(candidate: Path) -> None:
    if not (candidate / "runtime" / "python.exe").is_file():
        raise ProductVerificationError("embedded runtime/python.exe missing")
    if not (candidate / "app" / "launcher.py").is_file() or not (candidate / "bin" / "llm-agent.cmd").is_file():
        raise ProductVerificationError("self-contained candidate launcher layout incomplete")


def _v3_assert_candidate_receipt(candidate: Path, receipt: Mapping[str, Any]) -> None:
    if str(receipt.get("candidate_id", "")) != candidate.name:
        raise ProductVerificationError("receipt/candidate identity mismatch")


def _v3_assert_candidate(candidate: Path, receipt: dict[str, Any]) -> None:
    if not candidate.is_dir():
        raise ProductVerificationError(f"candidate directory missing: {candidate}")
    inventory = _v3_payload_inventory(candidate)
    _v3_assert_inventory_files(candidate, inventory)
    _v3_assert_candidate_members(candidate, inventory)
    _v3_assert_candidate_layout(candidate)
    _v3_assert_candidate_receipt(candidate, receipt)


def _v3_is_reparse_point(path: Path) -> bool:
    """Detect link-like directories before resolving or removing them."""

    if path.is_symlink():
        return True
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError as exc:
        raise ProductVerificationError(f"could not inspect disposable path: {path}") from exc
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _v3_application_home(working: Path, install_root: Path) -> Path:
    """Create and validate the harness-owned application-state authority."""

    working_resolved = working.resolve()
    if not working.is_dir() or _v3_is_reparse_point(working):
        raise ProductVerificationError(f"acceptance working root is not a safe directory: {working}")
    application_home = working / "application-home"
    if application_home.exists() or application_home.is_symlink():
        raise ProductVerificationError(f"disposable application home already exists: {application_home}")
    try:
        application_home.mkdir()
    except OSError as exc:
        raise ProductVerificationError(f"could not create disposable application home: {application_home}") from exc
    if _v3_is_reparse_point(application_home) or not application_home.is_dir():
        raise ProductVerificationError(f"disposable application home is not a safe directory: {application_home}")

    resolved_home = application_home.resolve()
    resolved_install = install_root.resolve()
    if not resolved_home.is_relative_to(working_resolved):
        raise ProductVerificationError(f"disposable application home escaped temp root: {resolved_home}")
    if (
        resolved_home == resolved_install
        or resolved_home.is_relative_to(resolved_install)
        or resolved_install.is_relative_to(resolved_home)
    ):
        raise ProductVerificationError("disposable application home collides with install root")
    if any(application_home.iterdir()):
        raise ProductVerificationError(f"disposable application home has unexpected initial contents: {application_home}")
    return resolved_home


def _v3_assert_app_paths(application_home: Path, app_paths: AppPaths) -> None:
    """Fail closed if the canonical owner resolves any mutable path elsewhere."""

    expected = {
        "home": application_home,
        "config": application_home / "config",
        "global": application_home / "global",
        "workspaces": application_home / "workspaces",
        "cache": application_home / "cache",
        "logs": application_home / "logs",
    }
    actual = {
        "home": app_paths.home_dir,
        "config": app_paths.config_dir,
        "global": app_paths.global_dir,
        "workspaces": app_paths.workspaces_dir,
        "cache": app_paths.cache_dir,
        "logs": app_paths.log_dir,
    }
    for name, path in actual.items():
        if path.resolve() != expected[name].resolve():
            raise ProductVerificationError(f"canonical {name} path escaped application home: {path}")


def _v3_prepare_context(bundle_root: Path) -> ProductContext:
    if os.name != "nt":
        raise ProductVerificationError("W18 installed-product acceptance is Windows-only")
    bundle_root = bundle_root.resolve()
    install_script = bundle_root / "install.ps1"
    uninstall_script = bundle_root / "uninstall.ps1"
    if not install_script.is_file() or not uninstall_script.is_file():
        raise ProductVerificationError("bundle must be an extracted v003 release bundle root")
    release_manifest_path = bundle_root / "release-manifest.json"
    release_manifest = _load_json(release_manifest_path, "release manifest")
    release_candidate_id = str(release_manifest.get("candidate_id", ""))
    release_source = release_manifest.get("source")
    release_source_tree = str(release_source.get("tree", "")) if isinstance(release_source, dict) else ""
    if not re.fullmatch(r"w18-[0-9a-f]{32}", release_candidate_id) or not re.fullmatch(
        r"[0-9a-f]{40}", release_source_tree
    ):
        raise ProductVerificationError("release manifest lacks canonical candidate/source identity")
    install_root = _install_root()
    if install_root.exists():
        raise ProductVerificationError(f"acceptance root already exists; refusing to touch it: {install_root}")
    before_user = _registry_path_snapshot()
    before_machine = _machine_path()
    working = Path(tempfile.mkdtemp(prefix="w18-v003-installed-product-")).resolve()
    outside = working / "José Teste é outside cwd"
    outside.mkdir()
    application_home = _v3_application_home(working, install_root)
    app_paths = AppPaths.discover(app_home=application_home)
    _v3_assert_app_paths(application_home, app_paths)
    config, sentinels = _state_sentinels(app_paths)
    real_user_config = _snapshot_file(
        _real_default_windows_config(),
        "real default Windows config",
    )
    report: dict[str, Any] = {
        "schema_version": 3,
        "status": "failed",
        "bundle_root": str(bundle_root),
        "install_root": str(install_root),
        "application_home": str(application_home),
        "application_home_owner": "verifier-harness",
        "application_paths": {
            "home": str(app_paths.home_dir),
            "config": str(app_paths.config_dir),
            "global": str(app_paths.global_dir),
            "workspaces": str(app_paths.workspaces_dir),
            "cache": str(app_paths.cache_dir),
            "logs": str(app_paths.log_dir),
        },
        "real_user_config": {
            "path": str(real_user_config.path),
            "present_before": real_user_config.present,
            "sha256_before": real_user_config.sha256,
        },
        "architecture": "offline-self-contained",
        "evidence_identity": {
            "candidate_id": release_candidate_id,
            "source_tree": release_source_tree,
            "release_manifest_sha256": hashlib.sha256(release_manifest_path.read_bytes()).hexdigest(),
        },
        "scenarios": {},
    }
    return ProductContext(
        bundle_root=bundle_root,
        install_script=install_script,
        uninstall_script=uninstall_script,
        install_root=install_root,
        before_user=before_user,
        before_machine=before_machine,
        working=working,
        outside=outside,
        config=config,
        sentinels=tuple(sentinels),
        report=report,
        application_home=application_home,
        app_paths=app_paths,
        real_user_config=real_user_config,
        original_user=before_user,
    )


def _v3_hostile_environment(context: ProductContext) -> dict[str, str]:
    if context.application_home is None:
        raise ProductVerificationError("v003 child environment has no application home")
    application_home = context.application_home.resolve()
    environment = dict(os.environ)
    environment.update(
        {
            "VIRTUAL_ENV": str(ROOT / ".venv"),
            "CONDA_PREFIX": str(ROOT),
            "PYTHONPATH": str(ROOT),
            "PYTHONHOME": str(ROOT),
            "PIP_INDEX_URL": "https://invalid.example/simple",
            "PIP_CONFIG_FILE": str(context.working / "hostile-pip.conf"),
            "UV_CONFIG_FILE": str(context.working / "hostile-uv.toml"),
            "UV_PYTHON": str(sys.executable),
            "LLM_AGENT_HOME": str(application_home),
        }
    )
    (context.working / "hostile-pip.conf").write_text("index-url = https://invalid.example/simple\n", encoding="utf-8")
    (context.outside / "uv.toml").write_text("python-preference = 'system'\n", encoding="utf-8")
    (context.outside / ".python-version").write_text("3.10\n", encoding="utf-8")
    return environment


def _v3_run_installer(
    context: ProductContext, script: Path, environment: Mapping[str, str], label: str
) -> CommandResult:
    result = _run_ps_file(script, (), cwd=context.outside, environment=environment)
    return _require_success(result, label)


def _v3_run_w17_interactive(
    context: ProductContext,
    facts: InstalledFacts,
    application_home: Path,
    interactions: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Launch the installed stable .cmd through native ConPTY."""

    from scripts.w18_conpty import (
        ConPtyError,
        bounded_conpty_diagnostics,
        normalize_terminal_text,
        run_conpty,
    )

    stable_text = facts.stable.read_text(encoding="utf-8-sig")
    candidate_launcher = context.install_root / "versions" / facts.candidate_id / "bin" / "llm-agent.cmd"
    candidate_runtime = context.install_root / "versions" / facts.candidate_id / "runtime" / "python.exe"
    candidate_shim = context.install_root / "versions" / facts.candidate_id / "app" / "launcher.py"
    expected_reference = f"..\\versions\\{facts.candidate_id}\\bin\\llm-agent.cmd"
    if expected_reference not in stable_text or not candidate_launcher.is_file() or not candidate_runtime.is_file():
        raise ProductVerificationError("stable launcher does not bind to the installed candidate")
    if not candidate_shim.is_file() or "from agent.interfaces.cli.app import main" not in candidate_shim.read_text(encoding="utf-8"):
        raise ProductVerificationError("installed launcher shim does not reach the canonical W17 CLI entry point")
    persistent_path = reconstructed_persistent_path(
        context.before_machine,
        _registry_path_snapshot().semantic(),
        os.environ,
    )
    environment = _v3_clean_child_environment(persistent_path, application_home)
    _v3_assert_no_checkout(tuple(environment.values()), "W17 interactive environment")
    comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    command = (f'"{comspec}"', "/d", "/s", "/c", f'""{facts.stable}""')
    try:
        result = run_conpty(
            command,
            cwd=context.outside,
            environment=environment,
            interactions=interactions,
        )
    except ConPtyError as exc:
        raise ProductVerificationError(f"installed W17 ConPTY acceptance unavailable: {exc}") from exc
    if result.returncode != 0:
        raise ProductVerificationError(
            f"installed W17 interactive launcher exited {result.returncode}; "
            f"diagnostics={bounded_conpty_diagnostics(result)}"
        )
    return {
        "transcript": result.transcript,
        "normalized_transcript": normalize_terminal_text(result.transcript),
        "returncode": result.returncode,
        "inputs": list(result.inputs),
        "diagnostics": dict(result.diagnostics),
        "raw_transcript_sha256": result.raw_transcript_sha256,
        "output_bytes": result.output_bytes,
    }


def _v3_windows_host_identity() -> dict[str, object]:
    version = sys.getwindowsversion()  # type: ignore[attr-defined]
    return {
        "platform": sys.platform,
        "architecture": platform.machine(),
        "release": platform.release(),
        "version": platform.version(),
        "build": int(version.build),
        "service_pack": version.service_pack,
    }


def _v3_input_sequence_metadata(values: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "ordinal": index,
            "utf8_bytes": len(value.encode("utf-8")),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
        for index, value in enumerate(values, start=1)
    ]


def _v3_verify_w17_interactive(context: ProductContext, facts: InstalledFacts) -> dict[str, Any]:
    """Prove first-run and existing-config ownership through the installed launcher."""

    if context.application_home is None:
        raise ProductVerificationError("W17 interactive acceptance has no verifier application home")
    first_home = context.working / "w17-a57-first-run-home"
    existing_home = context.working / "w17-a58-existing-config-home"
    candidate_root = context.install_root / "versions" / facts.candidate_id
    candidate_launcher = candidate_root / "bin" / "llm-agent.cmd"
    candidate_runtime = candidate_root / "runtime" / "python.exe"
    first_home.mkdir()
    existing_home.mkdir()
    first_run = _v3_run_w17_interactive(
        context,
        facts,
        first_home,
        (
            ("Parece ser o primeiro uso", "y\r"),
            ("Profiles dispon", "\r"),
            ("Modelo [", "\r"),
            ("Endpoint compat", "\r"),
            ("Workspace:", "1\r"),
            ("Digite /help", "/exit\r"),
        ),
    )
    first_visible_transcript = str(first_run["normalized_transcript"])
    first_config = first_home / "config" / "config.json"
    if not first_config.is_file():
        raise ProductVerificationError("A57 did not create the canonical W17 first-run config")
    first_lower = first_visible_transcript.casefold()
    first_markers = {
        "first_run_notice": "parece ser o primeiro uso" in first_lower,
        "w17_configuration_prompt": "profiles dispon" in first_lower and "modelo [" in first_lower,
        "w17_guided_setup_completed": "guiada validada" in first_lower,
        "w17_workspace_chooser": "workspace:" in first_lower,
        "w17_chat_surface": "digite /help" in first_lower,
    }
    if not all(first_markers.values()):
        raise ProductVerificationError(f"A57 W17 first-run markers are incomplete: {first_markers}")
    if any(token in first_lower for token in ("processando", "agente:", "[worker]")):
        raise ProductVerificationError("A57 unexpectedly entered provider/model execution")

    existing_config = existing_home / "config" / "config.json"
    existing_config.parent.mkdir(parents=True)
    shutil.copy2(first_config, existing_config)
    existing_state = existing_home / "state" / "existing-state-marker.txt"
    existing_state.parent.mkdir()
    existing_state.write_text("W17 existing canonical state\n", encoding="utf-8")
    existing_config_before = hashlib.sha256(existing_config.read_bytes()).hexdigest()
    existing_run = _v3_run_w17_interactive(
        context,
        facts,
        existing_home,
        (
            ("Workspace:", "1\r"),
            ("Digite /help", "/exit\r"),
        ),
    )
    existing_visible_transcript = str(existing_run["normalized_transcript"])
    existing_lower = existing_visible_transcript.casefold()
    existing_markers = {
        "existing_config_consumed": "parece ser o primeiro uso" not in existing_lower,
        "w17_workspace_chooser": "workspace:" in existing_lower,
        "w17_chat_surface": "digite /help" in existing_lower,
        "existing_config_unchanged": hashlib.sha256(existing_config.read_bytes()).hexdigest() == existing_config_before,
    }
    if not all(existing_markers.values()):
        raise ProductVerificationError(f"A58 W17 existing-config markers are incomplete: {existing_markers}")
    if any(token in existing_lower for token in ("processando", "agente:", "[worker]")):
        raise ProductVerificationError("A58 unexpectedly entered provider/model execution")

    identity = dict(context.report["evidence_identity"])
    evidence: dict[str, Any] = {
        "schema_version": "W18-W17-INSTALLED-INTERACTIVE-V1",
        "status": "passed",
        "harness": "conpty",
        "windows_host": _v3_windows_host_identity(),
        "evidence_identity": identity,
        "launcher_chain": {
            "stable_launcher": str(facts.stable),
            "stable_launcher_sha256": hashlib.sha256(facts.stable.read_bytes()).hexdigest(),
            "candidate_launcher": str(candidate_launcher),
            "candidate_launcher_sha256": hashlib.sha256(candidate_launcher.read_bytes()).hexdigest(),
            "candidate_runtime": str(candidate_runtime),
            "application_entry_point": "agent.interfaces.cli.app:main",
            "first_run_owner": "agent.interfaces.cli.first_run.recover_first_run_config",
            "workspace_owner": "agent.interfaces.cli.workspace_entry.choose_workspace",
        },
        "cases": {
            "A57": {
                "status": "passed",
                "application_home": str(first_home),
                "markers": first_markers,
                "inputs": ["y", "Enter", "Enter", "Enter", "1", "/exit"],
                "bounded_exit": True,
                "child_exit_code": first_run["returncode"],
                "input_sequence": first_run["inputs"],
                "input_sequence_metadata": _v3_input_sequence_metadata(first_run["inputs"]),
                "normalized_transcript_markers": sorted(key for key, value in first_markers.items() if value),
                "transcript_sha256": first_run["raw_transcript_sha256"],
                "transcript_bytes": first_run["output_bytes"],
                "harness_diagnostics": first_run["diagnostics"],
                "provider_model_server_invocations": 0,
                "network_used": False,
                "normalized_transcript_sha256": hashlib.sha256(first_visible_transcript.encode("utf-8")).hexdigest(),
            },
            "A58": {
                "status": "passed",
                "application_home": str(existing_home),
                "markers": existing_markers,
                "inputs": ["1", "/exit"],
                "bounded_exit": True,
                "child_exit_code": existing_run["returncode"],
                "input_sequence": existing_run["inputs"],
                "input_sequence_metadata": _v3_input_sequence_metadata(existing_run["inputs"]),
                "normalized_transcript_markers": sorted(key for key, value in existing_markers.items() if value),
                "transcript_sha256": existing_run["raw_transcript_sha256"],
                "transcript_bytes": existing_run["output_bytes"],
                "harness_diagnostics": existing_run["diagnostics"],
                "provider_model_server_invocations": 0,
                "network_used": False,
                "normalized_transcript_sha256": hashlib.sha256(existing_visible_transcript.encode("utf-8")).hexdigest(),
            },
        },
        "model_free": True,
        "network_free": True,
    }
    context.report["scenarios"]["A57_first_run_installed_interactive"] = {
        "status": "passed",
        "evidence": "w17_installed_interactive.cases.A57",
        "canonical_behavioral_evidence": True,
    }
    context.report["scenarios"]["A58_existing_config_installed_interactive"] = {
        "status": "passed",
        "evidence": "w17_installed_interactive.cases.A58",
        "canonical_behavioral_evidence": True,
    }
    return evidence


def _v3_clean_install(context: ProductContext, environment: Mapping[str, str]) -> InstalledFacts:
    _v3_run_installer(context, context.install_script, environment, "v003 clean offline install")
    receipt = _load_json(context.install_root / "current-install.json", "v003 current-install receipt")
    candidate_id = str(receipt.get("candidate_id", ""))
    candidate = context.install_root / "versions" / candidate_id
    stable = context.install_root / "bin" / "llm-agent.cmd"
    _v3_assert_candidate(candidate, receipt)
    if not stable.is_file():
        raise ProductVerificationError("v003 stable launcher missing")
    if str(receipt.get("payload", {}).get("path")) != "payload-windows-x64.zip":
        raise ProductVerificationError("v003 receipt does not bind payload archive")
    if str(receipt.get("embedded_python", {}).get("exact_version")) != "3.12.14":
        raise ProductVerificationError("v003 receipt does not bind embedded Python")
    if _machine_path() != context.before_machine:
        raise ProductVerificationError("Machine PATH changed during v003 install")
    after_user = _registry_path_snapshot()
    _assert_owned_path_count(after_user.value, str(context.install_root / "bin"), 1)
    context.report["scenarios"]["clean_offline_install"] = {
        "status": "passed",
        "candidate_id": candidate_id,
        "candidate_python": str(candidate / "runtime" / "python.exe"),
        "stable_launcher": str(stable),
        "payload_inventory": str(candidate / "payload-files.json"),
    }
    return InstalledFacts(candidate_id, candidate / "runtime" / "python.exe", stable, after_user)


def _v3_verify_same_candidate(context: ProductContext, environment: Mapping[str, str]) -> tuple[bytes, RegistryPathSnapshot]:
    receipt_path = context.install_root / "current-install.json"
    receipt_before = receipt_path.read_bytes()
    path_before = _registry_path_snapshot()
    _v3_run_installer(context, context.install_script, environment, "v003 same-candidate reinstall")
    if receipt_path.read_bytes() != receipt_before:
        raise ProductVerificationError("same-candidate reinstall rewrote committed receipt")
    path_after = _registry_path_snapshot()
    if path_after.value != path_before.value or path_after.kind != path_before.kind:
        raise ProductVerificationError("same-candidate reinstall changed raw User PATH")
    context.report["scenarios"]["same_candidate_reinstall"] = {"status": "passed", "idempotent": True}
    return receipt_before, path_before


def _v3_verify_corrupt_journal_no_mutation(
    context: ProductContext,
    environment: Mapping[str, str],
    facts: InstalledFacts,
) -> None:
    receipt_path = context.install_root / "current-install.json"
    journal_path = context.install_root / "transactions" / "current.journal.json"
    receipt_before = receipt_path.read_bytes()
    stable_before = facts.stable.read_bytes()
    path_before = _registry_path_snapshot()
    receipt = _load_json(receipt_path, "v003 current-install receipt")
    external = context.working / "external-journal-authority"
    external.mkdir()
    sentinel = external / "sentinel.bin"
    sentinel_bytes = b"W18-external-authority-must-remain-untouched\x00\xff"
    sentinel.write_bytes(sentinel_bytes)
    transaction_id = "w18-install-20260101000000000-123456789abc"
    prior_kind = path_before.kind_name if path_before.present else None
    journal = {
        "schema_version": 1,
        "operation": "install",
        "transaction_id": transaction_id,
        "state": "PREPARED",
        "install_root": str(context.install_root),
        "prior_install_root_present": True,
        "stable_launcher": str(facts.stable),
        "owned_path": str(context.install_root / "bin"),
        "candidate_id": facts.candidate_id,
        "candidate_path": str(context.install_root / "versions" / facts.candidate_id),
        "candidate_stage": str(external / "candidate-stage"),
        "candidate_backup": "",
        "transaction_root": str(external),
        "payload_sha256": str(receipt["payload"]["sha256"]),
        "payload_inventory_sha256": str(receipt["payload"]["inventory_sha256"]),
        "prior_path": {
            "key_present": path_before.key_present,
            "present": path_before.present,
            "value": path_before.value,
            "kind": prior_kind,
        },
        "prior_launcher_present": True,
        "prior_launcher_sha256": hashlib.sha256(stable_before).hexdigest(),
        "prior_launcher_backup": str(external / "prior-launcher.cmd"),
        "launcher_promoted": False,
        "path_mutated": False,
        "prior_receipt_present": True,
        "prior_receipt_backup": str(external / "prior-receipt.json"),
        "prior_candidate_id": facts.candidate_id,
        "prior_previous_candidate_id": str(receipt.get("previous_candidate_id") or ""),
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        result = _run_ps_file(context.install_script, (), cwd=context.outside, environment=environment)
        if result.returncode == 0:
            raise ProductVerificationError("path-tampered journal was accepted")
        if sentinel.read_bytes() != sentinel_bytes:
            raise ProductVerificationError("corrupt journal recovery mutated an external sentinel")
        if receipt_path.read_bytes() != receipt_before or facts.stable.read_bytes() != stable_before:
            raise ProductVerificationError("corrupt journal recovery mutated installed product files")
        path_after = _registry_path_snapshot()
        if path_after != path_before:
            raise ProductVerificationError("corrupt journal recovery mutated User PATH")
    finally:
        journal_path.unlink(missing_ok=True)
    context.report["scenarios"]["corrupt_journal_no_mutation"] = {
        "status": "passed",
        "external_sentinel_unchanged": True,
    }


def _v3_invalid_bundle(bundle_root: Path, destination: Path) -> Path:
    shutil.copytree(bundle_root, destination)
    path = destination / "release-manifest.json"
    document = _load_json(path, "v003 release manifest")
    document["payload"]["sha256"] = "0" * 64
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _v3_valid_fixture_bundle(bundle_root: Path, destination: Path, marker: str) -> Path:
    """Build a distinct, valid test bundle using production identity helpers.

    This is verifier-only: the release bundle is copied, one benign launcher
    comment is added to the payload, then the production inventory renderer
    and candidate-ID function bind every resulting hash before the real
    installer validates the fixture.
    """

    shutil.copytree(bundle_root, destination)
    manifest_path = destination / "release-manifest.json"
    manifest = _load_json(manifest_path, "release manifest")
    payload_archive = destination / str(manifest["payload"]["path"])
    inventory_path = destination / str(manifest["payload"]["inventory"])
    with tempfile.TemporaryDirectory(prefix="w18-v003-fixture-payload-") as raw:
        payload_root = Path(raw) / "payload"
        payload_root.mkdir()
        with zipfile.ZipFile(payload_archive, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = Path(info.filename.replace("/", os.sep))
                target = payload_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        launcher = payload_root / "app" / "launcher.py"
        if not launcher.is_file():
            raise ProductVerificationError("valid fixture payload has no launcher.py")
        launcher.write_bytes(launcher.read_bytes() + f"\n# W18 test fixture {marker}\n".encode("utf-8"))
        inventory = make_inventory(payload_root)
        inventory_path.write_bytes(render_inventory(inventory))
        temporary_archive = payload_archive.with_suffix(".fixture.zip")
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for archive_path in sorted(
                payload_root.rglob("*"), key=lambda item: item.relative_to(payload_root).as_posix()
            ):
                if archive_path.is_file():
                    member_name = archive_path.relative_to(payload_root).as_posix()
                    info = zipfile.ZipInfo(member_name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, archive_path.read_bytes())
        temporary_archive.replace(payload_archive)
    manifest["payload"]["sha256"] = hashlib.sha256(payload_archive.read_bytes()).hexdigest()
    manifest["payload"]["inventory_sha256"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    manifest["candidate_id"] = derive_candidate_id(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _v3_launch_lease_probe(context: ProductContext, facts: InstalledFacts) -> RunningCandidate:
    """Start the installed embedded runtime and wait for its lease marker."""

    candidate = context.install_root / "versions" / facts.candidate_id
    lease_source = candidate / "runtime" / "Lib" / "site-packages" / "agent" / "runtime" / "candidate_lease.py"
    code = (
        "import importlib.util,sys;from pathlib import Path;"
        f"p=Path({str(lease_source)!r});"
        "s=importlib.util.spec_from_file_location('_w18_candidate_lease_probe',p);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        f"lease=m.acquire_runtime_candidate_lease(Path({str(candidate / 'app' / 'launcher.py')!r}));"
        "print('W18_CANDIDATE_LEASE_ACQUIRED', flush=True);"
        "sys.stdin.read();"
        "lease.release() if lease else None"
    )
    environment = _v3_clean_child_environment(
        reconstructed_persistent_path(_machine_path(), _registry_path_snapshot().semantic(), os.environ),
        context.application_home or context.working,
    )
    process = subprocess.Popen(
        [str(candidate / "runtime" / "python.exe"), "-c", code],
        cwd=context.outside,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    marker_values: list[str] = []
    reader = threading.Thread(
        target=lambda: marker_values.append(process.stdout.readline()) if process.stdout is not None else None,
        name="w18-lease-marker-reader",
        daemon=True,
    )
    reader.start()
    reader.join(timeout=30)
    if reader.is_alive():
        process.kill()
        process.wait(timeout=30)
        raise ProductVerificationError("installed candidate lease marker was not emitted within 30 seconds")
    marker = marker_values[0].strip() if marker_values else ""
    if marker != "W18_CANDIDATE_LEASE_ACQUIRED":
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.kill()
        raise ProductVerificationError(f"installed candidate lease was not acquired: {marker} {stderr[-500:]}")
    return RunningCandidate(process, facts.candidate_id)


def _v3_stop_lease_probe(running: RunningCandidate) -> None:
    if running.process.stdin is not None:
        running.process.stdin.close()
    try:
        running.process.wait(timeout=30)
    except subprocess.TimeoutExpired as exc:
        running.process.kill()
        running.process.wait(timeout=30)
        raise ProductVerificationError("candidate lease probe did not terminate cleanly") from exc


def _v3_tree_state(path: Path) -> dict[str, str]:
    """Return a bounded state map that also records empty directories."""

    if path.is_symlink():
        raise ProductVerificationError(f"cannot snapshot link-like tree: {path}")
    if not path.exists():
        return {}
    state: dict[str, str] = {".": "<directory>"} if path.is_dir() else {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    }
    if not path.is_dir():
        return state
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix().casefold()):
        relative = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise ProductVerificationError(f"cannot snapshot link-like tree member: {child}")
        if child.is_dir():
            state[relative] = "<directory>"
        elif child.is_file():
            state[relative] = hashlib.sha256(child.read_bytes()).hexdigest()
        else:
            raise ProductVerificationError(f"cannot snapshot non-regular tree member: {child}")
    return state


def _v3_snapshot_active_uninstall(context: ProductContext) -> ActiveUninstallSnapshot:
    receipt_path = context.install_root / "current-install.json"
    stable = context.install_root / "bin" / "llm-agent.cmd"
    versions = context.install_root / "versions"
    transactions = context.install_root / "transactions"
    candidate_digests = {
        child.name: _v3_tree_digest(child)
        for child in sorted(versions.iterdir(), key=lambda item: item.name.casefold())
        if child.is_dir()
    }
    receipt = _load_json(receipt_path, "active-uninstall receipt")
    candidate_id = str(receipt.get("candidate_id", ""))
    candidate_python = versions / candidate_id / "runtime" / "python.exe"
    if not candidate_python.is_file():
        raise ProductVerificationError("active-uninstall receipt has no installed candidate runtime")
    user_path = _registry_path_snapshot()
    return ActiveUninstallSnapshot(
        receipt_path=receipt_path,
        stable=stable,
        versions=versions,
        transactions=transactions,
        receipt_bytes=receipt_path.read_bytes(),
        stable_bytes=stable.read_bytes(),
        candidate_digests=candidate_digests,
        transaction_state=_v3_tree_state(transactions),
        user_path=user_path,
        machine_path=_machine_path(),
        candidate_id=candidate_id,
        facts=InstalledFacts(candidate_id, candidate_python, stable, user_path),
    )


def _v3_assert_candidate_trees_unchanged(snapshot: ActiveUninstallSnapshot) -> None:
    for name, digest in snapshot.candidate_digests.items():
        candidate = snapshot.versions / name
        if not candidate.is_dir() or _v3_tree_digest(candidate) != digest:
            raise ProductVerificationError(f"active-uninstall failure changed candidate tree {name}")


def _v3_assert_blocked_uninstall(snapshot: ActiveUninstallSnapshot, blocked: CommandResult) -> str:
    if blocked.returncode == 0:
        raise ProductVerificationError("uninstall succeeded while the installed candidate lease was live")
    blocked_output = blocked.stdout + "\n" + blocked.stderr
    blocked_marker = "uninstall bloqueado: candidato ativo"
    if blocked_marker not in " ".join(blocked_output.split()):
        raise ProductVerificationError(
            "active-uninstall failure did not identify the live candidate lease: " + blocked_output[-3000:]
        )
    if snapshot.receipt_path.read_bytes() != snapshot.receipt_bytes:
        raise ProductVerificationError("active-uninstall failure changed the receipt")
    if snapshot.stable.read_bytes() != snapshot.stable_bytes:
        raise ProductVerificationError("active-uninstall failure changed the stable launcher")
    _v3_assert_candidate_trees_unchanged(snapshot)
    if _registry_path_snapshot() != snapshot.user_path:
        raise ProductVerificationError("active-uninstall failure changed User PATH")
    if _machine_path() != snapshot.machine_path:
        raise ProductVerificationError("active-uninstall failure changed Machine PATH")
    if _v3_tree_state(snapshot.transactions) != snapshot.transaction_state:
        raise ProductVerificationError("active-uninstall failure progressed a destructive transaction")
    return blocked_marker


def _v3_record_blocked_uninstall(
    context: ProductContext,
    snapshot: ActiveUninstallSnapshot,
    blocked: CommandResult,
    blocked_marker: str,
) -> None:
    context.report["scenarios"]["active_candidate_uninstall_fail_closed"] = {
        "status": "passed",
        "live_candidate_id": snapshot.candidate_id,
        "blocked_exit_code": blocked.returncode,
        "blocked_marker": blocked_marker,
        "receipt_unchanged": True,
        "stable_launcher_unchanged": True,
        "candidate_trees_unchanged": True,
        "user_path_unchanged": True,
        "machine_path_unchanged": True,
        "transaction_state_unchanged": True,
    }


def _v3_restore_after_active_uninstall(context: ProductContext, environment: Mapping[str, str]) -> None:
    _v3_run_installer(context, context.uninstall_script, environment, "active-uninstall after lease release")
    if context.install_root.exists():
        raise ProductVerificationError("uninstall remained blocked after the live candidate lease closed")
    _v3_run_installer(context, context.install_script, environment, "restore after active-uninstall acceptance")
    restored = _load_json(context.install_root / "current-install.json", "restored receipt")
    expected_candidate = str(context.report["evidence_identity"]["candidate_id"])
    if restored.get("candidate_id") != expected_candidate:
        raise ProductVerificationError("active-uninstall acceptance did not restore the installed candidate")


def _v3_verify_active_candidate_uninstall(
    context: ProductContext,
    environment: Mapping[str, str],
) -> None:
    """Exercise real uninstall fail-closed behavior against a live lease."""

    snapshot = _v3_snapshot_active_uninstall(context)
    running = _v3_launch_lease_probe(context, snapshot.facts)
    try:
        blocked = _run_ps_file(context.uninstall_script, (), cwd=context.outside, environment=environment)
        marker = _v3_assert_blocked_uninstall(snapshot, blocked)
        _v3_record_blocked_uninstall(context, snapshot, blocked, marker)
    finally:
        _v3_stop_lease_probe(running)

    _v3_restore_after_active_uninstall(context, environment)


def _v3_assert_current_previous(
    context: ProductContext,
    *,
    current: str,
    previous: str | None,
    label: str,
) -> None:
    receipt = _load_json(context.install_root / "current-install.json", "current-install receipt")
    if receipt.get("candidate_id") != current or (receipt.get("previous_candidate_id") or None) != previous:
        raise ProductVerificationError(f"{label} has wrong current/previous receipt identity")
    current_path = context.install_root / "versions" / current
    if not current_path.is_dir() or (previous is not None and not (context.install_root / "versions" / previous).is_dir()):
        raise ProductVerificationError(f"{label} lost current/previous candidate tree")
    launcher = (context.install_root / "bin" / "llm-agent.cmd").read_text(encoding="utf-8-sig")
    if current not in launcher:
        raise ProductVerificationError(f"{label} stable launcher does not resolve current candidate")



def _v3_verify_invalid_upgrade(
    context: ProductContext,
    environment: Mapping[str, str],
    receipt_before: bytes,
    path_before: RegistryPathSnapshot,
) -> None:
    with tempfile.TemporaryDirectory(prefix="w18-v003-invalid-bundle-") as raw:
        invalid = _v3_invalid_bundle(context.bundle_root, Path(raw) / "bundle")
        result = _run_ps_file(invalid / "install.ps1", (), cwd=context.outside, environment=environment)
        if result.returncode == 0:
            raise ProductVerificationError("invalid v003 bundle unexpectedly installed")
    if (context.install_root / "current-install.json").read_bytes() != receipt_before:
        raise ProductVerificationError("invalid v003 bundle changed current receipt")
    current_path = _registry_path_snapshot()
    if current_path.value != path_before.value or current_path.kind != path_before.kind:
        raise ProductVerificationError("invalid v003 bundle changed raw User PATH")
    receipt = _load_json(context.install_root / "current-install.json", "receipt after invalid upgrade")
    current = str(receipt.get("candidate_id", ""))
    stable = context.install_root / "bin" / "llm-agent.cmd"
    if current not in stable.read_text(encoding="utf-8-sig"):
        raise ProductVerificationError("invalid B changed stable launcher authority")
    if not (context.install_root / "versions" / current).is_dir():
        raise ProductVerificationError("invalid B removed the active candidate")
    _v3_fresh_shell(
        stable=stable,
        candidate=context.install_root / "versions" / current,
        machine_path=context.before_machine,
        user_path=current_path,
        cwd=context.outside,
        application_home=context.application_home or context.working,
    )
    context.report["scenarios"]["A46_invalid_pre_promotion"] = {
        "status": "passed",
        "receipt_still_a": True,
        "stable_launcher_executes_a": True,
        "path_unchanged": True,
        "partial_b_authoritative": False,
    }


def _v3_tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix().casefold()):
        if child.is_file():
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _v3_faulted_install(
    context: ProductContext,
    bundle: Path,
    seam: str,
    label: str,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    from scripts.installer_fault_instrumentation import instrument_installer

    instrumented_root = context.working / f"instrumented-{seam.casefold()}"
    shutil.copytree(bundle, instrumented_root)
    instrument_installer(
        instrumented_root / "install.ps1",
        instrumented_root / "install.ps1.tmp",
        seam,
        hard_stop=True,
    ).replace(instrumented_root / "install.ps1")
    result = _run_ps_file(instrumented_root / "install.ps1", (), cwd=context.outside, environment=environment)
    return _v3_validate_fault_result(result, seam, label)


def _v3_validate_fault_result(result: CommandResult, seam: str, label: str) -> dict[str, Any]:
    """Require a hard-stop result to prove the requested seam was reached."""

    normalized_seam = seam.upper()
    expected = _FAULT_SEAM_MARKERS.get(normalized_seam)
    if expected is None:
        raise ProductVerificationError(f"{label} uses an unsupported fault seam: {seam}")
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 191:
        raise ProductVerificationError(
            f"{label} did not terminate with the deterministic fault code 191: {result.returncode}"
        )
    if output.count(expected) != 1:
        raise ProductVerificationError(f"{label} did not prove the exact seam marker: {expected}")
    wrong_markers = [marker for marker in _FAULT_SEAM_MARKERS.values() if marker != expected and marker in output]
    if wrong_markers:
        raise ProductVerificationError(f"{label} reported the wrong seam marker: {wrong_markers}")
    return {"fault_exit_code": 191, "fault_reached_marker": expected}


def _v3_observe_recovery(
    context: ProductContext,
    bundle: Path,
    environment: Mapping[str, str],
    *,
    expected_candidate: str,
    expected_path: RegistryPathSnapshot,
    label: str,
) -> None:
    from scripts.installer_fault_instrumentation import instrument_installer

    observer = context.working / f"recovery-observer-{label}"
    shutil.copytree(bundle, observer)
    instrument_installer(observer / "install.ps1", observer / "install.ps1.tmp", "RECOVERY", hard_stop=True).replace(
        observer / "install.ps1"
    )
    result = _run_ps_file(observer / "install.ps1", (), cwd=context.outside, environment=environment)
    if result.returncode == 0:
        raise ProductVerificationError(f"{label} recovery observer did not stop after recovery")
    receipt = _load_json(context.install_root / "current-install.json", "recovered receipt")
    if receipt.get("candidate_id") != expected_candidate:
        raise ProductVerificationError(f"{label} recovery did not restore the prior receipt")
    stable = context.install_root / "bin" / "llm-agent.cmd"
    if expected_candidate not in stable.read_text(encoding="utf-8-sig"):
        raise ProductVerificationError(f"{label} recovery did not restore the stable launcher")
    if not (context.install_root / "versions" / expected_candidate).is_dir():
        raise ProductVerificationError(f"{label} recovery removed the prior candidate")
    if _registry_path_snapshot() != expected_path:
        raise ProductVerificationError(f"{label} recovery changed User PATH")
    journal = context.install_root / "transactions" / "current.journal.json"
    if journal.exists():
        raise ProductVerificationError(f"{label} recovery observer left a transaction journal")
    context.report["scenarios"][f"{label}_recovery_observation"] = {
        "status": "passed",
        "receipt_candidate": expected_candidate,
        "stable_launcher_restored": True,
        "path_restored": True,
        "journal_clean": True,
    }


def _v3_verify_active_stale_sequence(
    context: ProductContext,
    environment: Mapping[str, str],
    facts: InstalledFacts,
    bundle_b: Path,
    bundle_c: Path,
    candidate_a: str,
    candidate_b: str,
    candidate_c: str,
) -> None:
    candidate_a_path = context.install_root / "versions" / candidate_a
    a_active_before = _v3_tree_digest(candidate_a_path)
    active_a1 = _v3_launch_lease_probe(context, facts)
    active_a2: RunningCandidate | None = None
    try:
        active_a2 = _v3_launch_lease_probe(context, facts)
        _v3_run_installer(context, bundle_b / "install.ps1", environment, "lease sequence A to B")
        cleanup_active = _v3_run_installer(
            context, bundle_c / "install.ps1", environment, "lease sequence B to C"
        )
        active_marker = f"W18_CLEANUP_ACTIVE_PRESERVED candidate_id={candidate_a}"
        if active_marker not in cleanup_active.stdout + cleanup_active.stderr:
            raise ProductVerificationError("A49 cleanup did not explicitly classify stale A as active")
        _v3_assert_current_previous(context, current=candidate_c, previous=candidate_b, label="A49 A-B-C sequence")
        if not candidate_a_path.is_dir() or _v3_tree_digest(candidate_a_path) != a_active_before:
            raise ProductVerificationError("active stale A was deleted or changed")
        _v3_stop_lease_probe(active_a1)
        cleanup_one_reference = _v3_run_installer(
            context, bundle_c / "install.ps1", environment, "cleanup with second A reference active"
        )
        if active_marker not in cleanup_one_reference.stdout + cleanup_one_reference.stderr:
            raise ProductVerificationError("A49 cleanup lost active classification after first A process exited")
        if not candidate_a_path.is_dir():
            raise ProductVerificationError("A49 removed stale A while the second process retained an active handle")
        context.report["scenarios"]["A49_active_stale_candidate"] = {
            "status": "passed",
            "sequence": [candidate_a, candidate_b, candidate_c],
            "active_candidate": candidate_a,
            "stale_candidate_preserved": True,
            "cleanup_active_marker": active_marker,
            "cleanup_encountered_stale_a": True,
            "simultaneous_processes": 2,
            "second_process_preserved_after_first_exit": True,
        }
    finally:
        if active_a1.process.poll() is None:
            _v3_stop_lease_probe(active_a1)
        if active_a2 is not None:
            _v3_stop_lease_probe(active_a2)
    cleanup_inactive = _v3_run_installer(
        context, bundle_c / "install.ps1", environment, "cleanup inactive stale A"
    )
    inactive_marker = f"W18_CLEANUP_INACTIVE_ELIGIBLE candidate_id={candidate_a}"
    if inactive_marker not in cleanup_inactive.stdout + cleanup_inactive.stderr:
        raise ProductVerificationError("A49 cleanup did not explicitly classify stale A as inactive")
    if candidate_a_path.exists():
        raise ProductVerificationError("inactive stale A was not removed after lease release")
    _v3_assert_current_previous(context, current=candidate_c, previous=candidate_b, label="A49 cleanup result")
    context.report["scenarios"]["A49_inactive_stale_cleanup"] = {
        "status": "passed",
        "stale_a_removed_after_release": True,
        "previous_b_preserved": True,
        "current_c_preserved": True,
        "cleanup_inactive_marker": inactive_marker,
        "last_handle_closed_before_removal": True,
    }


def _v3_verify_corrective_lifecycle(
    context: ProductContext,
    environment: Mapping[str, str],
    facts: InstalledFacts,
    path_before: RegistryPathSnapshot,
) -> None:
    """Drive the real installer through C2 A45/A47/A48/A49/A50 behaviour."""

    fixtures = context.working / "valid-fixtures"
    fixtures.mkdir()
    bundle_b = _v3_valid_fixture_bundle(context.bundle_root, fixtures / "B", "B")
    bundle_c = _v3_valid_fixture_bundle(context.bundle_root, fixtures / "C", "C")
    manifest_b = _load_json(bundle_b / "release-manifest.json", "valid B manifest")
    manifest_c = _load_json(bundle_c / "release-manifest.json", "valid C manifest")
    candidate_a = facts.candidate_id
    candidate_b = str(manifest_b["candidate_id"])
    candidate_c = str(manifest_c["candidate_id"])
    if len({candidate_a, candidate_b, candidate_c}) != 3:
        raise ProductVerificationError("A/B/C fixtures are not distinct candidate identities")
    context.report["valid_candidate_fixtures"] = {
        "status": "passed",
        "A": candidate_a,
        "B": candidate_b,
        "C": candidate_c,
        "identity_derivation": "distribution.release_manifest.candidate_id",
        "normal_installer_validation": True,
    }

    candidate_a_path = context.install_root / "versions" / candidate_a
    a_before = _v3_tree_digest(candidate_a_path)
    a47_fault = _v3_faulted_install(context, bundle_b, "A47", "A47 post-promotion fault", environment)
    _v3_observe_recovery(
        context,
        bundle_b,
        environment,
        expected_candidate=candidate_a,
        expected_path=path_before,
        label="A47",
    )
    _v3_run_installer(context, bundle_b / "install.ps1", environment, "valid A to B upgrade")
    _v3_assert_current_previous(context, current=candidate_b, previous=candidate_a, label="A45 upgrade")
    if _v3_tree_digest(candidate_a_path) != a_before:
        raise ProductVerificationError("A45 changed previous candidate A")
    _v3_fresh_shell(
        stable=context.install_root / "bin" / "llm-agent.cmd",
        candidate=context.install_root / "versions" / candidate_b,
        machine_path=context.before_machine,
        user_path=_registry_path_snapshot(),
        cwd=context.outside,
        application_home=context.application_home or context.working,
    )
    context.report["scenarios"]["A45_valid_A_to_B"] = {
        "status": "passed",
        "current": candidate_b,
        "previous": candidate_a,
        "previous_known_good": True,
        "stable_launcher_executes": True,
        "path_unchanged": _registry_path_snapshot().value == path_before.value,
    }
    context.report["scenarios"]["A50_previous_known_good"] = {
        "status": "passed",
        "current": candidate_b,
        "previous": candidate_a,
        "candidate_a_intact": True,
    }

    # Re-enter a valid A state before the post-PATH fault, then observe the
    # real recovery transaction before any successful replacement starts.
    _v3_run_installer(context, context.bundle_root / "install.ps1", environment, "restore valid A baseline")
    path_a = _registry_path_snapshot()
    path_without_owned = remove_owned_segment(path_a.semantic(), str(context.install_root / "bin"), os.environ)
    if not path_without_owned.changed:
        raise ProductVerificationError("A48 fixture could not remove the owned PATH segment")
    _v3_write_user_path_snapshot(
        RegistryPathSnapshot(
            path_a.present,
            path_without_owned.value,
            path_a.kind,
            path_a.kind_name,
            path_a.key_present,
        )
    )
    path_a = _registry_path_snapshot()
    a48_fault = _v3_faulted_install(context, bundle_b, "A48", "A48 post-PATH fault", environment)
    _v3_observe_recovery(
        context,
        bundle_b,
        environment,
        expected_candidate=candidate_a,
        expected_path=path_a,
        label="A48",
    )
    _v3_run_installer(context, bundle_b / "install.ps1", environment, "post-recovery valid B upgrade")
    _v3_assert_current_previous(context, current=candidate_b, previous=candidate_a, label="A48 recovered upgrade")
    context.report["scenarios"]["A47_post_promotion_rollback"] = {
        "status": "passed",
        "fault_after_real_promotion": True,
        "recovery_observed_before_new_install": True,
        **a47_fault,
    }
    context.report["scenarios"]["A48_post_path_rollback"] = {
        "status": "passed",
        "fault_after_real_path_mutation": True,
        "raw_path_restored": True,
        "recovery_observed_before_new_install": True,
        **a48_fault,
    }

    # A must be stale, rather than previous, when cleanup is tested.
    _v3_run_installer(context, context.bundle_root / "install.ps1", environment, "restore A for lease sequence")
    _v3_verify_active_stale_sequence(
        context,
        environment,
        facts,
        bundle_b,
        bundle_c,
        candidate_a,
        candidate_b,
        candidate_c,
    )



def _v3_assert_uninstalled(context: ProductContext) -> None:
    after = _registry_path_snapshot()
    if after != context.before_user:
        raise ProductVerificationError("v003 uninstall did not restore exact unrelated User PATH")
    if _machine_path() != context.before_machine:
        raise ProductVerificationError("Machine PATH changed during v003 uninstall")
    if context.install_root.exists():
        raise ProductVerificationError("v003 uninstall left W18 install root")
    _assert_preservation(context.sentinels)
    if context.real_user_config is None:
        raise ProductVerificationError("real default Windows config guard was not initialized")
    _assert_file_snapshot_unchanged(context.real_user_config, "real default Windows config")
    context.report["real_user_config"]["verified_after_lifecycle"] = True


def _v3_write_user_path_snapshot(snapshot: RegistryPathSnapshot) -> None:
    if winreg is None:
        raise ProductVerificationError("Windows Registry is unavailable")
    if snapshot.present:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment")
        try:
            kind = winreg.REG_SZ if snapshot.kind_name == "String" else winreg.REG_EXPAND_SZ
            winreg.SetValueEx(key, "Path", 0, kind, str(snapshot.value))
        finally:
            key.Close()
        return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        key = None
    if key is not None:
        try:
            try:
                winreg.DeleteValue(key, "Path")
            except FileNotFoundError:
                pass
        finally:
            key.Close()
    if not snapshot.key_present:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Environment")
        except FileNotFoundError:
            pass


def _v3_assert_receipt_failure_preserved(
    context: ProductContext,
    *,
    label: str,
    replacement: bytes | None,
    stable_bytes: bytes,
    candidate_snapshots: Mapping[str, str],
    path_before: RegistryPathSnapshot,
    machine_before: str | None,
    journal_before: bytes | None,
) -> None:
    receipt_path = context.install_root / "current-install.json"
    stable = context.install_root / "bin" / "llm-agent.cmd"
    journal_path = context.install_root / "transactions" / "current.journal.json"
    if label == "A55_corrupt_receipt" and receipt_path.read_bytes() != replacement:
        raise ProductVerificationError("corrupt receipt was silently repaired")
    if label == "A54_missing_receipt" and receipt_path.exists():
        raise ProductVerificationError("missing receipt fixture was regenerated")
    if stable.read_bytes() != stable_bytes:
        raise ProductVerificationError(f"{label} changed stable launcher")
    if _registry_path_snapshot() != path_before or _machine_path() != machine_before:
        raise ProductVerificationError(f"{label} changed PATH state")
    for name, digest in candidate_snapshots.items():
        candidate = context.install_root / "versions" / name
        if not candidate.is_dir() or _v3_tree_digest(candidate) != digest:
            raise ProductVerificationError(f"{label} changed candidate tree {name}")
    journal_after = journal_path.read_bytes() if journal_path.exists() else None
    if journal_after != journal_before:
        raise ProductVerificationError(f"{label} created or changed a cleanup transaction")


def _v3_verify_receipt_fail_closed(context: ProductContext, environment: Mapping[str, str]) -> None:
    receipt_path = context.install_root / "current-install.json"
    receipt_bytes = receipt_path.read_bytes()
    stable = context.install_root / "bin" / "llm-agent.cmd"
    stable_bytes = stable.read_bytes()
    candidate_snapshots = {
        child.name: _v3_tree_digest(child)
        for child in (context.install_root / "versions").iterdir()
        if child.is_dir()
    }
    path_before = _registry_path_snapshot()
    machine_before = _machine_path()
    journal_path = context.install_root / "transactions" / "current.journal.json"
    journal_before = journal_path.read_bytes() if journal_path.exists() else None
    for label, replacement in (("A54_missing_receipt", None), ("A55_corrupt_receipt", b"{\"corrupt\":true}\n")):
        if replacement is None:
            receipt_path.unlink()
        else:
            receipt_path.write_bytes(replacement)
        result = _run_ps_file(context.uninstall_script, (), cwd=context.outside, environment=environment)
        if result.returncode == 0:
            raise ProductVerificationError(f"{label} uninstall unexpectedly succeeded")
        _v3_assert_receipt_failure_preserved(
            context,
            label=label,
            replacement=replacement,
            stable_bytes=stable_bytes,
            candidate_snapshots=candidate_snapshots,
            path_before=path_before,
            machine_before=machine_before,
            journal_before=journal_before,
        )
        context.report["scenarios"][label] = {
            "status": "passed",
            "receipt_fixture_preserved": True,
            "launcher_unchanged": True,
            "candidate_trees_unchanged": True,
            "user_path_unchanged": True,
            "machine_path_unchanged": True,
            "no_cleanup_transaction": True,
        }
        if replacement is None:
            receipt_path.write_bytes(receipt_bytes)
    if receipt_path.read_bytes() != receipt_bytes:
        receipt_path.write_bytes(receipt_bytes)


def _v3_verify_lifecycle(context: ProductContext, environment: Mapping[str, str]) -> None:
    # Exercise uninstall against raw installed PATH semantics, including
    # neighbors, an expandable variable, and empty-entry preservation.
    owned = str(context.install_root / "bin")
    seed_kind_name = "String" if context.before_user.kind_name == "String" else "ExpandString"
    seeded = RegistryPathSnapshot(
        True,
        "W18-unrelated-before;%LOCALAPPDATA%\\W18-unrelated;;" + owned + ";W18-unrelated-after;",
        winreg.REG_SZ if seed_kind_name == "String" else winreg.REG_EXPAND_SZ,
        seed_kind_name,
        True,
    )
    _v3_write_user_path_snapshot(seeded)
    expected = remove_owned_segment(seeded.semantic(), owned, os.environ)
    if not expected.changed or expected.owned_count != 0:
        raise ProductVerificationError("A53 fixture did not contain exactly removable owned PATH state")
    context.before_user = RegistryPathSnapshot(
        True,
        expected.value,
        seeded.kind,
        seeded.kind_name,
        seeded.key_present,
    )
    _v3_run_installer(context, context.uninstall_script, environment, "v003 uninstall")
    _v3_assert_uninstalled(context)
    context.report["scenarios"]["A53_exact_unrelated_path_preservation"] = {
        "status": "passed",
        "raw_neighbors_preserved": True,
        "percent_variable_preserved": True,
        "empty_entry_preserved": True,
        "machine_path_unchanged": True,
    }
    context.report["scenarios"]["uninstall_preservation"] = {
        "status": "passed",
        "preserved": [str(path) for path in context.sentinels],
    }
    _v3_run_installer(context, context.install_script, environment, "v003 reinstall after uninstall")
    after_reinstall = _registry_path_snapshot()
    _assert_owned_path_count(after_reinstall.value, str(context.install_root / "bin"), 1)
    _v3_run_installer(context, context.uninstall_script, environment, "v003 final uninstall")
    _v3_assert_uninstalled(context)
    context.report["scenarios"]["reinstall_after_uninstall"] = {"status": "passed", "preserved_config": context.config.is_file()}
    context.report["scenarios"]["final_cleanup"] = {"status": "passed"}


def _v3_verify_conpty_layer_matrix(
    context: ProductContext,
    facts: InstalledFacts,
    output: Path,
) -> dict[str, Any]:
    """Probe all ConPTY layers before the installed lifecycle is cleaned up."""

    if context.application_home is None:
        raise ProductVerificationError("ConPTY layer matrix has no verifier application home")
    from scripts.conpty_layer_matrix import (  # noqa: PLC0415
        ConPtyLayerMatrixError,
        build_conpty_layer_matrix,
        write_conpty_layer_matrix,
    )

    candidate_root = context.install_root / "versions" / facts.candidate_id
    probe_home = context.working / "conpty-layer-probe-home"
    first_run_home = context.working / "conpty-layer5-first-run-home"
    persistent_path = reconstructed_persistent_path(
        context.before_machine,
        _registry_path_snapshot().semantic(),
        os.environ,
    )
    environment = _v3_clean_child_environment(persistent_path, probe_home)
    _v3_assert_no_checkout(tuple(environment.values()), "ConPTY layer matrix environment")
    try:
        matrix = build_conpty_layer_matrix(
            candidate_id=facts.candidate_id,
            candidate_root=candidate_root,
            stable_launcher=facts.stable,
            cwd=context.outside,
            environment=environment,
            application_home=probe_home,
            first_run_home=first_run_home,
            evidence_identity=context.report["evidence_identity"],
            raw_capture_path=output.with_suffix(".raw.json"),
        )
        write_conpty_layer_matrix(matrix, output)
    except ConPtyLayerMatrixError as exc:
        raise ProductVerificationError(f"ConPTY layer matrix production failed: {exc}") from exc
    context.report["scenarios"]["conpty_layer_matrix"] = {
        "status": "passed",
        "evidence": "conpty-layer-matrix.json",
        "schema_version": matrix["schema_version"],
    }
    return matrix


def _v3_verify_installed_product(
    bundle_root: Path,
    *,
    keep: bool = False,
    conpty_layer_matrix_json: Path | None = None,
) -> dict[str, Any]:
    if conpty_layer_matrix_json is None:
        raise ProductVerificationError("--conpty-layer-matrix-json is required for the complete W18 verifier")
    context = _v3_prepare_context(bundle_root)
    application_home = context.application_home
    if application_home is None:
        raise ProductVerificationError("v003 context has no application home")
    installed = False
    try:
        hostile = _v3_hostile_environment(context)
        facts = _v3_clean_install(context, hostile)
        installed = True
        _v3_verify_corrupt_journal_no_mutation(context, hostile, facts)
        _write_preservation_state(context.config, context.sentinels)
        context.report["scenarios"]["preservation_state_created"] = {
            "status": "passed",
            "application_home": str(application_home),
            "sentinels": [str(path) for path in context.sentinels],
        }
        context.report["scenarios"]["fresh_shell"] = _v3_fresh_shell(
            stable=facts.stable,
            candidate=context.install_root / "versions" / facts.candidate_id,
            machine_path=context.before_machine,
            user_path=facts.after_install_user,
            cwd=context.outside,
            application_home=application_home,
        )
        receipt_before, path_before = _v3_verify_same_candidate(context, hostile)
        _v3_verify_invalid_upgrade(context, hostile, receipt_before, path_before)
        _v3_verify_corrective_lifecycle(context, hostile, facts, path_before)
        _v3_verify_receipt_fail_closed(context, hostile)
        _v3_verify_active_candidate_uninstall(context, hostile)
        _v3_verify_conpty_layer_matrix(context, facts, conpty_layer_matrix_json)
        # Keep the independent native ConPTY authority check after the C2
        # lifecycle controls so a host-side ConPTY blocker cannot suppress
        # the installed lease/uninstall evidence in the same run.
        context.report["w17_installed_interactive"] = _v3_verify_w17_interactive(context, facts)
        _v3_verify_lifecycle(context, hostile)
        installed = False
        context.report["status"] = "passed"
        return context.report
    finally:
        if installed and not keep:
            try:
                cleanup = _v3_clean_child_environment(
                    reconstructed_persistent_path(_machine_path(), _registry_path_snapshot().semantic(), os.environ),
                    application_home,
                )
                _run_ps_file(context.uninstall_script, (), cwd=context.outside, environment=cleanup)
            except Exception as exc:
                context.report["cleanup_error"] = str(exc)
        if context.original_user is not None:
            try:
                _v3_write_user_path_snapshot(context.original_user)
            except Exception as exc:
                context.report["path_restore_error"] = str(exc)
        if not keep:
            shutil.rmtree(context.working, ignore_errors=True)
        if context.real_user_config is None:
            raise ProductVerificationError("real default Windows config guard was not initialized")
        _assert_file_snapshot_unchanged(context.real_user_config, "real default Windows config")


_V3_PRIVATE_EVIDENCE_KEYS = frozenset(
    {
        "bundle_root",
        "install_root",
        "application_home",
        "application_paths",
        "real_user_config",
        "path",
        "cwd",
        "probe_home",
        "candidate_python",
        "stable_launcher",
        "candidate_launcher",
        "candidate_runtime",
        "candidate_path",
        "working",
        "outside",
        "sentinels",
        "preserved",
        "path_value",
        "machine_path",
        "user_path",
        "receipt_path",
        "transaction_root",
        "environment",
        "environment_dump",
        "env",
        "environment_variables",
        "env_dump",
        "config",
        "config_json",
        "config_content",
        "configuration",
        "user_config",
        "payload_inventory",
        "raw_transcript",
        "transcript",
        "normalized_transcript",
        "decoded_transcript",
        "transcript_tail",
        "inputs",
        "input_sequence",
    }
)
_V3_REDACTED_TEXT_KEYS = frozenset({"error", "cleanup_error", "path_restore_error"})


def _v3_bounded_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if lowered in _V3_PRIVATE_EVIDENCE_KEYS:
                continue
            if lowered in _V3_REDACTED_TEXT_KEYS:
                bounded[key] = "failure detail redacted from upload evidence"
                continue
            bounded[key] = _v3_bounded_value(child)
        return bounded
    if isinstance(value, list):
        return [_v3_bounded_value(child) for child in value]
    return value


def _v3_bounded_report(result: Mapping[str, Any]) -> dict[str, Any]:
    bounded = _v3_bounded_value(result)
    if not isinstance(bounded, dict):  # pragma: no cover - mapping input always yields a dict.
        raise ProductVerificationError("bounded W18 report is not a JSON object")
    return bounded


def _v3_write_json(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _v3_write_failure_outputs(args: argparse.Namespace, result: dict[str, Any]) -> None:
    bounded = _v3_bounded_report(result)
    for name in ("summary_json", "w17_summary_json", "conpty_layer_matrix_json"):
        _v3_write_json(getattr(args, name), bounded)


def _v3_write_success_outputs(args: argparse.Namespace, result: dict[str, Any]) -> None:
    bounded = _v3_bounded_report(result)
    _v3_write_json(args.summary_json, bounded)
    w17_path = args.w17_summary_json
    if w17_path is not None:
        w17_evidence = bounded.get("w17_installed_interactive")
        if not isinstance(w17_evidence, dict):
            raise ProductVerificationError("W17 interactive evidence was not produced")
        _v3_write_json(w17_path, w17_evidence)


def _v3_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True, help="extracted v003 release bundle root")
    parser.add_argument("--keep", action="store_true", help="keep disposable acceptance state for diagnosis")
    parser.add_argument("--summary-json", type=Path, help="write bounded JSON evidence")
    parser.add_argument(
        "--w17-summary-json",
        type=Path,
        help="write the bounded installed W17 interactive acceptance evidence",
    )
    parser.add_argument(
        "--conpty-layer-matrix-json",
        type=Path,
        help="write the separately-bindable native-ConPTY layer matrix while installed",
    )
    args = parser.parse_args(argv)
    if os.name != "nt":
        result = {"schema_version": 3, "status": "skipped", "reason": "W18 installed-product acceptance is Windows-only"}
        _v3_write_failure_outputs(args, result)
        print("W18_INSTALLED_PRODUCT=SKIP (Windows only)")
        return 0
    try:
        result = _v3_verify_installed_product(
            args.bundle_dir,
            keep=args.keep,
            conpty_layer_matrix_json=args.conpty_layer_matrix_json,
        )
    except ProductVerificationError as exc:
        result = {"schema_version": 3, "status": "failed", "error": str(exc)}
        _v3_write_failure_outputs(args, result)
        print(f"W18 v003 installed-product verification failed: {exc}", file=sys.stderr)
        return 1
    _v3_write_success_outputs(args, result)
    print("W18_INSTALLED_PRODUCT=PASS")
    return 0


globals()["verify_installed_product"] = _v3_verify_installed_product
globals()["main"] = _v3_main

if __name__ == "__main__":
    raise SystemExit(_v3_main())
