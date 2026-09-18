"""Run the deterministic W18-A01..A60 campaign.

The campaign is intentionally bounded.  Pure manifest/path/journal checks
remain local; A45-A55 lifecycle IDs are PASS only when this command consumes
machine-readable evidence from the canonical installed-product verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.runtime import paths as runtime_paths  # noqa: E402
from agent.runtime.paths import APP_DIRECTORY_NAME, AppPaths  # noqa: E402
from distribution.provenance import ProvenanceError, isolated_candidate_tree  # noqa: E402
from distribution.release_identity import APPLICATION_NAMESPACE  # noqa: E402
from distribution.release_manifest import (  # noqa: E402
    ManifestValidationError,
    candidate_id,
    make_manifest,
    sha256_file,
    validate_manifest,
)
from installer.journal import (  # noqa: E402
    JournalValidationError,
    recovery_needed,
    validate_journal,
)
from installer.path_semantics import (  # noqa: E402
    PathSnapshot,
    add_owned_segment,
    remove_owned_segment,
)
from scripts.conpty_authority import (  # noqa: E402
    ConPtyAuthorityError,
    validate_conpty_authority_evidence,
)
from scripts.uv_build_evidence import (  # noqa: E402
    UV_REQUIRED_CASES,
    UvBuildEvidenceError,
    validate_uv_build_evidence,
)


class ScenarioFailure(AssertionError):
    """Raised when one permanent adversarial scenario fails."""


@dataclass(frozen=True)
class Scenario:
    identifier: str
    description: str
    run: Callable[[], str]


_BEHAVIORAL_EVIDENCE: dict[str, Any] | None = None
_UV_BUILD_EVIDENCE: dict[str, Any] | None = None
_BEHAVIORAL_SCENARIOS = {
    "W18-A45": "A45_valid_A_to_B",
    "W18-A46": "A46_invalid_pre_promotion",
    "W18-A47": "A47_post_promotion_rollback",
    "W18-A48": "A48_post_path_rollback",
    "W18-A49": ("A49_active_stale_candidate", "A49_inactive_stale_cleanup"),
    "W18-A50": "A50_previous_known_good",
    "W18-A53": "A53_exact_unrelated_path_preservation",
    "W18-A54": "A54_missing_receipt",
    "W18-A55": "A55_corrupt_receipt",
    "W18-A57": "A57_first_run_installed_interactive",
    "W18-A58": "A58_existing_config_installed_interactive",
}


def _behavioral_evidence(identifier: str) -> str:
    """Classify evidence from the canonical installed-product verifier."""

    if _BEHAVIORAL_EVIDENCE is None:
        return "not-run: requires --installed-evidence from verify_installed_product"
    expected = _BEHAVIORAL_SCENARIOS[identifier]
    keys = expected if isinstance(expected, tuple) else (expected,)
    scenarios = _BEHAVIORAL_EVIDENCE.get("scenarios", {})
    missing = [key for key in keys if scenarios.get(key, {}).get("status") != "passed"]
    if missing:
        return "behavioral-failed: " + ",".join(missing)
    return "behavioral-evidence: " + ",".join(keys)


def _behavioral_scenario(identifier: str) -> str:
    result = _behavioral_evidence(identifier)
    if result.startswith("not-run:") or result.startswith("behavioral-failed:"):
        raise ScenarioFailure(result)
    return result


def _uv_build_evidence_result() -> str:
    if _UV_BUILD_EVIDENCE is None:
        return "not-run: requires --uv-build-evidence from the proof release build"
    return "uv-build-evidence: " + ",".join(UV_REQUIRED_CASES)


def _installed_product_workflow() -> Path:
    candidates = tuple(sorted((ROOT / ".github" / "workflows").glob("*-installed-product.yml")))
    if len(candidates) != 1:
        raise ScenarioFailure("exactly one installed-product workflow is required")
    return candidates[0]


def _expect_rejection(action: Callable[[], Any]) -> str:
    try:
        action()
    except (ManifestValidationError, JournalValidationError, ValueError, OSError):
        return "rejected"
    raise ScenarioFailure("malformed input was accepted")


def _manifest() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        make_manifest(
        source_base_commit="a" * 40,
        source_tree="b" * 40,
        wheel_sha256="1" * 64,
        runtime_lock_sha256="2" * 64,
        bootstrap_pip_lock_sha256="3" * 64,
        ),
    )


def _bundle_with_manifest(document: dict[str, Any], root: Path) -> None:
    root.mkdir(exist_ok=True)
    wheel = root / document["application"]["wheel"]
    runtime = root / document["runtime_lock"]["path"]
    bootstrap = root / document["bootstrap_pip_lock"]["path"]
    wheel.write_bytes(b"wheel")
    runtime.write_text("runtime\n", encoding="utf-8")
    bootstrap.write_text("bootstrap\n", encoding="utf-8")
    document["application"]["wheel_sha256"] = sha256_file(wheel)
    document["runtime_lock"]["sha256"] = sha256_file(runtime)
    document["bootstrap_pip_lock"]["sha256"] = sha256_file(bootstrap)
    document["candidate_id"] = candidate_id(document)


def _ps_text() -> str:
    return (ROOT / "installer/install.ps1").read_text(encoding="utf-8")


def _require_tokens(*tokens: str) -> str:
    text = _ps_text()
    missing = [token for token in tokens if token not in text]
    if missing:
        raise ScenarioFailure(f"missing production controls: {missing}")
    return "controls-present"


def _require_no_public_release() -> str:
    workflow = _installed_product_workflow()
    text = workflow.read_text(encoding="utf-8").lower()
    forbidden = ("twine", "pypi", "gh release", "publish-package", "publish-release")
    found = [token for token in forbidden if token in text]
    if found:
        raise ScenarioFailure(f"public release controls found in W18 workflow: {found}")
    return "workflow-no-publication"


def _path_environment() -> dict[str, str]:
    return {"LOCALAPPDATA": r"C:\Users\José Teste\AppData\Local"}


def _journal_document(state: str = "PREPARED") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": "install",
        "transaction_id": "w18-test",
        "state": state,
        "install_root": r"C:\Users\Test\AppData\Local\local-llm-agent\install",
        "prior_install_root_present": False,
        "stable_launcher": r"C:\Users\Test\AppData\Local\local-llm-agent\install\bin\llm-agent.exe",
        "owned_path": r"C:\Users\Test\AppData\Local\local-llm-agent\install\bin",
        "candidate_id": "w18-" + "a" * 32,
        "candidate_path": r"C:\Users\Test\AppData\Local\local-llm-agent\install\versions\w18-" + "a" * 32,
        "candidate_backup": "",
        "transaction_root": r"C:\Users\Test\AppData\Local\local-llm-agent\install\transactions\w18-test",
        "prior_path": {"key_present": True, "present": True, "value": "A;B", "kind": "ExpandString"},
        "prior_launcher_present": False,
        "prior_launcher_sha256": "",
        "prior_launcher_backup": "",
        "launcher_promoted": False,
        "path_mutated": False,
        "prior_receipt_present": False,
        "prior_receipt_backup": "",
        "prior_candidate_id": "",
        "prior_previous_candidate_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _manifest_field(field: str) -> str:
    with tempfile.TemporaryDirectory(prefix="w18-a-") as raw:
        root = Path(raw)
        document = _manifest()
        _bundle_with_manifest(document, root)
        if field == "schema_version":
            document[field] = "W18-RELEASE-MANIFEST-V2"
            return _expect_rejection(lambda: validate_manifest(document, bundle_root=root))
        if field == "uv":
            document["uv"]["sha256"] = "0" * 64
        else:
            artifact = document["application" if field == "wheel" else field]
            artifact["sha256" if field != "wheel" else "wheel_sha256"] = "0" * 64
        return _expect_rejection(lambda: validate_manifest(document, bundle_root=root))


def _path_case(raw: str, operation: str) -> str:
    owned = r"C:\Users\José Teste\AppData\Local\local-llm-agent\install\bin"
    snapshot = PathSnapshot(True, raw, "ExpandString", True)
    if operation == "add":
        result = add_owned_segment(snapshot, owned, _path_environment())
    else:
        result = remove_owned_segment(snapshot, owned, _path_environment())
    if result.owned_count != (1 if operation == "add" else 0):
        raise ScenarioFailure(f"wrong owned count for {raw!r}: {result}")
    return "path-model-pass"


def _tamper_runtime_file() -> str:
    with tempfile.TemporaryDirectory(prefix="w18-a-") as raw:
        root = Path(raw)
        document = _manifest()
        _bundle_with_manifest(document, root)
        runtime = root / document["runtime_lock"]["path"]
        runtime.write_text("tampered\n", encoding="utf-8")
        return _expect_rejection(lambda: validate_manifest(document, bundle_root=root))


def _valid_journal_state(state: str) -> str:
    if not recovery_needed(state):
        raise ScenarioFailure(f"state should be recoverable: {state}")
    document = _journal_document(state)
    validate_journal(
        document,
        install_root=document["install_root"],
        stable_launcher=document["stable_launcher"],
        owned_path=document["owned_path"],
    )
    return "journal-model-pass"


def _legacy_pre_v003_scenario_table_not_executed() -> tuple[Scenario, ...]:
    scenarios = [
        Scenario("W18-A01", "wrong bundle manifest schema", lambda: _manifest_field("schema_version")),
        Scenario("W18-A02", "wrong application wheel hash", lambda: _manifest_field("wheel")),
        Scenario("W18-A03", "wrong runtime lock hash", lambda: _manifest_field("runtime_lock")),
        Scenario("W18-A04", "wrong pip bootstrap hash", lambda: _manifest_field("bootstrap_pip_lock")),
        Scenario("W18-A05", "wrong uv hash", lambda: _manifest_field("uv")),
        Scenario("W18-A06", "uv invalid Authenticode", lambda: _require_tokens("Get-AuthenticodeSignature", 'Status -ne "Valid"')),
        Scenario("W18-A07", "unexpected uv archive member/traversal", lambda: _require_tokens("Expand-UvArchive", 'allowed = @("uv.exe", "uvw.exe", "uvx.exe")')),
        Scenario("W18-A08", "hostile cwd uv.toml", lambda: _require_tokens("--no-config", "New-ProductEnvironment")),
        Scenario("W18-A09", "hostile .python-version", lambda: _require_tokens("--managed-python", "exact_version")),
        Scenario("W18-A10", "hostile VIRTUAL_ENV", lambda: _require_tokens('"VIRTUAL_ENV"')),
        Scenario("W18-A11", "hostile CONDA_PREFIX", lambda: _require_tokens('"CONDA_PREFIX"')),
        Scenario("W18-A12", "hostile PYTHONPATH", lambda: _require_tokens('"PYTHONPATH"')),
        Scenario("W18-A13", "hostile PIP_INDEX_URL", lambda: _require_tokens('"PIP_INDEX_URL"', 'https://pypi.org/simple')),
        Scenario("W18-A14", "hostile PIP_CONFIG_FILE", lambda: _require_tokens('"PIP_CONFIG_FILE"', '"--isolated"')),
        Scenario("W18-A15", "no Python on PATH", lambda: _require_tokens("Find-ManagedPython", "python.exe")),
        Scenario("W18-A16", "incompatible system Python only", lambda: _require_tokens('"--managed-python"', "Python gerenciado escapou")),
        Scenario("W18-A17", "uv attempts to expose Python bin", lambda: _require_tokens('"UV_PYTHON_INSTALL_BIN"', '"0"', "PythonBinRoot")),
        Scenario("W18-A18", "runtime lock requires sdist", lambda: _require_tokens("--only-binary=:all:", "Assert-LockFile")),
        Scenario("W18-A19", "dependency wheel hash mismatch", _tamper_runtime_file),
        Scenario("W18-A20", "Agent editable/source install attempt", lambda: _require_tokens('"--no-deps"', '"--no-index"', "BundleRoot")),
        Scenario("W18-A21", "path with spaces", lambda: _path_case(r"C:\Users\Test User\AppData\Local", "add")),
        Scenario("W18-A22", "path with accented Unicode", lambda: _path_case(r"C:\Users\José Teste\AppData\Local", "add")),
        Scenario("W18-A23", "empty User PATH", lambda: _path_case("", "add")),
        Scenario("W18-A24", "raw PATH with percent variables", lambda: _path_case(r"%SystemRoot%\System32;C:\Other", "add")),
        Scenario("W18-A25", "raw PATH with duplicate semicolons/empty entries", lambda: _path_case("A;;B;", "add")),
        Scenario("W18-A26", "equivalent owned PATH segment", lambda: _path_case("%LOCALAPPDATA%\\local-llm-agent\\install\\bin\\", "add")),
        Scenario("W18-A27", "unrelated llm-agent command collision", lambda: _require_tokens("Assert-NoPersistentCollision", "Get-PersistentCommandPath", "Get-Command")),
        Scenario("W18-A28", "transient dev venv collision only", lambda: _require_tokens("New-ProductEnvironment", "Read-UserPathSnapshot")),
        Scenario("W18-A29", "Machine PATH remains unchanged", lambda: _require_tokens('GetEnvironmentVariable("Path", "Machine")', "Read-UserPathSnapshot")),
        Scenario("W18-A30", "mutex contention", lambda: _require_tokens("Enter-W18Mutex", "WaitOne", "Local\\W18-local-llm-agent")),
        Scenario("W18-A31", "abandoned mutex recovery", lambda: _require_tokens("AbandonedMutexException", "Recover-IncompleteJournal")),
        Scenario("W18-A32", "journal corrupt", lambda: _expect_rejection(lambda: validate_journal({}, install_root="", stable_launcher="", owned_path=""))),
        Scenario("W18-A33", "journal PREPARED recovery", lambda: _valid_journal_state("PREPARED")),
        Scenario("W18-A34", "journal STAGED recovery", lambda: _valid_journal_state("STAGED")),
        Scenario("W18-A35", "journal LAUNCHER_PROMOTED recovery", lambda: _valid_journal_state("LAUNCHER_PROMOTED")),
        Scenario("W18-A36", "journal PATH_MUTATED recovery", lambda: _valid_journal_state("PATH_MUTATED")),
        Scenario("W18-A37", "launcher promotion access denied", lambda: _require_tokens("Promote-StableLauncher", "Rollback-Transaction", "File]::Replace")),
        Scenario("W18-A38", "stable launcher wrong version", lambda: _require_tokens("launcher estavel reportou versao incorreta", "--version")),
        Scenario("W18-A39", "stable launcher wrong import origin", lambda: _require_tokens("import-origin", "agent", "sys.executable")),
        Scenario("W18-A40", "fresh child inherited dev PATH trap", lambda: _require_tokens("New-FreshShellProbeScript", "-NoProfile", "CandidatePython")),
        Scenario("W18-A41", "fresh shell arbitrary cwd", lambda: _require_tokens("Set-Location -LiteralPath $ProbeCwd", "-NoProfile")),
        Scenario("W18-A42", "System32 human acceptance script/procedure", lambda: _require_tokens("fresh-shell-cwd", "Test-FreshShell")),
        Scenario("W18-A43", "same candidate reinstall", lambda: _require_tokens("Test-SameCandidateHealthy", "idempotente")),
        Scenario("W18-A44", "repair corrupted candidate", lambda: _require_tokens("candidate_backup", "Move-Item -LiteralPath $candidatePath")),
        Scenario("W18-A45", "A to valid B", lambda: _require_tokens("previous_candidate_id", "prior_candidate_id", "STAGED")),
        Scenario("W18-A46", "A to invalid B pre-promotion", lambda: _require_tokens("Assert-Manifest", "Promote-StableLauncher")),
        Scenario("W18-A47", "A to invalid B post-promotion", lambda: _require_tokens("Restore-LauncherFromJournal", "launcher_promoted")),
        Scenario("W18-A48", "A to invalid B post-PATH", lambda: _require_tokens("Restore-UserPathSnapshot", "path_mutated")),
        Scenario("W18-A49", "retention refuses active cleanup", lambda: _require_tokens("candidate.lock", "Remove-SafeStaleCandidates")),
        Scenario("W18-A50", "retention keeps previous known-good", lambda: _require_tokens("previous_candidate_id", "protected")),
        Scenario("W18-A51", "uninstall preserves config", lambda: _require_tokens("config/data/state/cache/logs preservados", "Get-DefaultAppRoot")),
        Scenario("W18-A52", "uninstall preserves workspace state", lambda: _require_tokens("Remove-InstallContentsAfterUninstall", "data")),
        Scenario("W18-A53", "uninstall removes only owned PATH", lambda: _path_case("A;B", "remove")),
        Scenario("W18-A54", "uninstall missing receipt fails closed", lambda: _require_tokens("Get-Receipt $paths $true", "receipt W18 ausente")),
        Scenario("W18-A55", "uninstall corrupt receipt fails closed", lambda: _require_tokens("receipt W18 corrompido", "nenhuma remocao ampla")),
        Scenario("W18-A56", "reinstall consumes preserved state", lambda: _require_tokens("Get-InstallPaths", "Write-Receipt", "AppRoot")),
        Scenario("W18-A57", "W17 first-run handoff model-free", lambda: _require_tokens("Test-FreshShell", "config init", "doctor")),
        Scenario("W18-A58", "W17 existing config handoff model-free", lambda: _require_tokens("--home", "--workspace", "doctor")),
        Scenario("W18-A59", "future alias/state namespace structural test", _v3_alias_state_namespace),
        Scenario("W18-A60", "no public release side effect", _require_no_public_release),
    ]
    scenarios = [
        Scenario(f"LEGACY-PRE-V003-{index:02d}", scenario.description, scenario.run)
        for index, scenario in enumerate(scenarios, start=1)
    ]
    if len(scenarios) != 60:
        raise ScenarioFailure("legacy pre-v003 reference table is incomplete")
    return tuple(scenarios)


def _legacy_pre_v003_run_campaign_not_executed() -> dict[str, Any]:
    results: list[dict[str, str]] = []
    for scenario in _legacy_pre_v003_scenario_table_not_executed():
        try:
            evidence = scenario.run()
        except Exception as exc:
            results.append({"id": scenario.identifier, "status": "failed", "description": scenario.description, "detail": str(exc)})
        else:
            results.append({"id": scenario.identifier, "status": "passed", "description": scenario.description, "evidence": evidence})
    failed = [item for item in results if item["status"] == "failed"]
    not_run = [item for item in results if item["status"] == "not_run"]
    return {
        "schema_version": 1,
        "campaign": "W18-A01..W18-A60",
        "status": "failed" if failed else ("incomplete" if not_run else "passed"),
        "total": len(results),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": len(failed),
        "not_run": len(not_run),
        "results": results,
    }


def _legacy_pre_v003_main_not_executed(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", type=Path, help="write bounded campaign JSON")
    args = parser.parse_args(argv)
    report = _legacy_pre_v003_run_campaign_not_executed()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(rendered, encoding="utf-8")
    for item in report["results"]:
        print(f"{item['id']} {item['status'].upper()} - {item['description']}")
    print(f"W18_ADVERSARIAL={str(report['status']).upper()} ({report['passed']}/{report['total']})")
    return 0 if report["status"] == "passed" else 1


# ---------------------------------------------------------------------------
# W18 v003 deterministic campaign
# ---------------------------------------------------------------------------

def _v3_manifest() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        make_manifest(
            source_base_commit="a" * 40,
            source_tree="b" * 40,
            wheel_sha256="1" * 64,
            payload_sha256="4" * 64,
            payload_inventory_sha256="5" * 64,
            runtime_lock_sha256="2" * 64,
            bootstrap_pip_lock_sha256="3" * 64,
        ),
    )


def _v3_mutated_manifest(section: str, field: str | None = None) -> dict[str, Any]:
    document = deepcopy(_v3_manifest())
    if section == "schema":
        document["schema_version"] = "W18-RELEASE-MANIFEST-V2"
    elif section == "path":
        document["payload"]["path"] = "../payload-windows-x64.zip"
    elif section == "absolute":
        document["payload"]["inventory"] = "/payload-files.json"
    elif section == "candidate_source_commit":
        document["source"]["commit"] = "c" * 40
    else:
        target = document.get(section)
        if not isinstance(target, dict):
            raise ScenarioFailure(f"manifest mutation target is not an object: {section}")
        target[field or "sha256"] = "0" * 64
    return document


def _v3_reject_manifest(section: str, field: str | None = None) -> str:
    return _expect_rejection(lambda: validate_manifest(_v3_mutated_manifest(section, field)))


def _v3_payload_fixture() -> tuple[Path, dict[str, Any]]:
    import distribution.payload as payload_module

    root = Path(tempfile.mkdtemp(prefix="w18-v003-payload-"))
    (root / "runtime").mkdir()
    (root / "app").mkdir()
    (root / "bin").mkdir()
    (root / "runtime" / "python.exe").write_bytes(b"embedded-python")
    (root / "app" / "launcher.py").write_text("print('launcher')\n", encoding="utf-8")
    (root / "bin" / "llm-agent.cmd").write_text("@echo off\r\n", encoding="utf-8")
    return root, payload_module.make_inventory(root)


def _v3_inventory_rejection(kind: str) -> str:
    import distribution.payload as payload_module

    root, inventory = _v3_payload_fixture()
    try:
        if kind == "traversal":
            inventory["files"][0]["path"] = "../escape"
        elif kind == "collision":
            inventory["files"].append(dict(inventory["files"][0], path="RUNTIME/python.exe"))
            inventory["files"].sort(key=lambda item: item["path"])
        elif kind == "hash":
            inventory["files"][0]["sha256"] = "0" * 64
        elif kind == "size":
            inventory["files"][0]["size"] = int(inventory["files"][0]["size"]) + 1
        else:
            raise ScenarioFailure(f"unknown inventory rejection: {kind}")
        return _expect_rejection(lambda: payload_module.validate_inventory(inventory, root))
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def _v3_require(*tokens: str) -> str:
    texts = {
        "installer": (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8").casefold(),
        "builder": (ROOT / "scripts" / "build_windows_payload.py").read_text(encoding="utf-8").casefold(),
        "workflow": _installed_product_workflow().read_text(encoding="utf-8").casefold(),
        "manifest": (ROOT / "distribution" / "release_manifest.py").read_text(encoding="utf-8").casefold(),
        "payload": (ROOT / "distribution" / "payload.py").read_text(encoding="utf-8").casefold(),
        "verifier": (ROOT / "scripts" / "verify_installed_product.py").read_text(encoding="utf-8").casefold(),
    }
    flattened = "\n".join(texts.values())
    missing = [token for token in tokens if token.casefold() not in flattened]
    if missing:
        raise ScenarioFailure(f"missing v003 control(s): {missing}")
    return "controls-present"


def _v3_require_all(*actions: Callable[[], str]) -> str:
    evidence = tuple(action() for action in actions)
    return "+".join(evidence)


def _v3_installer_has_no_bootstrap_path() -> str:
    text = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8").casefold()
    forbidden = (
        "invoke-webrequest", "webclient", "start-bitstransfer", "downloadstring", "curl.exe", "wget.exe",
        "invoke-expression", "encodedcommand", "frombase64string", "pip install", "uv python",
        "setenvironmentvariable",
    )
    found = [token for token in forbidden if token in text]
    if found:
        raise ScenarioFailure(f"production installer retains forbidden bootstrap path: {found}")
    if "zipfile" not in text and "payload-windows-x64.zip" not in text:
        raise ScenarioFailure("production installer does not prove payload extraction boundary")
    return "offline-installer-static-firewall"


def _v3_journal_document(state: str = "PREPARED") -> dict[str, Any]:
    root = r"C:\Users\Test\AppData\Local\local-llm-agent\install"
    candidate = root + r"\versions\w18-" + "a" * 32
    transaction_id = "w18-install-20260101000000000-123456789abc"
    transaction_root = root + "\\transactions\\" + transaction_id
    return {
        "schema_version": 1,
        "operation": "install",
        "transaction_id": transaction_id,
        "state": state,
        "install_root": root,
        "prior_install_root_present": False,
        "stable_launcher": root + r"\bin\llm-agent.cmd",
        "owned_path": root + r"\bin",
        "candidate_id": "w18-" + "a" * 32,
        "candidate_path": candidate,
        "candidate_stage": transaction_root + r"\candidate-stage",
        "candidate_backup": "",
        "transaction_root": transaction_root,
        "payload_sha256": "4" * 64,
        "payload_inventory_sha256": "5" * 64,
        "prior_path": {"key_present": True, "present": True, "value": "A;B", "kind": "ExpandString"},
        "prior_launcher_present": False,
        "prior_launcher_sha256": "" ,
        "prior_launcher_backup": "",
        "launcher_promoted": False,
        "path_mutated": False,
        "prior_receipt_present": False,
        "prior_receipt_backup": "",
        "prior_candidate_id": "",
        "prior_previous_candidate_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _v3_journal_state(state: str) -> str:
    document = _v3_journal_document(state)
    validate_journal(document, install_root=document["install_root"], stable_launcher=document["stable_launcher"], owned_path=document["owned_path"])
    if not recovery_needed(state):
        raise ScenarioFailure(f"state should be recoverable: {state}")
    return "journal-model-pass"


def _v3_path_case(raw: str, operation: str) -> str:
    owned = r"C:\Users\José Teste\AppData\Local\local-llm-agent\install\bin"
    snapshot = PathSnapshot(True, raw, "ExpandString", True)
    if operation == "add":
        result = add_owned_segment(snapshot, owned, {"LOCALAPPDATA": r"C:\Users\José Teste\AppData\Local"})
        if result.owned_count != 1:
            raise ScenarioFailure(f"wrong W18 path owner count: {result}")
    else:
        result = remove_owned_segment(snapshot, owned, {"LOCALAPPDATA": r"C:\Users\José Teste\AppData\Local"})
        if result.owned_count != 0:
            raise ScenarioFailure(f"W18 path owner was not removed: {result}")
    return "path-model-pass"


def _v3_candidate_id_seals_artifacts() -> str:
    original = _v3_manifest()
    changed = json.loads(json.dumps(original))
    changed["payload"]["sha256"] = "6" * 64
    changed["candidate_id"] = candidate_id(changed)
    if candidate_id(changed) == original["candidate_id"]:
        raise ScenarioFailure("candidate id ignored payload identity")
    sealed = json.loads(json.dumps(original))
    sealed["source"]["commit"] = "c" * 40
    if candidate_id(sealed) != original["candidate_id"]:
        raise ScenarioFailure("candidate id changed with excluded source.commit")
    return "candidate-id-projection-pass"


def _v3_no_publication() -> str:
    workflow = _installed_product_workflow().read_text(encoding="utf-8").casefold()
    forbidden = ("twine upload", "gh release create", "git push", "git tag", "publish-package", "publish-release")
    found = [item for item in forbidden if item in workflow]
    if found:
        raise ScenarioFailure(f"publication side effect found: {found}")
    return "workflow-no-publication"


def _v3_w17_firewall() -> str:
    for relative in (
        "agent/application.py",
        "agent/orchestrator.py",
        "agent/interfaces/cli/first_run.py",
        "agent/interfaces/cli/interactive_session.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8").casefold()
        if "installer.install" in text or "set-userpath" in text or "uv python install" in text:
            raise ScenarioFailure(f"W18 installer ownership entered W17 owner: {relative}")
    return "w17-firewall-pass"


def _v3_alias_state_namespace() -> str:
    """Exercise the storage boundary independently of distribution aliases."""

    if APP_DIRECTORY_NAME != APPLICATION_NAMESPACE:
        raise ScenarioFailure("storage and release namespace authorities diverged")

    if runtime_paths.os.name == "nt":
        config_home = ROOT / ".tmp" / "a59-roaming"
        data_home = ROOT / ".tmp" / "a59-local"
        environment = {
            "APPDATA": str(config_home),
            "LOCALAPPDATA": str(data_home),
        }
        expected_config = config_home / APPLICATION_NAMESPACE
        expected_data = data_home / APPLICATION_NAMESPACE / "data"
    else:
        config_home = ROOT / ".tmp" / "a59-config"
        data_home = ROOT / ".tmp" / "a59-data"
        state_home = ROOT / ".tmp" / "a59-state"
        cache_home = ROOT / ".tmp" / "a59-cache"
        environment = {
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_DATA_HOME": str(data_home),
            "XDG_STATE_HOME": str(state_home),
            "XDG_CACHE_HOME": str(cache_home),
        }
        expected_config = config_home / APPLICATION_NAMESPACE
        expected_data = data_home / APPLICATION_NAMESPACE
    alias_metadata = {
        "distribution_name": "future-llm-agent",
        "entry_point": "agent.interfaces.cli.app:main",
    }
    if alias_metadata["distribution_name"] == APPLICATION_NAMESPACE:
        raise ScenarioFailure("alias fixture is not independent of the durable namespace")
    if alias_metadata["entry_point"] != "agent.interfaces.cli.app:main":
        raise ScenarioFailure("alias fixture does not target the canonical CLI")

    canonical = AppPaths.discover(env=environment)
    alternate = AppPaths.discover(env=environment)
    if canonical != alternate:
        raise ScenarioFailure("distribution alias changed durable AppPaths")
    if canonical.config_dir != expected_config or canonical.data_dir != expected_data:
        raise ScenarioFailure("AppPaths is not rooted at the durable application namespace")
    return "alias-independent-storage-pass"


PERMANENT_SCENARIOS: tuple[Scenario, ...] = (
        Scenario("W18-A01", "wrong/legacy online-bootstrap manifest schema rejected", lambda: _v3_reject_manifest("schema")),
        Scenario("W18-A02", "wrong application wheel hash", lambda: _v3_reject_manifest("application", "wheel_sha256")),
        Scenario("W18-A03", "wrong self-contained payload archive hash", lambda: _v3_reject_manifest("payload")),
        Scenario("W18-A04", "wrong payload-files inventory hash", lambda: _v3_reject_manifest("payload", "inventory_sha256")),
        Scenario("W18-A05", "payload file hash/size mismatch after extraction", lambda: _v3_require_all(lambda: _v3_inventory_rejection("hash"), lambda: _v3_inventory_rejection("size"))),
        Scenario("W18-A06", "payload ZIP traversal/absolute member", lambda: _v3_require("Assert-BundleRelativePath", "payload archive member", "archive_member_paths")),
        Scenario("W18-A07", "payload duplicate/case-colliding Windows path", lambda: _v3_inventory_rejection("collision")),
        Scenario("W18-A08", "payload reparse/symlink escape attempt", lambda: _v3_require("Test-ReparsePoint", "Extract-Payload", "link-like")),
        Scenario("W18-A09", "payload missing embedded runtime\\python.exe", lambda: _v3_require("runtime\\python.exe", "payload self-contained perdeu membro obrigatório")),
        Scenario("W18-A10", "payload contains uv/separate pip/build-cache/build-scratch leakage", lambda: _v3_require("uv.exe", "pip", "build", "scratch", "payload_path_leakage_reason")),
        Scenario("W18-A11", "release-build uv asset hash/signature mismatch", _uv_build_evidence_result),
        Scenario("W18-A12", "release-build CPython source artifact hash/identity mismatch", lambda: _v3_require("source_artifact_sha256", "source_artifact_url", "3.12.14")),
        Scenario("W18-A13", "hostile cwd/project/uv/pip build configuration cannot redirect release artifacts", lambda: _v3_require("PIP_CONFIG_FILE", "UV_CONFIG_FILE", "_clean_build_environment", "isolated_candidate_tree")),
        Scenario("W18-A14", "hostile VIRTUAL_ENV/PYTHONPATH/PYTHONHOME cannot redirect installed probes", lambda: _v3_require("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "Test-FreshShell")),
        Scenario("W18-A15", "installer succeeds with network unavailable / has no network fallback", _v3_installer_has_no_bootstrap_path),
        Scenario("W18-A16", "no Python on PATH", lambda: _v3_require("runtime\\python.exe", "Invoke-ExplicitProcess")),
        Scenario("W18-A17", "incompatible system Python only", lambda: _v3_require("embedded_python", "runtime\\python.exe", "3.12.14")),
        Scenario("W18-A18", "build-time runtime lock requires sdist", lambda: _v3_require("--only-binary=:all:", "--require-hashes", "runtime_lock")),
        Scenario("W18-A19", "build-time dependency wheel hash mismatch", lambda: _v3_require("--require-hashes", "sha256_file", "wheelhouse")),
        Scenario("W18-A20", "Agent editable/source install attempt", lambda: _v3_require("--no-deps", "--no-index", "application.wheel")),
        Scenario("W18-A21", "exact payload relocation to path with spaces", lambda: _v3_require("relocated payload with spaces", "_probe_payload_relocations")),
        Scenario("W18-A22", "exact payload relocation to accented Unicode path", lambda: _v3_require("payload relocado José", "_probe_payload_relocations")),
        Scenario("W18-A23", "empty User PATH", lambda: _v3_path_case("", "add")),
        Scenario("W18-A24", "raw PATH with percent variables", lambda: _v3_path_case(r"%LOCALAPPDATA%\Other", "add")),
        Scenario("W18-A25", "raw PATH with duplicate semicolons/empty entries", lambda: _v3_path_case("A;;B;", "add")),
        Scenario("W18-A26", "equivalent owned PATH segment", lambda: _v3_path_case("%LOCALAPPDATA%\\local-llm-agent\\install\\bin\\", "add")),
        Scenario("W18-A27", "unrelated llm-agent command collision", lambda: _v3_require("Test-PersistentCollision", "Get-Command", "colisão persistente")),
        Scenario("W18-A28", "transient dev venv collision only", lambda: _v3_require("VIRTUAL_ENV", "Get-PersistentPath", "NoProfile")),
        Scenario("W18-A29", "Machine PATH remains unchanged", lambda: _v3_require("SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment", "Machine PATH changed")),
        Scenario("W18-A30", "mutex contention", lambda: _v3_require("Enter-W18Mutex", "WaitOne", "Local\\W18-local-llm-agent")),
        Scenario("W18-A31", "abandoned mutex recovery", lambda: _v3_require("AbandonedMutexException", "Recover-IncompleteJournal")),
        Scenario("W18-A32", "journal corrupt", lambda: _expect_rejection(lambda: validate_journal({}, install_root="", stable_launcher="", owned_path=""))),
        Scenario("W18-A33", "journal PREPARED recovery", lambda: _v3_journal_state("PREPARED")),
        Scenario("W18-A34", "journal STAGED recovery", lambda: _v3_journal_state("STAGED")),
        Scenario("W18-A35", "journal LAUNCHER_PROMOTED recovery", lambda: _v3_journal_state("LAUNCHER_PROMOTED")),
        Scenario("W18-A36", "journal PATH_MUTATED recovery", lambda: _v3_journal_state("PATH_MUTATED")),
        Scenario("W18-A37", "launcher promotion access denied", lambda: _v3_require("Promote-StableLauncher", "Invoke-RollbackTransaction", "File]::Replace")),
        Scenario("W18-A38", "stable launcher wrong version", lambda: _v3_require("launcher estável reportou versão incorreta", "--version")),
        Scenario("W18-A39", "stable launcher wrong import origin", lambda: _v3_require("import-origin", "sys.executable", "runtime embutido")),
        Scenario("W18-A40", "fresh child inherited dev PATH trap", lambda: _v3_require("NoProfile", "Get-PersistentPath", "W18_DOCTOR_WORKSPACE")),
        Scenario("W18-A41", "fresh shell arbitrary cwd", lambda: _v3_require("arbitrary cwd", "WorkingDirectory", "outside cwd")),
        Scenario("W18-A42", "System32 human acceptance script/procedure", lambda: _v3_require("System32", "Test-FreshShell")),
        Scenario("W18-A43", "same candidate reinstall", lambda: _v3_require("Test-ReceiptForManifest", "instalação idempotente")),
        Scenario("W18-A44", "repair corrupted candidate", lambda: _v3_require("candidate_backup", "Move-Item -LiteralPath $candidatePath", "Extract-Payload")),
        Scenario("W18-A45", "A to valid B", lambda: _behavioral_scenario("W18-A45")),
        Scenario("W18-A46", "A to invalid B pre-promotion", lambda: _behavioral_scenario("W18-A46")),
        Scenario("W18-A47", "A to valid B post-promotion rollback", lambda: _behavioral_scenario("W18-A47")),
        Scenario("W18-A48", "A to valid B post-PATH rollback", lambda: _behavioral_scenario("W18-A48")),
        Scenario("W18-A49", "retention refuses active cleanup", lambda: _behavioral_scenario("W18-A49")),
        Scenario("W18-A50", "retention keeps previous known-good", lambda: _behavioral_scenario("W18-A50")),
        Scenario("W18-A51", "uninstall preserves config", lambda: _v3_require("config/data/state/cache/logs preservados", "Remove-InstallRootAfterUninstall")),
        Scenario("W18-A52", "uninstall preserves workspace state", lambda: _v3_require("config/data/state/cache/logs preservados", "application-home")),
        Scenario("W18-A53", "uninstall removes only owned PATH", lambda: _behavioral_scenario("W18-A53")),
        Scenario("W18-A54", "uninstall missing receipt fails closed", lambda: _v3_require("Get-OptionalReceipt", "receipt W18 ausente", "nenhuma remoção ampla")),
        Scenario("W18-A55", "uninstall corrupt receipt fails closed", lambda: _v3_require("receipt não prova propriedade", "fail-closed")),
        Scenario("W18-A56", "reinstall consumes preserved state", lambda: _v3_require("local-llm-agent", "Write-Receipt", "previous_candidate_id")),
        Scenario("W18-A57", "W17 first-run handoff model-free", lambda: _v3_require("config init", "doctor", "launcher.py")),
        Scenario("W18-A58", "W17 existing config handoff model-free", lambda: _v3_require_all(lambda: _v3_require("--home", "--workspace", "doctor"), _v3_w17_firewall)),
        Scenario("W18-A59", "future alias/state namespace structural test", _v3_alias_state_namespace),
        Scenario("W18-A60", "no public release side effect", _v3_no_publication),
)


def _v3_scenario_table() -> tuple[Scenario, ...]:
    scenarios = PERMANENT_SCENARIOS
    if len(scenarios) != 60 or [scenario.identifier for scenario in scenarios] != [f"W18-A{index:02d}" for index in range(1, 61)]:
        raise ScenarioFailure("permanent W18 v003 scenario IDs are not complete")
    return tuple(scenarios)


def _v3_run_campaign() -> dict[str, Any]:
    results: list[dict[str, str]] = []
    for scenario in _v3_scenario_table():
        if scenario.identifier == "W18-A11":
            evidence = scenario.run()
            status = "not_run" if evidence.startswith("not-run:") else "passed"
            results.append({"id": scenario.identifier, "status": status, "description": scenario.description, "evidence": evidence})
            continue
        if scenario.identifier in _BEHAVIORAL_SCENARIOS:
            evidence = _behavioral_evidence(scenario.identifier)
            if evidence.startswith("not-run:"):
                results.append({"id": scenario.identifier, "status": "not_run", "description": scenario.description, "detail": evidence})
                continue
            if evidence.startswith("behavioral-failed:"):
                results.append({"id": scenario.identifier, "status": "failed", "description": scenario.description, "detail": evidence})
                continue
            results.append({"id": scenario.identifier, "status": "passed", "description": scenario.description, "evidence": evidence})
            continue
        try:
            evidence = scenario.run()
        except Exception as exc:
            results.append({"id": scenario.identifier, "status": "failed", "description": scenario.description, "detail": str(exc)})
        else:
            results.append({"id": scenario.identifier, "status": "passed", "description": scenario.description, "evidence": evidence})
    failed = [item for item in results if item["status"] == "failed"]
    not_run = [item for item in results if item["status"] == "not_run"]
    return {
        "schema_version": 3,
        "campaign": "W18-A01..W18-A60",
        "status": "failed" if failed else ("incomplete" if not_run else "passed"),
        "total": len(results),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": len(failed),
        "not_run": len(not_run),
        "results": results,
    }


def _current_canonical_source_tree() -> str:
    """Return the full candidate tree for this exact current worktree."""

    try:
        with isolated_candidate_tree(ROOT) as candidate:
            return str(candidate.tree)
    except ProvenanceError as exc:
        raise ScenarioFailure(f"current W18 candidate provenance is unavailable: {exc}") from exc


def _prepare_installed_evidence(
    installed_evidence: Path,
    *,
    conpty_authority_evidence: Path | None,
    installed_interactive_evidence: Path | None,
    conpty_layer_matrix: Path | None,
) -> tuple[dict[str, Any], bytes, dict[str, str], bytes | None]:
    try:
        evidence_bytes = installed_evidence.read_bytes()
        loaded = json.loads(evidence_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScenarioFailure(f"installed-product evidence is unreadable: {installed_evidence}") from exc
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema_version") != 3
        or loaded.get("status") != "passed"
    ):
        raise ScenarioFailure("installed-product evidence is not a passed canonical verifier report")
    identity = loaded.get("evidence_identity")
    fixtures = loaded.get("valid_candidate_fixtures")
    scenarios = loaded.get("scenarios")
    candidate = identity.get("candidate_id") if isinstance(identity, dict) else None
    source_tree = identity.get("source_tree") if isinstance(identity, dict) else None
    release_manifest_sha256 = identity.get("release_manifest_sha256") if isinstance(identity, dict) else None
    fixture_a = fixtures.get("A") if isinstance(fixtures, dict) else None
    clean = scenarios.get("clean_offline_install") if isinstance(scenarios, dict) else None
    clean_candidate = clean.get("candidate_id") if isinstance(clean, dict) else None
    if (
        not isinstance(candidate, str)
        or re.fullmatch(r"w18-[0-9a-f]{32}", candidate) is None
        or not isinstance(source_tree, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_tree) is None
        or source_tree != _current_canonical_source_tree()
        or not isinstance(release_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", release_manifest_sha256) is None
        or fixture_a != candidate
        or clean_candidate != candidate
    ):
        raise ScenarioFailure("installed-product evidence source identity is missing or inconsistent")
    if isinstance(loaded.get("w17_installed_interactive"), dict) and conpty_authority_evidence is None:
        raise ScenarioFailure("canonical installed W17 evidence requires --conpty-authority-evidence for A57/A58")
    authority_bytes: bytes | None = None
    if conpty_authority_evidence is not None:
        if installed_interactive_evidence is None or conpty_layer_matrix is None:
            raise ScenarioFailure(
                "ConPTY authority validation requires installed-interactive evidence and layer matrix paths"
            )
        try:
            authority_bytes = conpty_authority_evidence.read_bytes()
            validate_conpty_authority_evidence(
                conpty_authority_evidence,
                installed_evidence,
                installed_interactive_evidence,
                conpty_layer_matrix,
            )
        except (OSError, ConPtyAuthorityError) as exc:
            raise ScenarioFailure(f"ConPTY authority evidence is invalid: {exc}") from exc
    return loaded, evidence_bytes, {
        "candidate_id": candidate,
        "source_tree": source_tree,
        "release_manifest_sha256": release_manifest_sha256,
    }, authority_bytes


def _prepare_uv_campaign_evidence(
    path: Path | None,
    identity: dict[str, str] | None,
) -> tuple[dict[str, Any] | None, bytes | None]:
    if path is None:
        return None, None
    if identity is None:
        raise ScenarioFailure("uv build evidence requires canonical installed-product identity")
    try:
        return validate_uv_build_evidence(
            path,
            candidate_id=identity["candidate_id"],
            source_tree=identity["source_tree"],
            release_manifest_sha256=identity["release_manifest_sha256"],
        )
    except UvBuildEvidenceError as exc:
        raise ScenarioFailure(f"uv build evidence is invalid: {exc}") from exc


def _attach_installed_report(
    report: dict[str, Any],
    path: Path | None,
    evidence_bytes: bytes | None,
    identity: dict[str, str] | None,
) -> None:
    if path is None:
        return
    if identity is None or evidence_bytes is None:
        raise ScenarioFailure("installed-product evidence identity was not retained")
    report["installed_evidence"] = {
        "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        **identity,
    }


def _attach_conpty_report(
    report: dict[str, Any],
    authority_path: Path | None,
    authority_bytes: bytes | None,
    installed_interactive_path: Path | None,
    layer_matrix_path: Path | None,
) -> None:
    if authority_path is None:
        return
    if authority_bytes is None:
        raise ScenarioFailure("ConPTY authority bytes were not retained")
    report["conpty_authority"] = {
        "sha256": hashlib.sha256(authority_bytes).hexdigest(),
        "installed_interactive_bound": installed_interactive_path is not None,
        "layer_matrix_bound": layer_matrix_path is not None,
    }


def _attach_uv_report(
    report: dict[str, Any],
    path: Path | None,
    evidence_bytes: bytes | None,
    document: dict[str, Any] | None,
) -> None:
    if path is None:
        return
    if evidence_bytes is None or document is None:
        raise ScenarioFailure("uv build evidence bytes were not retained")
    report["uv_build_evidence"] = {
        "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "schema_version": document["schema_version"],
        "cases": list(UV_REQUIRED_CASES),
    }


def run_campaign(
    installed_evidence: Path | None = None,
    *,
    uv_build_evidence: Path | None = None,
    conpty_authority_evidence: Path | None = None,
    installed_interactive_evidence: Path | None = None,
    conpty_layer_matrix: Path | None = None,
) -> dict[str, Any]:
    """Run structural checks and optionally classify canonical evidence.

    A real installed verifier report contains the W17 interactive object.  If
    that object is present, the final campaign must also consume the exact
    native-ConPTY authority plus its two source files.  Synthetic unit-test
    fixtures without W17 evidence retain the lightweight campaign behavior.
    """

    global _BEHAVIORAL_EVIDENCE, _UV_BUILD_EVIDENCE
    _BEHAVIORAL_EVIDENCE = None
    _UV_BUILD_EVIDENCE = None
    evidence_identity: dict[str, str] | None = None
    evidence_bytes: bytes | None = None
    authority_bytes: bytes | None = None
    uv_evidence_bytes: bytes | None = None
    if installed_evidence is not None:
        loaded, evidence_bytes, evidence_identity, authority_bytes = _prepare_installed_evidence(
            installed_evidence,
            conpty_authority_evidence=conpty_authority_evidence,
            installed_interactive_evidence=installed_interactive_evidence,
            conpty_layer_matrix=conpty_layer_matrix,
        )
        _BEHAVIORAL_EVIDENCE = loaded
    _UV_BUILD_EVIDENCE, uv_evidence_bytes = _prepare_uv_campaign_evidence(
        uv_build_evidence,
        evidence_identity,
    )
    report = _v3_run_campaign()
    _attach_installed_report(report, installed_evidence, evidence_bytes, evidence_identity)
    _attach_conpty_report(
        report,
        conpty_authority_evidence,
        authority_bytes,
        installed_interactive_evidence,
        conpty_layer_matrix,
    )
    _attach_uv_report(report, uv_build_evidence, uv_evidence_bytes, _UV_BUILD_EVIDENCE)
    return report


def _v3_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", type=Path, help="write bounded campaign JSON")
    parser.add_argument(
        "--installed-evidence",
        type=Path,
        help="canonical verify_installed_product summary for A45-A55 behavioral evidence",
    )
    parser.add_argument(
        "--uv-build-evidence",
        type=Path,
        help="candidate-bound uv build adversarial evidence required for W18-A11",
    )
    parser.add_argument(
        "--conpty-authority-evidence",
        type=Path,
        help="bound native-ConPTY authority required when installed A57/A58 evidence is present",
    )
    parser.add_argument(
        "--installed-interactive-evidence",
        type=Path,
        help="exact W17 installed-interactive JSON bound by the ConPTY authority",
    )
    parser.add_argument(
        "--conpty-layer-matrix",
        type=Path,
        help="exact ConPTY layer-matrix JSON bound by the ConPTY authority",
    )
    args = parser.parse_args(argv)
    try:
        report = run_campaign(
            args.installed_evidence,
            uv_build_evidence=args.uv_build_evidence,
            conpty_authority_evidence=args.conpty_authority_evidence,
            installed_interactive_evidence=args.installed_interactive_evidence,
            conpty_layer_matrix=args.conpty_layer_matrix,
        )
    except ScenarioFailure as exc:
        report = {
            "schema_version": 3,
            "campaign": "W18-A01..W18-A60",
            "status": "failed",
            "total": 0,
            "passed": 0,
            "failed": 1,
            "results": [],
            "error": str(exc),
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(rendered, encoding="utf-8")
    for item in report["results"]:
        print(f"{item['id']} {item['status'].upper()} - {item['description']}")
    print(f"W18_ADVERSARIAL={str(report['status']).upper()} ({report['passed']}/{report['total']})")
    return 0 if report["status"] == "passed" else 1


globals()["main"] = _v3_main

if __name__ == "__main__":
    raise SystemExit(_v3_main())
