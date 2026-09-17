"""Build the v003 self-contained Windows payload at release-build time.

The release builder is intentionally the only place that is allowed to use
the development Python, the pinned build tools, a wheelhouse, or network
access.  The resulting payload contains only the embedded CPython runtime,
the application, and its resolved runtime wheels.  It emits the deterministic
``payload-windows-x64.zip`` and ``payload-files.json`` release members.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distribution.lockfiles import LockSummary, validate_bootstrap_lock, validate_runtime_lock  # noqa: E402
from distribution.payload import (  # noqa: E402
    PayloadValidationError,
    archive_member_paths,
    make_inventory,
    payload_path_leakage_reason,
    render_inventory,
    validate_inventory,
)
from distribution.provenance import (  # noqa: E402
    CandidateTree,
    isolated_candidate_tree,
    materialize_candidate_tree,
)
from distribution.release_identity import (  # noqa: E402
    APPLICATION_WHEEL,
    BOOTSTRAP_PIP_LOCK,
    BOOTSTRAP_PIP_VERSION,
    BUNDLE_MEMBERS,
    INSTALL_WRAPPERS,
    PAYLOAD_ARCHIVE,
    PAYLOAD_INVENTORY,
    PYTHON_SOURCE_ARTIFACT_SHA256,
    RELEASE_NOTICES,
    RELEASE_VERSION,
    RUNTIME_LOCK,
    UV_ASSET_URL,
    UV_SHA256,
    UV_SIGNATURE,
    UV_VERSION,
)
from distribution.release_manifest import (  # noqa: E402
    make_manifest,
    render_manifest,
    sha256_bytes,
    sha256_file,
    validate_manifest,
)
from distribution.reproducibility import (  # noqa: E402
    CanonicalReleaseEpoch,
    ReproducibilityError,
    canonicalize_distlib_launchers,
    canonicalize_wheel,
    derive_release_epoch,
    normalize_tree_mtimes,
    reconcile_dist_info_records,
    reproducible_build_environment,
    validate_canonical_zip,
    validate_dist_info_records,
    validate_wheel_records,
    write_canonical_zip,
)


class BuildError(RuntimeError):
    """Raised when a release candidate cannot be made release-ready."""


class _CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser variant that retains the case of entry-point names."""

    def optionxform(self, option: str) -> str:
        return option


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    wheel: Path
    payload: Path
    payload_inventory: Path
    manifest: Path
    release_zip: Path
    inventory: Path
    attestation: Path
    wheel_sha256: str
    payload_sha256: str
    payload_inventory_sha256: str
    manifest_sha256: str
    zip_sha256: str
    runtime_lock_sha256: str
    bootstrap_lock_sha256: str
    candidate_id: str
    source_date_epoch: int
    runtime_lock: LockSummary
    bootstrap_lock: LockSummary
    wheel_members: tuple[str, ...]
    payload_members: tuple[str, ...]
    relocation_probes: tuple[str, ...]
    uv_evidence: Path


@dataclass(frozen=True)
class UvVerification:
    """Evidence produced only after inspecting the supplied uv archive."""

    artifact_name: str
    artifact_sha256: str
    version: str
    asset_url: str
    archive_members: tuple[str, ...]
    signature: Mapping[str, str]

    def manifest_evidence(self) -> dict[str, object]:
        return {
            "version": self.version,
            "asset_url": self.asset_url,
            "sha256": self.artifact_sha256,
            "expected_signature": dict(self.signature),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact_name,
            "asset_url": self.asset_url,
            "version": self.version,
            "sha256": self.artifact_sha256,
            "archive_members": list(self.archive_members),
            "authenticode": dict(self.signature),
            "verification": "artifact_hash_archive_members_version_and_authenticode",
        }


_PIP_VERSION_PATTERN = re.compile(r"^pip\s+(\S+)\s+from\s+", re.IGNORECASE | re.MULTILINE)
_SUBPROCESS_OUTPUT_LIMIT = 4000


