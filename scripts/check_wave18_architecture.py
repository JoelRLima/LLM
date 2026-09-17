"""Model-free ownership and prohibition checks for the installed product.

This checker is intentionally conservative.  It covers the static seams that
must remain true even when the Windows lifecycle job is not available, while
the focused tests and installed-product harness cover behaviour.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS7_PATTERNS = (
    (re.compile(r"\?\?"), "null-coalescing operator"),
    (re.compile(r"ForEach-Object\s+-Parallel", re.IGNORECASE), "ForEach-Object -Parallel"),
    (re.compile(r"Start-Process[^\r\n]*-Environment", re.IGNORECASE), "Start-Process -Environment"),
    (re.compile(r"\?\s+[^:\r\n]+\s+:"), "ternary operator"),
)
FORBIDDEN_RUNTIME_TOKENS = (
    "uv tool install",
    "uv self update",
    "irm | iex",
    "curl | sh",
    "SetEnvironmentVariable(\"Path\",",
)
IDENTITY_OWNER = "distribution/release_identity.py"


@dataclass(frozen=True)
class Violation:
    rule_id: str
    path: str
    detail: str

    def format(self) -> str:
        return f"{self.rule_id} {self.path}: {self.detail}"


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _exists(*relative_paths: str) -> list[Violation]:
    return [
        Violation("W18-ARCH-01", relative, "required W18 owner surface is missing")
        for relative in relative_paths
        if not (ROOT / relative).is_file()
    ]


def _check_runtime_firewall() -> list[Violation]:
    findings: list[Violation] = []
    runtime_files = sorted((ROOT / "agent").rglob("*.py"))
    for path in runtime_files:
        relative = _relative(path)
        text = _read(path).casefold()
        if relative in {"agent/__init__.py", "agent/_version.py"}:
            continue
        for token in FORBIDDEN_RUNTIME_TOKENS:
            if token.casefold() in text:
                findings.append(
                    Violation("W18-ARCH-02", relative, f"installer/provisioning token escaped runtime: {token}")
                )
        try:
            tree = ast.parse(_read(path), filename=relative)
        except SyntaxError:
            findings.append(Violation("W18-ARCH-03", relative, "runtime module is not parseable"))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                imported_names = [alias.name for alias in node.names]
                if imported.startswith(("installer", "distribution")) or any(
                    name.startswith(("installer", "distribution")) for name in imported_names
                ):
                    findings.append(
                        Violation("W18-ARCH-04", relative, "agent runtime imports the W18 installer boundary")
                    )
    return findings


def _check_candidate_lease_surface() -> list[Violation]:
    required = (
        "agent/runtime/candidate_lease.py",
        "scripts/installer_fault_instrumentation.py",
    )
    return [
        Violation("W18-ARCH-20", path, "W18 C2 lease/instrumentation surface is missing")
        for path in required
        if not (ROOT / path).is_file()
    ]


_INSTALLER_FILES = (
    "installer/install.ps1",
    "installer/uninstall.ps1",
    "installer/install.cmd",
    "installer/uninstall.cmd",
)


def _check_powershell_syntax() -> list[Violation]:
    findings: list[Violation] = []
    for relative in _INSTALLER_FILES[:2]:
        text = _read(ROOT / relative)
        for pattern, label in PS7_PATTERNS:
            if pattern.search(text):
                findings.append(Violation("W18-ARCH-05", relative, f"PowerShell 5.1 path uses {label}"))
    return findings


def _check_installer_controls() -> list[Violation]:
    findings: list[Violation] = []
    text = _read(ROOT / "installer/install.ps1").casefold()
    required_tokens = (
        "get-authenticodesignature",
        "invoke-webrequest -usebasicparsing",
        "--no-config",
        "--managed-python",
        "--without-pip",
        "--require-hashes",
        "--only-binary",
        "--isolated",
        "local\\w18-local-llm-agent",
        "read-userpathsnapshot",
        "set-journalstate",
        "fresh_shell_verified",
        "file]::replace",
        "Global\\w18-candidate-",
        "Enter-W18CandidateLease",
        "Enter-W18UninstallCandidateLeases",
    )
    for required_token in required_tokens:
        if required_token not in text:
            findings.append(Violation("W18-ARCH-06", "installer/install.ps1", f"installer is missing required control {required_token}"))
    return findings


def _check_cmd_wrappers() -> list[Violation]:
    findings: list[Violation] = []
    for relative in _INSTALLER_FILES[2:]:
        text = _read(ROOT / relative).casefold()
        if "powershell.exe" not in text or "-noprofile" not in text or "-file" not in text:
            findings.append(Violation("W18-ARCH-07", relative, "cmd wrapper must use no-profile PowerShell"))
    return findings


def _check_forbidden_installer_tokens() -> list[Violation]:
    findings: list[Violation] = []
    for relative in (*_INSTALLER_FILES, "scripts/build_release_artifacts.py"):
        text = _read(ROOT / relative).casefold()
        for forbidden in ("uv tool install", "irm | iex", "curl | sh", "pyinstaller", "msix", "winget"):
            if forbidden in text:
                findings.append(Violation("W18-ARCH-19", relative, f"forbidden production path token: {forbidden}"))
    if "setenvironmentvariable" in _read(ROOT / "installer/install.ps1").casefold():
        findings.append(Violation("W18-ARCH-19", "installer/install.ps1", "installer must not mutate Machine PATH"))
    return findings


def _check_installer_scripts() -> list[Violation]:
    findings = _exists(*_INSTALLER_FILES)
    findings.extend(_check_powershell_syntax())
    findings.extend(_check_installer_controls())
    findings.extend(_check_cmd_wrappers())
    findings.extend(_check_forbidden_installer_tokens())
    return findings


def _check_distribution_boundary() -> list[Violation]:
    findings = _exists(
        "distribution/release_identity.py",
        "distribution/release_manifest.py",
        "distribution/lockfiles.py",
        "distribution/provenance.py",
        "distribution/reproducibility.py",
        "distribution/bootstrap-pip.lock",
        "distribution/runtime-windows-py312.lock",
        "scripts/build_release_artifacts.py",
        "scripts/check_reproducible_release.py",
    )
    identity = _read(ROOT / IDENTITY_OWNER)
    for token in (
        "IDENTITY_STATUS = \"provisional\"",
        "APPLICATION_NAMESPACE = \"local-llm-agent\"",
        "CLI_NAME = \"llm-agent\"",
        "UV_VERSION = \"0.12.13\"",
        "PYTHON_VERSION = \"3.12.14\"",
    ):
        if token not in identity:
            findings.append(Violation("W18-ARCH-08", IDENTITY_OWNER, f"frozen identity is missing {token}"))
    version = _read(ROOT / "agent/_version.py")
    if "VERSION = \"0.2.0rc1\"" not in version or "local-llm-agent" in version:
        findings.append(Violation("W18-ARCH-09", "agent/_version.py", "version owner is not name-independent"))
    project = _read(ROOT / "pyproject.toml")
    if 'dynamic = ["version"]' not in project or 'version = {attr = "agent._version.VERSION"}' not in project:
        findings.append(Violation("W18-ARCH-10", "pyproject.toml", "setuptools version is not sourced from the single owner"))
    if 'version = "0.1.0"' in project:
        findings.append(Violation("W18-ARCH-11", "pyproject.toml", "old duplicated version remains"))
    return findings


def _check_release_and_ci() -> list[Violation]:
    findings: list[Violation] = []
    builder = _read(ROOT / "scripts/build_release_artifacts.py") + _read(ROOT / "scripts/build_windows_payload.py")
    if "isolated_candidate_tree" not in builder or "materialize_candidate_tree" not in builder:
        findings.append(Violation("W18-ARCH-12", "scripts/build_release_artifacts.py", "release builder is not candidate-tree-bound"))
    if "BUNDLE_MEMBERS" not in builder or "release-manifest.json" not in builder:
        findings.append(Violation("W18-ARCH-13", "scripts/build_release_artifacts.py", "release bundle allowlist is not manifest-bound"))
    if "provenance-attestation.json" not in builder or "uncommitted_candidate" not in builder:
        findings.append(Violation("W18-ARCH-13", "scripts/build_release_artifacts.py", "candidate provenance evidence is missing"))
    provenance = _read(ROOT / "distribution/provenance.py")
    if "assert_commit_tree_matches" not in provenance or "seal_manifest_for_commit" not in provenance:
        findings.append(Violation("W18-ARCH-13", "distribution/provenance.py", "post-audit sealing support is missing"))
    workflow = _read(ROOT / ".github/workflows/ci.yml")
    if "mypy --platform linux" not in workflow or "mypy --platform win32" not in workflow:
        findings.append(Violation("W18-ARCH-14", ".github/workflows/ci.yml", "existing Mypy matrix coverage was removed"))
    if workflow.find("Repository quality policy") > workflow.find("Pytest") >= 0:
        findings.append(Violation("W18-ARCH-15", ".github/workflows/ci.yml", "quality gate must precede pytest"))
    dedicated = _read(next(iter(sorted((ROOT / ".github" / "workflows").glob("*-installed-product.yml"))), ROOT / ".github/workflows"))
    for token in ("windows-latest", "build_release_artifacts.py", "verify_installed_product.py", "run_wave18_adversarial.py"):
        if token not in dedicated:
            findings.append(Violation("W18-ARCH-16", ".github/workflows", f"dedicated job is missing {token}"))
    if any(token.casefold() in dedicated.casefold() for token in ("gh release create", "twine upload", "git push", "git tag")):
        findings.append(Violation("W18-ARCH-17", ".github/workflows", "ordinary CI contains publication side effects"))
    return findings


def _check_w17_ownership() -> list[Violation]:
    findings: list[Violation] = []
    for relative in (
        "agent/application.py",
        "agent/orchestrator.py",
        "agent/planning",
        "agent/tools",
        "agent/interfaces/cli/first_run.py",
        "agent/interfaces/cli/interactive_session.py",
    ):
        path = ROOT / relative
        candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for candidate in candidates:
            text = _read(candidate).casefold()
            if "installer.install" in text or "set-userpath" in text or "uv python install" in text:
                findings.append(Violation("W18-ARCH-18", _relative(candidate), "W18 installer logic entered a W17/core owner"))
    return findings


def check_architecture(root: Path = ROOT) -> list[Violation]:
    del root
    findings: list[Violation] = []
    findings.extend(_check_distribution_boundary())
    findings.extend(_check_installer_scripts())
    findings.extend(_check_runtime_firewall())
    findings.extend(_check_candidate_lease_surface())
    findings.extend(_check_release_and_ci())
    findings.extend(_check_w17_ownership())
    return sorted(findings, key=lambda finding: (finding.path, finding.rule_id, finding.detail))


def main() -> int:
    findings = check_architecture()
    if findings:
        print("W18 architecture failed:")
        for finding in findings:
            print(finding.format())
        return 1
    print("W18_ARCHITECTURE=PASS")
    return 0


if False and __name__ == "__main__":
    raise SystemExit(main())


def _v3_read(relative: str) -> str:
    return _read(ROOT / relative)


_V3_REQUIRED_SURFACES = (
    "distribution/payload.py",
    "distribution/release_identity.py",
    "distribution/release_manifest.py",
    "distribution/provenance.py",
    "distribution/reproducibility.py",
    "scripts/build_release_artifacts.py",
    "scripts/build_windows_payload.py",
    "scripts/check_reproducible_release.py",
    "scripts/verify_installed_product.py",
)
_V3_IDENTITY_CONTROLS = (
    'SCHEMA_VERSION = "W18-RELEASE-MANIFEST-V3"',
    'IDENTITY_STATUS = "provisional"',
    "PYTHON_SOURCE_ARTIFACT_SHA256",
    'PAYLOAD_ARCHIVE = "payload-windows-x64.zip"',
    'PAYLOAD_INVENTORY = "payload-files.json"',
)
_V3_INSTALLER_CONTROLS = (
    "payload-windows-x64.zip",
    "payload-files.json",
    "Extract-Payload",
    "runtime\\python.exe",
    "Test-FreshShell",
    "Recover-IncompleteJournal",
    "Set-UserPathValue",
    "[System.IO.File]::Replace",
    "Local\\W18-local-llm-agent",
    "Global\\W18-candidate-gate-",
    "Global\\W18-candidate-active-",
    "Enter-W18CandidateLease",
    "Enter-W18UninstallCandidateLeases",
    "PYTHONDONTWRITEBYTECODE",
)
_V3_INSTALLER_FORBIDDEN = (
    "invoke-webrequest",
    "webclient",
    "start-bitstransfer",
    "downloadstring",
    "curl.exe",
    "wget.exe",
    "irm ",
    "invoke-expression",
    "encodedcommand",
    "frombase64string",
    "uv python",
    "pip install",
    "python -m pip",
    "--managed-python",
    "--without-pip",
    "new-item -itemtype directory -path $venv",
)


def _installed_product_workflow_path() -> Path | None:
    candidates = tuple(sorted((ROOT / ".github" / "workflows").glob("*-installed-product.yml")))
    return candidates[0] if len(candidates) == 1 else None


def _v3_required_surface_findings() -> list[Violation]:
    findings = _exists(*_V3_REQUIRED_SURFACES)
    if _installed_product_workflow_path() is None:
        findings.append(
            Violation(
                "W18-V3-01",
                ".github/workflows",
                "exactly one installed-product workflow is required",
            )
        )
    return findings


def _v3_identity_findings() -> list[Violation]:
    identity = _v3_read(IDENTITY_OWNER)
    return [
        Violation("W18-V3-02", IDENTITY_OWNER, f"missing v003 identity control: {token}")
        for token in _V3_IDENTITY_CONTROLS
        if token not in identity
    ]


def _v3_installer_control_findings(installer: str) -> list[Violation]:
    lowered = installer.casefold()
    return [
        Violation("W18-V3-03", "installer/install.ps1", f"missing offline control: {token}")
        for token in _V3_INSTALLER_CONTROLS
        if token.casefold() not in lowered
    ]


def _v3_installer_forbidden_findings(installer: str) -> list[Violation]:
    lowered = installer.casefold()
    findings = [
        Violation("W18-V3-04", "installer/install.ps1", f"online/bootstrap or dynamic-code token remains: {token}")
        for token in _V3_INSTALLER_FORBIDDEN
        if token in lowered
    ]
    if re.search(r"['\"]-m['\"]\s*,\s*['\"](?:pip|venv)['\"]", installer, re.IGNORECASE):
        findings.append(Violation("W18-V3-04", "installer/install.ps1", "installer invokes a package manager or venv"))
    if "get-authenticodesignature" in lowered:
        findings.append(Violation("W18-V3-05", "installer/install.ps1", "release-build signature verification leaked into installer"))
    if "setenvironmentvariable" in lowered:
        findings.append(Violation("W18-V3-06", "installer/install.ps1", "installer must not write process/system PATH through Environment API"))
    return findings


def _v3_installer_shell_findings() -> list[Violation]:
    findings: list[Violation] = []
    for relative in ("installer/install.ps1", "installer/uninstall.ps1"):
        text = _v3_read(relative)
        findings.extend(
            Violation("W18-V3-07", relative, f"PowerShell 5.1 path uses {label}")
            for pattern, label in PS7_PATTERNS
            if pattern.search(text)
        )
    for relative in ("installer/install.cmd", "installer/uninstall.cmd"):
        text = _v3_read(relative).casefold()
        if "powershell.exe" not in text or "-noprofile" not in text or "-file" not in text:
            findings.append(Violation("W18-V3-08", relative, "wrapper must use no-profile PowerShell"))
    return findings


def _v3_installer_findings() -> list[Violation]:
    installer = _v3_read("installer/install.ps1")
    findings = _v3_installer_control_findings(installer)
    findings.extend(_v3_installer_forbidden_findings(installer))
    findings.extend(_v3_installer_shell_findings())
    return findings


def _v3_release_findings() -> list[Violation]:
    findings: list[Violation] = []
    builder = _v3_read("scripts/build_windows_payload.py") + _v3_read("scripts/build_release_artifacts.py")
    for token in (
        "isolated_candidate_tree",
        "materialize_candidate_tree",
        "payload-files.json",
        "payload-windows-x64.zip",
        "uncommitted_candidate",
        "PYTHONDONTWRITEBYTECODE",
        "SOURCE_DATE_EPOCH",
        "canonicalize_distlib_launchers",
        "reconcile_dist_info_records",
        "normalize_tree_mtimes",
        "validate_canonical_zip",
    ):
        if token not in builder:
            findings.append(Violation("W18-V3-09", "scripts/build_release_artifacts.py", f"release builder missing {token}"))
    payload = _v3_read("distribution/payload.py")
    for token in ("W18-PAYLOAD-FILES-V1", "sha256", "casefold", "reparse"):
        if token.casefold() not in payload.casefold():
            findings.append(Violation("W18-V3-10", "distribution/payload.py", f"payload inventory missing {token}"))
    manifest = _v3_read("distribution/release_manifest.py")
    for token in ("payload_inventory_sha256", "source_artifact_sha256", "build_tools", "candidate_id"):
        if token not in manifest:
            findings.append(Violation("W18-V3-11", "distribution/release_manifest.py", f"v003 manifest missing {token}"))
    return findings


def _v3_workflow_findings() -> list[Violation]:
    workflow_path = _installed_product_workflow_path()
    if workflow_path is None:
        return [Violation("W18-V3-12", ".github/workflows", "exactly one installed-product workflow is required")]
    workflow = _read(workflow_path)
    relative = _relative(workflow_path)
    findings = [
        Violation("W18-V3-12", relative, f"dedicated workflow missing {token}")
        for token in ("windows-latest", "build_release_artifacts.py", "--python-artifact", "verify_installed_product.py", "run_wave18_adversarial.py")
        if token not in workflow
    ]
    if any(token.casefold() in workflow.casefold() for token in ("twine upload", "gh release create", "git push", "git tag")):
        findings.append(Violation("W18-V3-13", relative, "workflow contains publication side effects"))
    normal_ci = _v3_read(".github/workflows/ci.yml")
    if "mypy --platform linux" not in normal_ci or "mypy --platform win32" not in normal_ci:
        findings.append(Violation("W18-V3-14", ".github/workflows/ci.yml", "existing Mypy matrix coverage was removed"))
    if normal_ci.find("Repository quality policy") > normal_ci.find("Pytest") >= 0:
        findings.append(Violation("W18-V3-15", ".github/workflows/ci.yml", "quality must precede pytest"))
    return findings


def _v3_runtime_boundary_findings() -> list[Violation]:
    findings: list[Violation] = []
    for path in sorted((ROOT / "agent").rglob("*.py")):
        relative = _relative(path)
        if relative in {"agent/__init__.py", "agent/_version.py"}:
            continue
        if re.search(r"(?:from|import)\s+(?:installer|distribution)(?:\.|\s|$)", _read(path)):
            findings.append(Violation("W18-V3-16", relative, "W18 boundary imported into application runtime"))
    return findings


def _v3_w17_findings() -> list[Violation]:
    findings: list[Violation] = []
    for relative in (
        "agent/application.py",
        "agent/orchestrator.py",
        "agent/interfaces/cli/first_run.py",
        "agent/interfaces/cli/interactive_session.py",
    ):
        text = _v3_read(relative).casefold()
        if "installer" in text or "set-userpath" in text:
            findings.append(Violation("W18-V3-17", relative, "installer logic entered a W17 owner"))
    return findings


def _v3_architecture() -> list[Violation]:
    findings: list[Violation] = []
    findings.extend(_v3_required_surface_findings())
    findings.extend(_v3_identity_findings())
    findings.extend(_v3_installer_findings())
    findings.extend(_v3_release_findings())
    findings.extend(_v3_workflow_findings())
    findings.extend(_v3_runtime_boundary_findings())
    findings.extend(_v3_w17_findings())
    return sorted(findings, key=lambda finding: (finding.path, finding.rule_id, finding.detail))


def _v3_check_architecture(root: Path = ROOT) -> list[Violation]:
    del root
    return _v3_architecture()


globals()["check_architecture"] = _v3_check_architecture


def _v3_main() -> int:
    findings = _v3_architecture()
    if findings:
        print("W18 architecture failed:")
        for finding in findings:
            print(finding.format())
        return 1
    print("W18_ARCHITECTURE=PASS")
    return 0


globals()["main"] = _v3_main


if __name__ == "__main__":
    raise SystemExit(_v3_main())