def _clean_build_environment(
    epoch: CanonicalReleaseEpoch | None = None,
    *,
    scratch: Path | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    for key in (
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PIP_CONFIG_FILE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "UV_CONFIG_FILE",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_INSECURE_HOST",
        "SOURCE_DATE_EPOCH",
        "PYTHONHASHSEED",
        "TZ",
        "LC_ALL",
        "LANG",
    ):
        environment.pop(key, None)
    # Release-build Python invocations must not add volatile bytecode to the
    # payload while pip assembles its embedded site-packages.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if epoch is not None:
        environment = reproducible_build_environment(epoch, environment)
    if scratch is not None:
        environment["TMP"] = str(scratch)
        environment["TEMP"] = str(scratch)
        # pip's vendored platformdirs needs a deterministic Windows known
        # folder when reproducible_build_environment removes the user's
        # profile variables.  Keep that lookup inside the build scratch tree.
        deterministic_local_appdata = scratch / "localappdata"
        deterministic_local_appdata.mkdir(parents=True, exist_ok=True)
        environment["LOCALAPPDATA"] = str(deterministic_local_appdata)
        environment["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(deterministic_local_appdata)
        deterministic_appdata = scratch / "appdata"
        deterministic_appdata.mkdir(parents=True, exist_ok=True)
        environment["WIN_PD_OVERRIDE_APPDATA"] = str(deterministic_appdata)
        environment["WIN_PD_OVERRIDE_COMMON_APPDATA"] = str(deterministic_appdata)
    return environment


def _format_subprocess_output(stdout: str, stderr: str) -> str:
    streams = [("stdout", stdout), ("stderr", stderr)]
    streams = [(label, value) for label, value in streams if value]
    streams = [(label, value.rstrip("\r\n")) for label, value in streams]
    if not streams:
        return ""
    if len(streams) == 1:
        label, value = streams[0]
        return f"[{label}]\n{value}"[-_SUBPROCESS_OUTPUT_LIMIT:]

    separators = 2 * (len(streams) - 1)
    labels = sum(len(f"[{label}]\n") for label, _ in streams)
    per_stream_limit = max(1, (_SUBPROCESS_OUTPUT_LIMIT - separators - labels) // len(streams))
    sections = [f"[{label}]\n{value[-per_stream_limit:]}" for label, value in streams]
    return "\n\n".join(sections)


def _run(command: Sequence[str], cwd: Path, *, environment: Mapping[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment) if environment is not None else _clean_build_environment(),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        output = _format_subprocess_output(
            getattr(exc, "stdout", "") or "",
            getattr(exc, "stderr", "") or "",
        ) or str(exc)
        raise BuildError(f"command failed: {' '.join(command)}\n{output[-4000:]}") from exc
    return completed.stdout


def _verify_build_driver(python_executable: Path) -> None:
    """Verify the selected interpreter owns the frozen release-build toolchain."""

    pip_output = _run(
        (str(python_executable), "-m", "pip", "--version"),
        cwd=ROOT,
        environment=_clean_build_environment(),
    )
    match = _PIP_VERSION_PATTERN.search(pip_output)
    actual_pip = match.group(1) if match else "unknown"
    if actual_pip != BOOTSTRAP_PIP_VERSION:
        raise BuildError(
            f"selected build interpreter must provide pip {BOOTSTRAP_PIP_VERSION}; found {actual_pip}"
        )
    _run(
        (str(python_executable), "-c", "import setuptools.build_meta"),
        cwd=ROOT,
        environment=_clean_build_environment(),
    )


def _read_uv_authenticode(executable: Path) -> dict[str, str]:
    """Read the frozen local Authenticode evidence from the extracted uv.exe."""

    if os.name != "nt":
        raise BuildError("uv Authenticode verification requires Windows")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise BuildError(f"Windows PowerShell 5.1 is missing for uv verification: {powershell}")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$path=[Environment]::GetEnvironmentVariable('W18_UV_PROBE_PATH'); "
        "$signature=Get-AuthenticodeSignature -LiteralPath $path; "
        "[ordered]@{status=[string]$signature.Status; "
        "signer_subject=[string]$signature.SignerCertificate.Subject; "
        "signer_thumbprint=[string]$signature.SignerCertificate.Thumbprint; "
        "timestamp_subject=[string]$signature.TimeStamperCertificate.Subject; "
        "timestamp_thumbprint=[string]$signature.TimeStamperCertificate.Thumbprint} | ConvertTo-Json -Compress"
    )
    try:
        environment = _clean_build_environment()
        environment["W18_UV_PROBE_PATH"] = str(executable)
        output = _run(
            (str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script),
            cwd=executable.parent,
            environment=environment,
        )
        parsed = json.loads(output)
    except (BuildError, json.JSONDecodeError) as exc:
        raise BuildError(f"could not read uv Authenticode evidence: {executable}") from exc
    if not isinstance(parsed, dict):
        raise BuildError("uv Authenticode evidence is not an object")
    return {key: str(parsed.get(key, "")) for key in ("status", "signer_subject", "signer_thumbprint", "timestamp_subject", "timestamp_thumbprint")}


_EXPECTED_UV_MEMBERS = ("uv.exe", "uvw.exe", "uvx.exe")


def _validate_uv_artifact_file(artifact: Path) -> Path:
    resolved = artifact.resolve()
    expected_name = Path(UV_ASSET_URL).name
    if resolved.name != expected_name:
        raise BuildError(f"uv artifact filename must be {expected_name}; found {resolved.name}")
    if resolved.is_symlink() or not resolved.is_file():
        raise BuildError(f"uv artifact is missing or link-like: {resolved}")
    return resolved


def _validate_uv_hash(actual_hash: str) -> None:
    if actual_hash != UV_SHA256:
        raise BuildError(f"uv artifact SHA-256 mismatch: expected {UV_SHA256}; found {actual_hash}")


def _read_uv_archive_observations(artifact: Path) -> tuple[tuple[str, ...], str, dict[str, str]]:
    try:
        with zipfile.ZipFile(artifact, "r") as archive:
            infos = archive.infolist()
            if any(info.is_dir() for info in infos):
                raise BuildError("uv archive contains directory members")
            if any(((info.external_attr >> 16) & 0o170000) == 0o120000 for info in infos):
                raise BuildError("uv archive contains a symlink member")
            members = tuple(sorted(info.filename for info in infos))
            if members != _EXPECTED_UV_MEMBERS:
                raise BuildError(f"uv archive members differ from the pinned Windows asset: {members}")
            with tempfile.TemporaryDirectory(prefix="w18-uv-verify-") as raw:
                executable = Path(raw) / "uv.exe"
                executable.write_bytes(archive.read("uv.exe"))
                signature = _read_uv_authenticode(executable)
                version_output = _run(
                    (str(executable), "--version"),
                    cwd=Path(raw),
                    environment=_clean_build_environment(scratch=Path(raw)),
                ).strip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f"uv artifact is not a readable ZIP archive: {artifact}") from exc
    return members, version_output, signature


def _validate_uv_version_output(version_output: str) -> None:
    if not version_output.startswith(f"uv {UV_VERSION} ") and version_output != f"uv {UV_VERSION}":
        raise BuildError(f"uv artifact reports the wrong version: {version_output!r}")
    if "x86_64-pc-windows-msvc" not in version_output:
        raise BuildError(f"uv artifact reports the wrong platform: {version_output!r}")


def _validate_uv_signature(signature: Mapping[str, str]) -> dict[str, str]:
    verified = dict(signature)
    for key, expected in UV_SIGNATURE.items():
        if key != "evidence" and verified.get(key) != str(expected):
            raise BuildError(f"uv Authenticode evidence mismatch for {key}")
    verified["evidence"] = str(UV_SIGNATURE["evidence"])
    return verified


def _validate_uv_observations(
    actual_hash: str,
    version_output: str,
    signature: Mapping[str, str],
) -> dict[str, str]:
    _validate_uv_hash(actual_hash)
    _validate_uv_version_output(version_output)
    return _validate_uv_signature(signature)


def _verify_uv_artifact(artifact: Path) -> UvVerification:
    """Verify the exact pinned uv release asset before accepting the build."""

    artifact = _validate_uv_artifact_file(artifact)
    actual_hash = sha256_file(artifact)
    _validate_uv_hash(actual_hash)
    members, version_output, signature = _read_uv_archive_observations(artifact)
    verified_signature = _validate_uv_observations(actual_hash, version_output, signature)
    return UvVerification(
        artifact_name=artifact.name,
        artifact_sha256=actual_hash,
        version=UV_VERSION,
        asset_url=UV_ASSET_URL,
        archive_members=members,
        signature=verified_signature,
    )


def _uv_rejection_case(label: str, action: Callable[[], Any]) -> dict[str, str]:
    try:
        action()
    except BuildError as exc:
        return {"status": "passed", "outcome": "rejected", "detail": str(exc)}
    raise BuildError(f"uv adversarial case was accepted: {label}")


def _uv_adversarial_cases(verification: UvVerification) -> dict[str, dict[str, str]]:
    valid_signature = dict(verification.signature)
    invalid_signature = dict(valid_signature)
    invalid_signature["status"] = "HashMismatch"
    valid_version = f"uv {UV_VERSION} (x86_64-pc-windows-msvc)"
    _validate_uv_observations(verification.artifact_sha256, valid_version, valid_signature)
    return {
        "valid_pinned_artifact": {"status": "passed", "outcome": "accepted"},
        "wrong_hash": _uv_rejection_case("wrong_hash", lambda: _validate_uv_hash("0" * 64)),
        "wrong_version": _uv_rejection_case(
            "wrong_version",
            lambda: _validate_uv_version_output("uv 0.0.0 (x86_64-pc-windows-msvc)"),
        ),
        "wrong_platform": _uv_rejection_case(
            "wrong_platform",
            lambda: _validate_uv_version_output(f"uv {UV_VERSION} (aarch64-unknown-linux-gnu)"),
        ),
        "authenticode": _uv_rejection_case(
            "authenticode",
            lambda: _validate_uv_signature(invalid_signature),
        ),
    }


def _materialize_source_snapshot(snapshot: CandidateTree, target: Path) -> None:
    try:
        materialize_candidate_tree(snapshot, target)
    except (OSError, ValueError) as exc:
        raise BuildError(f"cannot materialize candidate Git tree {snapshot.tree}") from exc
    if not (target / "pyproject.toml").is_file() or not (target / "agent").is_dir():
        raise BuildError("candidate Git tree is missing the PEP 517 source inputs")


def _build_wheel(
    python_executable: Path,
    source_snapshot: Path,
    wheel_dir: Path,
    epoch: CanonicalReleaseEpoch,
) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=False)
    _run(
        (
            str(python_executable),
            "-m",
            "pip",
            "wheel",
            "--isolated",
            "--no-input",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheel_dir),
            str(source_snapshot),
        ),
        cwd=source_snapshot,
        environment=_clean_build_environment(epoch, scratch=source_snapshot.parent),
    )
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].name != APPLICATION_WHEEL:
        names = ", ".join(path.name for path in wheels) or "none"
        raise BuildError(f"expected exactly {APPLICATION_WHEEL}, found {names}")
    try:
        canonicalize_wheel(wheels[0], epoch)
        validate_canonical_zip(wheels[0], epoch)
        with zipfile.ZipFile(wheels[0], "r") as archive:
            validate_wheel_records(archive)
    except (OSError, zipfile.BadZipFile, ReproducibilityError) as exc:
        raise BuildError(f"application wheel is not reproducible: {wheels[0]}") from exc
    return wheels[0]


_WHEEL_DENYLIST_PARTS = {
    ".agent-local",
    ".audit-local",
    ".git",
    ".venv",
    ".test_runtime",
    "tests",
    "installer",
    "transactions",
    "secrets",
}


def _validate_wheel_member(member: str) -> None:
    path = PurePosixPath(member)
    lowered = member.casefold()
    if member.startswith(("/", "\\")) or ".." in path.parts:
        raise BuildError(f"unsafe wheel member: {member}")
    if any(part.casefold() in _WHEEL_DENYLIST_PARTS for part in path.parts):
        raise BuildError(f"denylisted wheel member: {member}")
    if any(token in lowered for token in ("task_contract", "task_spec", "install_transaction", "transaction.journal")):
        raise BuildError(f"denylisted wheel member: {member}")
    if lowered.endswith((".env", ".pem", ".key", ".secret")):
        raise BuildError(f"possible secret in wheel: {member}")


def _validate_wheel(wheel: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(wheel) as archive:
        members = tuple(sorted(archive.namelist()))
        if not members:
            raise BuildError("wheel is empty")
        for member in members:
            _validate_wheel_member(member)
        metadata_names = [name for name in members if name.endswith(".dist-info/METADATA")]
        entrypoint_names = [name for name in members if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entrypoint_names) != 1:
            raise BuildError("wheel metadata or entry points are missing")
        metadata = archive.read(metadata_names[0]).decode("utf-8").splitlines()
        entrypoints = archive.read(entrypoint_names[0]).decode("utf-8")
        if "Name: local-llm-agent" not in metadata or f"Version: {RELEASE_VERSION}" not in metadata:
            raise BuildError("wheel metadata identity/version mismatch")
        if "llm-agent = agent.interfaces.cli.app:main" not in entrypoints:
            raise BuildError("wheel console entry point mismatch")
        if "agent/resources/default_config.json" not in members:
            raise BuildError("required package data is missing from wheel")
    return members


def _safe_tar_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "python":
        raise BuildError(f"unsafe CPython archive member: {name}")
    return PurePosixPath(*path.parts[1:])


def _extract_python_artifact(artifact: Path, runtime_root: Path) -> None:
    if not artifact.is_file():
        raise BuildError(f"CPython source artifact is missing: {artifact}")
    if sha256_file(artifact) != PYTHON_SOURCE_ARTIFACT_SHA256:
        raise BuildError("CPython source artifact SHA-256 differs from the frozen release input")
    runtime_root.mkdir(parents=True, exist_ok=False)
    try:
        archive = tarfile.open(artifact, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise BuildError(f"cannot open CPython source artifact: {artifact}") from exc
    with archive:
        for member in archive.getmembers():
            relative = _safe_tar_path(member.name)
            if not relative.parts:
                continue
            if member.isdir():
                (runtime_root / Path(*relative.parts)).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg():
                raise BuildError(f"CPython artifact contains a non-regular member: {member.name}")
            destination = runtime_root / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise BuildError(f"CPython artifact member cannot be read: {member.name}")
            with source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    if not (runtime_root / "python.exe").is_file():
        raise BuildError("CPython payload does not contain runtime\\python.exe")


def _remove_path(path: Path, removed_files: set[Path] | None = None) -> None:
    if removed_files is not None:
        if path.is_file() or path.is_symlink():
            removed_files.add(path.resolve())
        elif path.is_dir():
            removed_files.update(child.resolve() for child in path.rglob("*") if child.is_file())
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _remove_build_only_runtime_tools(runtime_root: Path) -> frozenset[Path]:
    removed_files: set[Path] = set()
    site_packages = runtime_root / "Lib" / "site-packages"
    for child in tuple(site_packages.iterdir()) if site_packages.is_dir() else ():
        lowered = child.name.casefold()
        if lowered == "pip" or lowered.startswith("pip-") or lowered.startswith("setuptools") or lowered.startswith("wheel"):
            _remove_path(child, removed_files)
        elif lowered == "direct_url.json":
            _remove_path(child, removed_files)
    scripts = runtime_root / "Scripts"
    if scripts.is_dir():
        for child in tuple(scripts.iterdir()):
            if child.name.casefold().startswith(("pip", "easy_install")):
                _remove_path(child, removed_files)
    for direct_url in runtime_root.rglob("direct_url.json"):
        _remove_path(direct_url, removed_files)
    return frozenset(removed_files)


def _recorded_launcher_distributions(site_packages: Path) -> tuple[Path, ...]:
    return tuple(
        dist_info
        for dist_info in sorted(site_packages.glob("*.dist-info"), key=lambda path: path.name.casefold())
        if not dist_info.name.casefold().startswith(("pip-", "setuptools-", "wheel-"))
    )


def _read_console_launcher_metadata(
    dist_info: Path,
) -> tuple[configparser.ConfigParser, list[list[str]]] | None:
    entry_points_path = dist_info / "entry_points.txt"
    record_path = dist_info / "RECORD"
    if not entry_points_path.is_file() or not record_path.is_file():
        return None
    parser = _CasePreservingConfigParser(interpolation=None)
    try:
        parser.read_string(entry_points_path.read_text(encoding="utf-8"))
        with record_path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeDecodeError, configparser.Error, csv.Error) as exc:
        raise BuildError(f"cannot inspect installed console entry points: {dist_info.name}") from exc
    return parser, rows


def _find_recorded_launcher(rows: Sequence[Sequence[str]], executable_name: str) -> tuple[int, str] | None:
    recorded = [
        (index, row[0])
        for index, row in enumerate(rows)
        if len(row) == 3
        and not row[0].endswith("/")
        and PurePosixPath(row[0]).name.casefold() == executable_name
    ]
    return recorded[0] if len(recorded) == 1 else None


def _iter_recorded_console_launcher_group(
    parser: configparser.ConfigParser,
    rows: Sequence[Sequence[str]],
    group: str,
    gui: bool,
) -> tuple[tuple[str, str, bool, int, str], ...]:
    if not parser.has_section(group):
        return ()
    found: list[tuple[str, str, bool, int, str]] = []
    for name, specification in sorted(parser.items(group), key=lambda item: item[0].casefold()):
        recorded = _find_recorded_launcher(rows, f"{name}.exe".casefold())
        if recorded is not None:
            row_index, relative = recorded
            found.append((name, specification, gui, row_index, relative))
    return tuple(found)


def _iter_recorded_console_launchers(
    parser: configparser.ConfigParser,
    rows: Sequence[Sequence[str]],
) -> tuple[tuple[str, str, bool, int, str], ...]:
    launchers: list[tuple[str, str, bool, int, str]] = []
    for group, gui in (("console_scripts", False), ("gui_scripts", True)):
        launchers.extend(_iter_recorded_console_launcher_group(parser, rows, group, gui))
    return tuple(launchers)


def _recorded_launcher_target(
    relative: str,
    site_packages: Path,
    runtime_root: Path,
) -> Path:
    record_member = PurePosixPath(relative)
    if record_member.is_absolute() or "\\" in relative or "\x00" in relative:
        raise BuildError(f"unsafe recorded console launcher path: {relative!r}")
    recorded_target = (site_packages / Path(*record_member.parts)).resolve()
    if recorded_target != runtime_root and runtime_root not in recorded_target.parents:
        raise BuildError(f"recorded console launcher escapes runtime root: {relative!r}")
    return recorded_target


def _materialize_console_launcher(
    maker_type: Any,
    python_executable: Path,
    target: Path,
    name: str,
    specification: str,
    gui: bool,
    relative: str,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    maker = maker_type(None, str(target.parent))
    maker.executable = f'"{python_executable}"'
    maker.variants = {""}
    maker.clobber = False
    maker.set_mode = False
    generated = tuple(Path(path).resolve() for path in maker.make(f"{name} = {specification}", {"gui": gui}))
    if generated != (target,) or not target.is_file():
        raise BuildError(f"failed to materialize recorded console launcher: {relative}")
    return target


def _render_record_rows(record_path: Path, rows: Sequence[Sequence[str]]) -> None:
    rendered = io.StringIO(newline="")
    csv.writer(rendered, lineterminator="\n").writerows(rows)
    record_path.write_text(rendered.getvalue(), encoding="utf-8", newline="")


def _materialize_recorded_console_launchers(python_executable: Path, runtime_root: Path) -> tuple[Path, ...]:
    """Create console launchers that pip records outside a ``--target`` tree."""

    try:
        from pip._vendor.distlib.scripts import ScriptMaker
    except ImportError as exc:
        raise BuildError("build Python does not provide pip's distlib script maker") from exc

    runtime_root = runtime_root.resolve()
    site_packages = runtime_root / "Lib" / "site-packages"
    created: list[Path] = []
    for dist_info in _recorded_launcher_distributions(site_packages):
        metadata = _read_console_launcher_metadata(dist_info)
        if metadata is None:
            continue
        parser, rows = metadata
        record_path = dist_info / "RECORD"
        for name, specification, gui, row_index, relative in _iter_recorded_console_launchers(parser, rows):
            recorded_target = _recorded_launcher_target(relative, site_packages, runtime_root)
            canonical_relative = f"bin/{name}.exe"
            target = (site_packages / canonical_relative).resolve()
            if recorded_target != target and recorded_target.exists():
                raise BuildError(f"non-canonical recorded console launcher already exists: {relative!r}")
            rows[row_index][0] = canonical_relative
            if target.is_file():
                continue
            created.append(
                _materialize_console_launcher(ScriptMaker, python_executable, target, name, specification, gui, relative)
            )
        _render_record_rows(record_path, rows)
    return tuple(created)


def _remove_recorded_console_launchers(runtime_root: Path) -> frozenset[Path]:
    """Remove pip-materialized console launchers that are not runtime inputs.

    The embedded product owns its relative ``bin/llm-agent.cmd`` launcher.  A
    pip-generated launcher is therefore build/install metadata, regardless of
    whether its entry point came from a dependency or the application wheel.
    Restrict removal to launchers proven by each distribution's entry-point
    metadata and RECORD; unrelated executables remain subject to the
    fail-closed canonicalization sweep.
    """

    runtime_root = runtime_root.resolve()
    site_packages = runtime_root / "Lib" / "site-packages"
    removed: set[Path] = set()
    for dist_info in _recorded_launcher_distributions(site_packages):
        metadata = _read_console_launcher_metadata(dist_info)
        if metadata is None:
            continue
        parser, rows = metadata
        for _, _, _, _, relative in _iter_recorded_console_launchers(parser, rows):
            target = _recorded_launcher_target(relative, site_packages, runtime_root)
            if target.is_file() or target.is_symlink():
                _remove_path(target, removed)
    return frozenset(removed)


def _remove_volatile_bytecode(root: Path, removed_files: set[Path] | None = None) -> None:
    """Remove CPython cache files before the payload becomes inventory-bound."""

    paths = sorted(root.rglob("*"), key=lambda path: (len(path.parts), str(path)), reverse=True)
    for path in paths:
        if path.is_file() and path.suffix.casefold() == ".pyc":
            _remove_path(path, removed_files)
    for path in paths:
        if path.name.casefold() == "__pycache__" and path.is_dir():
            _remove_path(path)


def _assert_no_volatile_bytecode(root: Path) -> None:
    for path in root.rglob("*"):
        if path.name.casefold() == "__pycache__" and path.is_dir():
            raise BuildError(f"volatile bytecode cache leaked into payload: {path.relative_to(root)}")
        if path.is_file() and path.suffix.casefold() == ".pyc":
            raise BuildError(f"volatile bytecode leaked into payload: {path.relative_to(root)}")


def _prepare_wheelhouse(
    python_executable: Path,
    source_snapshot: Path,
    runtime_lock: Path,
    wheelhouse: Path | None,
    scratch: Path,
    epoch: CanonicalReleaseEpoch,
) -> Path:
    target = wheelhouse.resolve() if wheelhouse is not None else scratch / "wheelhouse"
    target.mkdir(parents=True, exist_ok=True)
    if wheelhouse is None:
        _run(
            (
                str(python_executable),
                "-m",
                "pip",
                "download",
                "--isolated",
                "--no-input",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--only-binary=:all:",
                "--require-hashes",
                "--index-url",
                "https://pypi.org/simple",
                "--dest",
                str(target),
                "--requirement",
                str(runtime_lock),
            ),
            cwd=source_snapshot,
            environment=_clean_build_environment(epoch, scratch=scratch),
        )
    wheels = sorted(target.glob("*.whl"))
    if not wheels:
        raise BuildError(f"runtime wheelhouse is empty: {target}")
    if any(path.is_symlink() for path in wheels):
        raise BuildError("runtime wheelhouse contains a symlink")
    return target


def _install_runtime_into_payload(
    python_executable: Path,
    source_snapshot: Path,
    runtime_root: Path,
    wheel: Path,
    runtime_lock: Path,
    wheelhouse: Path,
    scratch: Path,
    epoch: CanonicalReleaseEpoch,
) -> None:
    site_packages = runtime_root / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    _run(
        (
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--no-input",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--only-binary=:all:",
            "--target",
            str(site_packages),
            "--upgrade",
            "--requirement",
            str(runtime_lock),
        ),
        cwd=source_snapshot,
        environment=_clean_build_environment(epoch, scratch=scratch),
    )
    app_requirement = scratch / "application-requirement.txt"
    app_requirement.write_text(
        f"local-llm-agent=={RELEASE_VERSION} --hash=sha256:{sha256_file(wheel)}\n",
        encoding="utf-8",
    )
    app_wheelhouse = scratch / "application-wheelhouse"
    app_wheelhouse.mkdir()
    shutil.copy2(wheel, app_wheelhouse / wheel.name)
    _run(
        (
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--no-input",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            "--find-links",
            str(app_wheelhouse),
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "--target",
            str(site_packages),
            "--upgrade",
            "--requirement",
            str(app_requirement),
        ),
        cwd=source_snapshot,
        environment=_clean_build_environment(epoch, scratch=scratch),
    )
    _materialize_recorded_console_launchers(python_executable, runtime_root)
    removed_files = set(_remove_build_only_runtime_tools(runtime_root))
    removed_files.update(_remove_recorded_console_launchers(runtime_root))
    _remove_volatile_bytecode(runtime_root, removed_files)
    try:
        canonicalize_distlib_launchers(runtime_root, epoch)
        reconcile_dist_info_records(runtime_root, removed_files)
        validate_dist_info_records(runtime_root)
    except ReproducibilityError as exc:
        raise BuildError(f"runtime reproducibility canonicalization failed: {exc}") from exc


def _write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _write_payload_shim(payload_root: Path) -> None:
    _write_utf8(
        payload_root / "app" / "launcher.py",
        "import importlib.util\n"
        "from pathlib import Path\n\n"
        "_launcher = Path(__file__).resolve()\n"
        "_candidate = _launcher.parent.parent\n"
        "_lease_source = _candidate / 'runtime' / 'Lib' / 'site-packages' / 'agent' / 'runtime' / 'candidate_lease.py'\n"
        "if not _lease_source.is_file() or _lease_source.resolve().parents[5] != _candidate:\n"
        "    raise RuntimeError('candidate lease bootstrap source is not canonical')\n"
        "_lease_spec = importlib.util.spec_from_file_location('_w18_candidate_lease_bootstrap', _lease_source)\n"
        "if _lease_spec is None or _lease_spec.loader is None:\n"
        "    raise RuntimeError('candidate lease bootstrap loader is unavailable')\n"
        "_lease_module = importlib.util.module_from_spec(_lease_spec)\n"
        "_lease_spec.loader.exec_module(_lease_module)\n"
        "_candidate_lease = _lease_module.acquire_runtime_candidate_lease(_launcher)\n"
        "from agent.interfaces.cli.app import main\n\n"
        "if __name__ == \"__main__\":\n    raise SystemExit(main())\n",
    )
    _write_utf8(
        payload_root / "bin" / "llm-agent.cmd",
        "@echo off\r\n"
        "setlocal\r\n"
        "set \"PYTHONDONTWRITEBYTECODE=1\"\r\n"
        '"%~dp0..\\runtime\\python.exe" "%~dp0..\\app\\launcher.py" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n",
    )


def _assert_required_payload_files(payload_root: Path) -> None:
    required = (Path("runtime/python.exe"), Path("app/launcher.py"), Path("bin/llm-agent.cmd"))
    for relative_path in required:
        if not (payload_root / relative_path).is_file():
            raise BuildError(f"required payload member is missing: {relative_path.as_posix()}")


def _assert_no_build_only_packages(payload_root: Path) -> None:
    site_packages = payload_root / "runtime" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        return
    for child in site_packages.iterdir():
        lowered = child.name.casefold()
        if lowered == "pip" or lowered.startswith(("pip-", "setuptools", "wheel")):
            raise BuildError(f"build-only package leaked into payload: {child.name}")


def _validate_payload_paths(payload_root: Path) -> None:
    for path in payload_root.rglob("*"):
        relative_path = path.relative_to(payload_root).as_posix().casefold()
        parts = relative_path.split("/")
        structural_reason = payload_path_leakage_reason(relative_path, inventoried=True)
        if structural_reason:
            raise BuildError(f"forbidden payload path: {relative_path} ({structural_reason})")
        if any(part in {".agent-local", ".audit-local", ".git", ".venv", "transactions"} for part in parts):
            raise BuildError(f"forbidden payload path: {relative_path}")
        if relative_path.startswith("installer/"):
            raise BuildError(f"forbidden payload path: {relative_path}")
        if relative_path.endswith("/direct_url.json") or relative_path.endswith("/pip.exe"):
            raise BuildError(f"build metadata/tool leaked into payload: {relative_path}")


def _assert_no_build_source_markers(payload_root: Path, source_snapshot: Path, build_scratch: Path) -> None:
    forbidden_bytes = {str(source_snapshot).casefold(), str(build_scratch).casefold()}
    for marker in forbidden_bytes:
        for variant in {marker, marker.replace("\\", "/")}:
            encoded = variant.encode("utf-8")
            if not encoded:
                continue
            for path in payload_root.rglob("*"):
                if path.is_file() and encoded in path.read_bytes().lower():
                    raise BuildError(f"build/source path leaked into payload: {path}")


def _assert_no_artifact_path_markers(artifact: Path, markers: Sequence[Path]) -> None:
    data = artifact.read_bytes().lower()
    for raw_marker in markers:
        marker = str(raw_marker.resolve()).casefold()
        for variant in {marker, marker.replace("\\", "/")}:
            encoded = variant.encode("utf-8")
            if encoded and encoded in data:
                raise BuildError(f"build/output path leaked into artifact: {artifact}")


def _payload_inventory_members(payload_root: Path) -> tuple[str, ...]:
    try:
        inventory = make_inventory(payload_root)
        validate_inventory(inventory, payload_root)
    except PayloadValidationError as exc:
        raise BuildError(f"payload inventory validation failed: {exc}") from exc
    return tuple(str(item["path"]) for item in inventory["files"])


def _validate_payload_contents(payload_root: Path, source_snapshot: Path, build_scratch: Path) -> tuple[str, ...]:
    _assert_required_payload_files(payload_root)
    _assert_no_build_only_packages(payload_root)
    _validate_payload_paths(payload_root)
    _assert_no_build_source_markers(payload_root, source_snapshot, build_scratch)
    _assert_no_volatile_bytecode(payload_root)
    return _payload_inventory_members(payload_root)


def _write_deterministic_zip(
    root: Path,
    destination: Path,
    members: Sequence[str],
    epoch: CanonicalReleaseEpoch,
) -> None:
    names = tuple(sorted(members))
    write_canonical_zip(
        destination,
        ((name, (root / Path(*name.split("/"))).read_bytes()) for name in names),
        epoch,
    )


def _validate_payload_archive(payload_root: Path, archive_path: Path, inventory: Mapping[str, Any]) -> None:
    expected = tuple(str(item["path"]) for item in inventory["files"])
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if any(item.is_dir() for item in entries):
            raise BuildError("payload archive contains directory entries")
        actual = archive_member_paths(item.filename for item in entries)
        if actual != expected:
            raise BuildError("payload archive members differ from inventory")
        by_path = {str(item["path"]): item for item in inventory["files"]}
        for item in entries:
            if item.file_size != by_path[item.filename]["size"]:
                raise BuildError(f"payload archive size differs from inventory: {item.filename}")
            source_path = payload_root / Path(*item.filename.split("/"))
            expected_hash = by_path[item.filename]["sha256"]
            if sha256_file(source_path) != expected_hash or sha256_bytes(archive.read(item.filename)) != expected_hash:
                raise BuildError(f"payload archive hash differs from inventory: {item.filename}")


def _target_environment() -> dict[str, str]:
    environment = _clean_build_environment()
    environment.pop("PYTHONUSERBASE", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_target(python: Path, arguments: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(python), *arguments],
            cwd=cwd,
            env=_target_environment(),
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BuildError(f"cannot execute embedded payload runtime: {python}") from exc


def _assert_target_success(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        raise BuildError(f"{label} failed ({result.returncode}): {(result.stdout + result.stderr).strip()[-4000:]}")
    return result.stdout


def _run_cmd_launcher(launcher: Path, arguments: Sequence[str], cwd: Path) -> str:
    # Start with CALL instead of a quote.  That keeps cmd.exe /c from treating
    # the first quote as its special command-string delimiter when the path
    # contains spaces.
    command_line = 'call "' + str(launcher) + '"'
    for argument in arguments:
        command_line += " " + subprocess.list2cmdline([str(argument)])
    try:
        result = subprocess.run(
            command_line,
            cwd=cwd,
            env=_target_environment(),
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            shell=True,
        )
    except OSError as exc:
        raise BuildError("cannot execute candidate .cmd launcher") from exc
    return _assert_target_success(result, "candidate launcher")


def _probe_payload(payload_root: Path) -> None:
    runtime = payload_root / "runtime" / "python.exe"
    launcher = payload_root / "bin" / "llm-agent.cmd"
    launcher_py = payload_root / "app" / "launcher.py"
    with tempfile.TemporaryDirectory(prefix="w18-payload-probe-") as raw:
        probe_root = Path(raw)
        cwd = probe_root / "outside cwd"
        home = probe_root / "home"
        cwd.mkdir()
        result = _run_target(runtime, [str(launcher_py), "--version"], cwd)
        if _assert_target_success(result, "embedded --version").strip() != f"llm-agent {RELEASE_VERSION}":
            raise BuildError("embedded payload version is incorrect")
        result = _run_target(runtime, [str(launcher_py), "--help"], cwd)
        if "usage:" not in _assert_target_success(result, "embedded --help").casefold():
            raise BuildError("embedded payload help is missing usage")
        init = _run_target(runtime, [str(launcher_py), "config", "init", "--home", str(home)], cwd)
        _assert_target_success(init, "embedded config init")
        doctor = _run_target(
            runtime,
            [str(launcher_py), "doctor", "--json", "--home", str(home), "--workspace", str(cwd)],
            cwd,
        )
        payload = json.loads(_assert_target_success(doctor, "embedded offline doctor"))
        if payload.get("readiness", {}).get("offline_ready") is not True:
            raise BuildError("embedded offline doctor did not report offline_ready")
        code = (
            "import agent,importlib.metadata,json,pathlib,sys;"
            "print(json.dumps({'agent':str(pathlib.Path(agent.__file__).resolve()),"
            "'exe':str(pathlib.Path(sys.executable).resolve()),'agent_version':agent.__version__,"
            "'version':importlib.metadata.version('local-llm-agent'),'path':list(sys.path)}))"
        )
        origin = _run_target(runtime, ["-c", code], cwd)
        origin_data = json.loads(_assert_target_success(origin, "embedded import-origin probe"))
        if origin_data["agent_version"] != RELEASE_VERSION or origin_data["version"] != RELEASE_VERSION:
            raise BuildError("embedded metadata/version equality failed")
        if Path(origin_data["exe"]).resolve() != runtime.resolve():
            raise BuildError("embedded sys.executable is not payload runtime/python.exe")
        if not Path(origin_data["agent"]).resolve().is_relative_to(runtime.parent.resolve()):
            raise BuildError("embedded agent import escaped payload runtime")
        if any(str(value).casefold().startswith(str(ROOT).casefold()) for value in origin_data["path"] if value):
            raise BuildError("source checkout leaked into embedded sys.path")
        if os.name == "nt":
            if _run_cmd_launcher(launcher, ["--version"], cwd).strip() != f"llm-agent {RELEASE_VERSION}":
                raise BuildError("candidate .cmd launcher version is incorrect")
            _run_cmd_launcher(launcher, ["--help"], cwd)


def _probe_payload_relocations(payload_root: Path, scratch: Path) -> tuple[str, ...]:
    """Prove the same payload bytes execute after space/Unicode relocation."""

    probes = (
        ("relocated payload with spaces", scratch / "relocated payload with spaces"),
        ("payload relocado José", scratch / "payload relocado José"),
    )
    for _, destination in probes:
        shutil.copytree(payload_root, destination)
        _probe_payload(destination)
    return tuple(name for name, _ in probes)


def _copy_bundle_inputs(source_snapshot: Path, stage: Path) -> None:
    for source_relative, bundle_name in (
        *((Path("installer") / name, name) for name in INSTALL_WRAPPERS),
        (Path("distribution") / RUNTIME_LOCK, RUNTIME_LOCK),
        (Path("distribution") / BOOTSTRAP_PIP_LOCK, BOOTSTRAP_PIP_LOCK),
        (Path("distribution") / RELEASE_NOTICES, RELEASE_NOTICES),
    ):
        source = source_snapshot / source_relative
        if not source.is_file():
            raise BuildError(f"required bundle input is missing: {source_relative}")
        shutil.copy2(source, stage / bundle_name)


def _write_json(path: Path, document: Any) -> None:
    path.write_bytes((json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_release_zip(stage: Path, destination: Path, epoch: CanonicalReleaseEpoch) -> None:
    names = tuple(sorted(path.name for path in stage.iterdir() if path.is_file()))
    if names != tuple(sorted(BUNDLE_MEMBERS)):
        raise BuildError(f"bundle allowlist mismatch: {names}")
    write_canonical_zip(destination, ((name, (stage / name).read_bytes()) for name in names), epoch)


def build_release(
    output_dir: Path,
    python_executable: Path = Path(sys.executable),
    *,
    python_artifact: Path | None = None,
    wheelhouse: Path | None = None,
    uv_artifact: Path | None = None,
) -> BuildResult:
    """Build a candidate-bound wheel, payload, and v003 release bundle."""

    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_value = python_artifact or os.environ.get("W18_PYTHON_ARTIFACT")
    if not artifact_value:
        raise BuildError("--python-artifact is required; release build needs the frozen CPython input")
    artifact = Path(artifact_value).resolve()
    uv_artifact_value = uv_artifact or os.environ.get("W18_UV_ARTIFACT")
    if not uv_artifact_value:
        raise BuildError("--uv-artifact is required; release build needs the frozen uv input")
    uv_verification = _verify_uv_artifact(Path(uv_artifact_value))
    python_executable = python_executable.resolve()
    _verify_build_driver(python_executable)
    with isolated_candidate_tree(ROOT) as candidate:
        release_epoch = derive_release_epoch(ROOT, candidate.base_commit)
        with tempfile.TemporaryDirectory(prefix="candidate-build-") as raw:
            scratch = Path(raw)
            source_snapshot = scratch / "source"
            wheel_dir = scratch / "wheel"
            payload_root = scratch / "payload"
            _materialize_source_snapshot(candidate, source_snapshot)
            try:
                normalize_tree_mtimes(source_snapshot, release_epoch)
            except ReproducibilityError as exc:
                raise BuildError(f"source snapshot timestamps are not canonical: {exc}") from exc
            runtime_lock = source_snapshot / "distribution" / RUNTIME_LOCK
            bootstrap_lock = source_snapshot / "distribution" / BOOTSTRAP_PIP_LOCK
            runtime_summary = validate_runtime_lock(runtime_lock)
            bootstrap_summary = validate_bootstrap_lock(bootstrap_lock)
            wheel = _build_wheel(python_executable, source_snapshot, wheel_dir, release_epoch)
            _assert_no_artifact_path_markers(wheel, (scratch, output_dir))
            wheel_members = _validate_wheel(wheel)
            wheelhouse_path = _prepare_wheelhouse(
                python_executable, source_snapshot, runtime_lock, wheelhouse, scratch, release_epoch
            )
            runtime_root = payload_root / "runtime"
            _extract_python_artifact(artifact, runtime_root)
            _install_runtime_into_payload(
                python_executable,
                source_snapshot,
                runtime_root,
                wheel,
                runtime_lock,
                wheelhouse_path,
                scratch,
                release_epoch,
            )
            # The CPython install-only artifact currently includes precompiled
            # stdlib caches.  They are not required at runtime and become stale
            # when the archive is relocated/extracted, so remove them before
            # inventory sealing.  Later probes run with the same no-bytecode
            # policy and the final assertions prevent reintroduction.
            _remove_volatile_bytecode(payload_root)
            _write_payload_shim(payload_root)
            payload_members = _validate_payload_contents(payload_root, source_snapshot, scratch)
            _probe_payload(payload_root)
            _assert_no_volatile_bytecode(payload_root)
            relocation_probes = _probe_payload_relocations(payload_root, scratch)
            _assert_no_volatile_bytecode(payload_root)
            inventory = make_inventory(payload_root)
            inventory_bytes = render_inventory(inventory)
            inventory_path_scratch = scratch / PAYLOAD_INVENTORY
            inventory_path_scratch.write_bytes(inventory_bytes)
            payload_archive_scratch = scratch / PAYLOAD_ARCHIVE
            _write_deterministic_zip(payload_root, payload_archive_scratch, payload_members, release_epoch)
            try:
                validate_canonical_zip(payload_archive_scratch, release_epoch, payload_members)
            except ReproducibilityError as exc:
                raise BuildError(f"payload ZIP is not canonical: {exc}") from exc
            _assert_no_artifact_path_markers(payload_archive_scratch, (scratch, output_dir))
            _validate_payload_archive(payload_root, payload_archive_scratch, inventory)

            stage = output_dir / "bundle"
            stage.mkdir()
            shutil.copy2(wheel, stage / APPLICATION_WHEEL)
            _copy_bundle_inputs(source_snapshot, stage)
            shutil.copy2(payload_archive_scratch, stage / PAYLOAD_ARCHIVE)
            shutil.copy2(inventory_path_scratch, stage / PAYLOAD_INVENTORY)
            manifest = make_manifest(
                source_base_commit=candidate.base_commit,
                source_tree=candidate.tree,
                source_commit=None,
                wheel_sha256=sha256_file(stage / APPLICATION_WHEEL),
                payload_sha256=sha256_file(stage / PAYLOAD_ARCHIVE),
                payload_inventory_sha256=sha256_file(stage / PAYLOAD_INVENTORY),
                runtime_lock_sha256=sha256_file(stage / RUNTIME_LOCK),
                bootstrap_pip_lock_sha256=sha256_file(stage / BOOTSTRAP_PIP_LOCK),
                uv_evidence=uv_verification.manifest_evidence(),
            )
            manifest_path = stage / "release-manifest.json"
            manifest_path.write_bytes(render_manifest(manifest))
            validate_manifest(
                manifest,
                bundle_root=stage,
                expected_source_tree=candidate.tree,
                expected_base_commit=candidate.base_commit,
            )
            release_zip = output_dir / f"local-llm-agent-{RELEASE_VERSION}-windows-x64.zip"
            _write_release_zip(stage, release_zip, release_epoch)
            try:
                validate_canonical_zip(release_zip, release_epoch, BUNDLE_MEMBERS)
            except ReproducibilityError as exc:
                raise BuildError(f"release ZIP is not canonical: {exc}") from exc
            _assert_no_artifact_path_markers(release_zip, (scratch, output_dir))
            inventory_path = output_dir / "sha256-inventory.json"
            top_level_inventory = {
                "schema_version": "W18-SHA256-INVENTORY-V3",
                "candidate_id": manifest["candidate_id"],
                "source": manifest["source"],
                "members": {name: sha256_file(stage / name) for name in sorted(BUNDLE_MEMBERS)},
                "release_zip": {"path": release_zip.name, "sha256": sha256_file(release_zip)},
            }
            _write_json(inventory_path, top_level_inventory)
            output_manifest = output_dir / "release-manifest.json"
            shutil.copy2(manifest_path, output_manifest)
            attestation_path = output_dir / "provenance-attestation.json"
            _write_json(
                attestation_path,
                {
                    "schema_version": "W18-PROVENANCE-ATTESTATION-V3",
                    "status": "uncommitted_candidate",
                    "candidate_id": manifest["candidate_id"],
                    "source": manifest["source"],
                    "materialization": "temporary_git_index_and_object_database",
                    "payload": {
                        "path": PAYLOAD_ARCHIVE,
                        "sha256": manifest["payload"]["sha256"],
                        "inventory": PAYLOAD_INVENTORY,
                        "inventory_sha256": manifest["payload"]["inventory_sha256"],
                    },
                    "relocation_probes": list(relocation_probes),
                    "bundle_inventory": inventory_path.name,
                    "release_manifest": {
                        "path": output_manifest.name,
                        "sha256": sha256_file(output_manifest),
                    },
                },
            )
            uv_evidence_path = output_dir / "uv-build-evidence.json"
            _write_json(
                uv_evidence_path,
                {
                    "schema_version": "W18-UV-BUILD-ADVERSARIAL-EVIDENCE-V2",
                    "status": "passed",
                    "candidate_id": manifest["candidate_id"],
                    "source_tree": manifest["source"]["tree"],
                    "source": manifest["source"],
                    "release_manifest_sha256": sha256_file(output_manifest),
                    "pinned_uv": {
                        "url": UV_ASSET_URL,
                        "archive_sha256": UV_SHA256,
                        "version": UV_VERSION,
                    },
                    "uv": uv_verification.to_dict(),
                    "cases": _uv_adversarial_cases(uv_verification),
                },
            )
    return BuildResult(
        output_dir=output_dir,
        wheel=output_dir / "bundle" / APPLICATION_WHEEL,
        payload=output_dir / "bundle" / PAYLOAD_ARCHIVE,
        payload_inventory=output_dir / "bundle" / PAYLOAD_INVENTORY,
        manifest=output_dir / "release-manifest.json",
        release_zip=output_dir / f"local-llm-agent-{RELEASE_VERSION}-windows-x64.zip",
        inventory=output_dir / "sha256-inventory.json",
        attestation=output_dir / "provenance-attestation.json",
        wheel_sha256=sha256_file(output_dir / "bundle" / APPLICATION_WHEEL),
        payload_sha256=sha256_file(output_dir / "bundle" / PAYLOAD_ARCHIVE),
        payload_inventory_sha256=sha256_file(output_dir / "bundle" / PAYLOAD_INVENTORY),
        manifest_sha256=sha256_file(output_dir / "release-manifest.json"),
        zip_sha256=sha256_file(output_dir / f"local-llm-agent-{RELEASE_VERSION}-windows-x64.zip"),
        runtime_lock_sha256=sha256_file(output_dir / "bundle" / RUNTIME_LOCK),
        bootstrap_lock_sha256=sha256_file(output_dir / "bundle" / BOOTSTRAP_PIP_LOCK),
        candidate_id=manifest["candidate_id"],
        source_date_epoch=release_epoch.source_date_epoch,
        runtime_lock=runtime_summary,
        bootstrap_lock=bootstrap_summary,
        wheel_members=wheel_members,
        payload_members=payload_members,
        relocation_probes=relocation_probes,
        uv_evidence=uv_evidence_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--python-artifact", type=Path, required=False)
    parser.add_argument("--wheelhouse", type=Path, required=False)
    parser.add_argument("--uv-artifact", type=Path, required=False)
    args = parser.parse_args(argv)
    try:
        result = build_release(
            args.output_dir,
            args.python_executable,
            python_artifact=args.python_artifact,
            wheelhouse=args.wheelhouse,
            uv_artifact=args.uv_artifact,
        )
    except (BuildError, ValueError) as exc:
        print(f"W18 v003 release build failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "candidate_id": result.candidate_id,
                "source_date_epoch": result.source_date_epoch,
                "wheel": {"path": str(result.wheel), "sha256": result.wheel_sha256},
                "payload": {"path": str(result.payload), "sha256": result.payload_sha256},
                "payload_inventory": {
                    "path": str(result.payload_inventory),
                    "sha256": result.payload_inventory_sha256,
                },
                "manifest": {"path": str(result.manifest), "sha256": result.manifest_sha256},
                "release_zip": {"path": str(result.release_zip), "sha256": result.zip_sha256},
                "inventory": str(result.inventory),
                "attestation": str(result.attestation),
                "uv_evidence": str(result.uv_evidence),
                "runtime_packages": len(result.runtime_lock.packages),
                "runtime_hashes": result.runtime_lock.hash_count,
                "runtime_sdist_count": result.runtime_lock.sdist_count,
                "bundle_members": list(BUNDLE_MEMBERS),
                "wheel_members": len(result.wheel_members),
                "payload_members": len(result.payload_members),
                "relocation_probes": list(result.relocation_probes),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
