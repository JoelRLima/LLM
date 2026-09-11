"""Static structural checker and deterministic mutation gate for the release harness."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARCHITECTURE_RULES = tuple(f"PRM-A{index:02d}" for index in range(1, 87))
REQUIRED_MUTATION_ARMS = tuple(f"PRM-M{index:02d}" for index in range(1, 21))

_PUBLIC_ROOTS = ("agent", "scripts", "tests", "docs", "README.md", ".gitignore")
_REQUIRED_FILES = (
    "agent/evaluation/campaign_serialization.py",
    "agent/evaluation/campaign_progress.py",
    "agent/evaluation/real_model_preflight.py",
    "agent/evaluation/artifact_paths.py",
    "scripts/run_evaluation_campaign.py",
    "scripts/verify_installed_package.py",
)
_PROJECTION_FILE = "agent/evaluation/release_prerequisites.py"
_SCENARIO_DEFINITION_FILES = (
    "agent/evaluation/h_series_scenarios.py",
    "agent/evaluation/scenario_h12.py",
    "agent/evaluation/structured_proof_scenarios.py",
)
_NEUTRAL_FIXTURE_IDS = frozenset(
    {
        "h1-direct-and-observed",
        "fonte_h2-scalar-binding",
        "grep-nested-content-binding",
        "duplicate-args-bindings-fail-closed",
        "continuation-causal-evidence",
        "invalid-repair-no-effect",
        "empty-search-is-not-failure",
        "failed-tool-is-not-empty",
        "bounded-search-discloses-truncation",
        "false-condition-no-effect",
        "hierarchical-partial-failure",
        "code-task-validation-rollback",
        "source-destination-authority",
        "negation-scope",
        "conditional-branches",
        "implicit-grounding",
        "validation-authority",
        "invocation-resource-domains",
        "structured-positive-proof-full-consumption",
    }
)
_NEUTRAL_FIXTURE_POLICY = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_CANONICAL_PATHS = (
    "installed-acceptance.json",
    "evaluation-corrective-dry-run.json",
    "evaluation-corrective-ready.json",
    "real-model-preflight.json",
    "real-model-epoch-2.json",
    "real-model-epoch-2.partial.json",
)


def _read(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _require(findings: list[str], root: Path, relative: str) -> str:
    source = _read(root, relative)
    if not source:
        findings.append(f"missing:{relative}")
    return source


def _source_map(root: Path, findings: list[str]) -> dict[str, str]:
    relatives = (
        *_REQUIRED_FILES,
        "agent/evaluation/campaign_report.py",
        "agent/evaluation/campaign.py",
        "agent/evaluation/analysis_support.py",
        "agent/evaluation/analysis_structure.py",
        _PROJECTION_FILE,
    )
    return {relative: _require(findings, root, relative) for relative in relatives}


def _check_serializer(sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    serialization = sources[_REQUIRED_FILES[0]]
    for symbol in ("sanitize_campaign_report", "max_campaign_run_records", "_full_sequence"):
        if symbol not in serialization:
            findings.append(f"serializer:{symbol}")
    if 'key == "runs"' not in serialization or '_full_sequence(raw_value, field="runs", bound=selected_bound)' not in serialization:
        findings.append("serializer:canonical-runs-projection")
    if "return 164" in serialization or "MAX_EVIDENCE_ITEMS" in serialization:
        findings.append("serializer:generic-or-literal-campaign-bound")
    if "write_bytes_atomic" not in serialization:
        findings.append("serializer:atomic-writer")
    if "sanitize_campaign_report(report)" not in sources["agent/evaluation/analysis_support.py"]:
        findings.append("secret-scan:canonical-full-campaign")
    return findings


def _check_report(sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    report = sources["agent/evaluation/campaign_report.py"]
    if "write_campaign_report(destination, report)" not in report:
        findings.append("report:atomic-canonical-writer")
    if "validate_campaign_report" not in report or "reanalysis" not in report:
        findings.append("report:readback-reanalysis")
    projection = sources[_PROJECTION_FILE]
    for token in ("project_release_prerequisite_snapshot", "validate_release_prerequisite_projection"):
        if token not in projection:
            findings.append(f"report:prerequisite-projection:{token}")
    return findings


def _check_progress(sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    serialization = sources[_REQUIRED_FILES[0]]
    progress = sources[_REQUIRED_FILES[1]]
    campaign = sources["agent/evaluation/campaign.py"]
    for token in ("ProgressWriter", "progress_callback", "environmental", "write_progress_document"):
        if token not in progress + campaign:
            findings.append(f"progress:{token}")
    if "write_bytes_atomic" not in progress + serialization:
        findings.append("progress:atomic-owner")
    if "write_campaign_progress(path, progress)" not in progress:
        findings.append("progress:canonical-atomic-call")
    environmental_start = campaign.find("if any(record.environmental")
    environmental_end = campaign.find("continue", environmental_start)
    if environmental_start < 0 or environmental_end < 0 or "progress_callback(" not in campaign[environmental_start:environmental_end]:
        findings.append("progress:environmental-attempt")
    return findings


def _check_preflight_identity(preflight: str) -> list[str]:
    findings: list[str] = []
    for token in (
        '_candidate_checks(reasons, installed, expected_candidate, "INSTALLED_ACCEPTANCE")',
        '_candidate_checks(reasons, readiness, expected_candidate, "DETERMINISTIC_READINESS")',
    ):
        if token not in preflight:
            findings.append(f"preflight:{token}")
    return findings


def _check_preflight_safety(preflight: str) -> list[str]:
    findings: list[str] = []
    if any(token in preflight for token in ("OpenAICompatibleGateway", ".complete(", ".stream(", "requests.")):
        findings.append("preflight:provider-or-network-call")
    return findings


def _check_cli_mode(cli: str) -> list[str]:
    findings: list[str] = []
    if "real-model-preflight" not in cli or "build_real_model_preflight" not in cli:
        findings.append("cli:preflight-mode")
    if "return 0 if release_verdict == \"RELEASE_READY\" else 1" not in cli:
        findings.append("cli:truthful-live-exit")
    return findings


def _check_cli_order(cli: str) -> list[str]:
    findings: list[str] = []
    preflight_index = cli.find("preflight = build_real_model_preflight")
    provider_index = cli.find("from agent.llm.providers")
    if preflight_index < 0 or provider_index < 0 or provider_index < preflight_index:
        findings.append("cli:preflight-before-provider")
    return findings


def _check_cli_paths(cli: str) -> list[str]:
    findings: list[str] = []
    if 'else paths.real_model_epoch_2\n        if arguments.mode == "live-model"' not in cli:
        findings.append("cli:live-output-owner")
    if "LIVE_OUTPUT_RESERVED_PATH" not in cli or "reserved_live_artifact_paths" not in cli:
        findings.append("cli:live-output-collision")
    if "live_owned_artifact_paths" not in cli or "resolve_output_path" not in cli:
        findings.append("cli:live-owned-path-policy")
    if "output_path=selected_output" not in cli or "progress_path=progress_path" not in cli:
        findings.append("cli:canonical-output-forwarding")
    return findings


def _check_preflight_and_cli(sources: dict[str, str]) -> list[str]:
    preflight = sources[_REQUIRED_FILES[2]]
    cli = sources[_REQUIRED_FILES[4]]
    findings: list[str] = []
    for checker in (
        lambda: _check_preflight_identity(preflight),
        lambda: _check_preflight_safety(preflight),
        lambda: _check_cli_mode(cli),
        lambda: _check_cli_order(cli),
        lambda: _check_cli_paths(cli),
    ):
        findings.extend(checker())
    return findings


def _check_installed_and_paths(root: Path, sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    installed = sources[_REQUIRED_FILES[5]]
    paths = sources[_REQUIRED_FILES[3]]
    if '"wheel_sha256": wheel_sha256' not in installed:
        findings.append("installed:wheel-digest")
    if '"candidate_identity": selected_identity' not in installed:
        findings.append("installed:candidate-binding")
    if "/.agent-local/" not in _read(root, ".gitignore") or "/.audit-local/" not in _read(root, ".gitignore"):
        findings.append("hygiene:root-ignores")
    for path in _CANONICAL_PATHS:
        if f'"{path}"' not in paths:
            findings.append(f"paths:missing:{path}")
    return findings


def _check_public_hygiene(root: Path, sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    public_text = _public_text(root)
    fixture_ids = _fixture_ids(root)
    if fixture_ids != _NEUTRAL_FIXTURE_IDS:
        missing = sorted(_NEUTRAL_FIXTURE_IDS - fixture_ids)
        unexpected = sorted(fixture_ids - _NEUTRAL_FIXTURE_IDS)
        if missing:
            findings.append("hygiene:fixture-whitelist-missing:" + ",".join(missing))
        if unexpected:
            findings.append("hygiene:fixture-whitelist-unexpected:" + ",".join(unexpected))
    if any(not _NEUTRAL_FIXTURE_POLICY.fullmatch(value) for value in fixture_ids):
        findings.append("hygiene:fixture-policy")
    structure = sources["agent/evaluation/analysis_structure.py"]
    if "duplicate_semantic_scenario_identity" not in structure:
        findings.append("validator:duplicate-semantic-identity")
    readme = _read(root, "README.md")
    for line in readme.splitlines():
        stripped = line.strip()
        if stripped.startswith("llm-agent run ") or stripped.startswith("llm-agent task resume "):
            if "--workspace" not in stripped:
                findings.append("docs:headless-workspace")
    if "/".join(("reports", "acceptance")) in public_text or "\\".join(("reports", "acceptance")) in public_text:
        findings.append("paths:legacy-public-artifact-root")
    return findings


def _fixture_ids(root: Path) -> set[str]:
    """Read only public scenario declarations and enforce neutral IDs by policy."""

    values: set[str] = set()
    for relative in _SCENARIO_DEFINITION_FILES:
        source = _read(root, relative)
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "HSeriesScenario":
                continue
            if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant) and isinstance(node.args[2].value, str):
                values.add(node.args[2].value)
    return values


def _check_resume(sources: dict[str, str]) -> list[str]:
    findings: list[str] = []
    campaign = sources["agent/evaluation/campaign.py"]
    if "resume_compatible(resume_report, current_resume_identity)" not in campaign:
        findings.append("resume:identity-compatibility")
    if "if target is not None and valid_repetitions >= target:" not in campaign:
        findings.append("resume:completed-repetitions-not-rerun")
    environmental_start = campaign.find("if any(record.environmental")
    environmental_end = campaign.find("continue", environmental_start)
    if environmental_start < 0 or environmental_end < 0 or "progress_callback(" not in campaign[environmental_start:environmental_end]:
        findings.append("progress:environmental-attempt")
    return sorted(set(findings))


def _check(root: Path) -> list[str]:
    findings: list[str] = []
    sources = _source_map(root, findings)
    for check in (_check_serializer, _check_report, _check_progress, _check_preflight_and_cli, _check_resume):
        findings.extend(check(sources))
    findings.extend(_check_installed_and_paths(root, sources))
    findings.extend(_check_public_hygiene(root, sources))
    return sorted(set(findings))


def _public_text(root: Path) -> str:
    chunks: list[str] = []
    for relative in _PUBLIC_ROOTS:
        path = root / relative
        if path.is_file():
            chunks.append(_read(root, relative))
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and "__pycache__" not in child.parts:
                    try:
                        chunks.append(child.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError):
                        continue
    return "\n".join(chunks)


def check_repository(root: Path = ROOT) -> list[str]:
    return _check(root.resolve())


def _copy_candidate(source: Path, destination: Path) -> None:
    for relative in ("agent", "scripts", "docs"):
        origin_dir = source / relative
        if origin_dir.exists():
            shutil.copytree(
                origin_dir,
                destination / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
    for relative in ("README.md", ".gitignore", "pyproject.toml", "setup.py", "setup.cfg"):
        origin = source / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.exists():
            shutil.copy2(origin, target)


def _replace(path: Path, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if old not in source:
        return False
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def _mutations(root: Path) -> Iterable[tuple[str, str, Callable[[Path], bool]]]:
    yield "PRM-M01", "generic top-level run projection", lambda copy: _replace(copy / _REQUIRED_FILES[0], '_full_sequence(raw_value, field="runs", bound=selected_bound)', "sanitize_evidence(raw_value)")
    yield "PRM-M02", "generic sanitizer for final report", lambda copy: _replace(
        copy / _REQUIRED_FILES[0],
        "write_bytes_atomic(destination, canonical_json_bytes(sanitize_campaign_report(report)))",
        "write_bytes_atomic(destination, canonical_json_bytes(sanitize_evidence(report)))",
    )
    yield "PRM-M03", "first-64-only secret scan", lambda copy: _replace(
        copy / "agent/evaluation/analysis_support.py",
        'rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)',
        'rendered = json.dumps({**dict(report), "runs": list(report.get("runs", ()))[:64]}, ensure_ascii=False, sort_keys=True, default=str)',
    )
    yield "PRM-M04", "literal campaign bound", lambda copy: _replace(copy / _REQUIRED_FILES[0], "return sum(len(scenario.arms) for scenario in selected) * max_campaign_attempts(policy)", "return 164")
    yield "PRM-M05", "stale installed identity", lambda copy: _replace(copy / _REQUIRED_FILES[2], '_candidate_checks(reasons, installed, expected_candidate, "INSTALLED_ACCEPTANCE")', "pass")
    yield "PRM-M06", "stale deterministic identity", lambda copy: _replace(copy / _REQUIRED_FILES[2], '_candidate_checks(reasons, readiness, expected_candidate, "DETERMINISTIC_READINESS")', "pass")
    yield "PRM-M07", "provider before preflight", lambda copy: _replace(
        copy / _REQUIRED_FILES[4],
        "    frozen_external_identity = normalize_external_identity(arguments.external_identity)\n    preflight = build_real_model_preflight(",
        "    frozen_external_identity = normalize_external_identity(arguments.external_identity)\n"
        "    import mutation_gateway\n"
        "    mutation_gateway.record(\"gateway\")\n"
        "    preflight = build_real_model_preflight(",
    )
    yield "PRM-M08", "divergent installed artifact path", lambda copy: _replace(copy / _REQUIRED_FILES[3], '"installed-acceptance.json"', '"evaluation-installed-acceptance.json"')
    yield "PRM-M09", "missing wheel digest", lambda copy: _replace(copy / _REQUIRED_FILES[5], '"wheel_sha256": wheel_sha256', '"wheel_digest": wheel_sha256')
    yield "PRM-M10", "always-zero live verdict", lambda copy: _replace(copy / _REQUIRED_FILES[4], 'return 0 if release_verdict == "RELEASE_READY" else 1', "return 0")
    yield "PRM-M11", "live deterministic output collision", lambda copy: _replace(
        copy / _REQUIRED_FILES[4],
        'else paths.real_model_epoch_2\n        if arguments.mode == "live-model"',
        'else paths.corrective_dry_run\n        if arguments.mode == "live-model"',
    )
    yield "PRM-M12", "direct progress write", lambda copy: _replace(copy / _REQUIRED_FILES[1], "write_campaign_progress(path, progress)", "Path(path).write_text(json.dumps(progress), encoding=\"utf-8\")")
    yield "PRM-M13", "environmental progress omission", lambda copy: _replace(
        copy / "agent/evaluation/campaign.py",
        """            if progress_callback is not None:
                progress_callback(
                    scenario.h_id,
                    marked_records,
                    _scenario_summary(
                        scenario,
                        scenario_results,
                        valid_repetitions,
                        prior_summary,
                        "environmental_attempt_pending",
                        environmental_increment=environmental_attempts,
                    ),
                )
""",
        """            if False:
                pass
""",
    )
    yield "PRM-M14", "identity-free resume", lambda copy: _replace(copy / "agent/evaluation/campaign.py", "resume_compatible(resume_report, current_resume_identity)", "True")
    yield "PRM-M15", "valid repetition rerun", lambda copy: _replace(
        copy / "agent/evaluation/campaign.py",
        "        return records, _scenario_summary(",
        "        target = valid_repetitions + 1\n        if False:\n            return records, _scenario_summary(",
    )
    yield "PRM-M16", "duplicate semantic identity", lambda copy: _replace(
        copy / "agent/evaluation/analysis_structure.py",
        """    if key in seen_semantic:
        errors.append(f"duplicate_semantic_scenario_identity:{h_id}:{arm_id}:{repetition}")
""",
        """    if key in seen_semantic:
        pass
""",
    )
    yield "PRM-M17", "missing local ignores", lambda copy: _replace(copy / ".gitignore", "/.agent-local/", "/.agent-local-disabled/")
    yield "PRM-M18", "unapproved fixture identifier", lambda copy: _replace(copy / "agent/evaluation/h_series_scenarios.py", "source-destination-authority", "fixture-not-approved")
    yield "PRM-M19", "headless example without workspace", lambda copy: _replace(copy / "README.md", "llm-agent run --workspace", "llm-agent run")
    yield "PRM-M20", "provider call in preflight", lambda copy: _replace(
        copy / _REQUIRED_FILES[2],
        "    root = Path(repo_root).resolve()\n",
        "    import mutation_gateway\n"
        "    mutation_gateway.record(\"provider\")\n"
        "    root = Path(repo_root).resolve()\n",
    )


_MUTATION_OWNERS = {
    "PRM-M01": "agent/evaluation/campaign_serialization.py",
    "PRM-M02": "agent/evaluation/campaign_serialization.py",
    "PRM-M03": "agent/evaluation/analysis_support.py",
    "PRM-M04": "agent/evaluation/campaign_serialization.py",
    "PRM-M05": "agent/evaluation/real_model_preflight.py",
    "PRM-M06": "agent/evaluation/real_model_preflight.py",
    "PRM-M07": "scripts/run_evaluation_campaign.py",
    "PRM-M08": "agent/evaluation/artifact_paths.py",
    "PRM-M09": "scripts/verify_installed_package.py",
    "PRM-M10": "scripts/run_evaluation_campaign.py",
    "PRM-M11": "scripts/run_evaluation_campaign.py",
    "PRM-M12": "agent/evaluation/campaign_serialization.py",
    "PRM-M13": "agent/evaluation/campaign.py",
    "PRM-M14": "agent/evaluation/campaign.py",
    "PRM-M15": "agent/evaluation/campaign.py",
    "PRM-M16": "agent/evaluation/analysis_structure.py",
    "PRM-M17": ".gitignore",
    "PRM-M18": "agent/evaluation/h_series_scenarios.py",
    "PRM-M19": "README.md",
    "PRM-M20": "agent/evaluation/real_model_preflight.py",
}

_MUTATION_SEMANTICS = {
    "PRM-M01": ("restore generic top-level runs truncation", "bounded campaign round-trip"),
    "PRM-M02": ("write final report through generic sanitizer that truncates runs", "final report round-trip"),
    "PRM-M03": ("scan only first 64 runs for secrets", "late-run secret scan"),
    "PRM-M04": ("replace derived campaign hard bound with literal 164", "derived campaign bound checker"),
    "PRM-M05": ("accept stale installed candidate identity", "installed identity preflight"),
    "PRM-M06": ("accept stale deterministic candidate identity", "deterministic identity preflight"),
    "PRM-M07": ("invoke gateway before preflight", "instrumented gateway ordering"),
    "PRM-M08": ("restore divergent installed artifact path", "canonical artifact path owner"),
    "PRM-M09": ("drop wheel SHA from installed summary", "installed provenance schema"),
    "PRM-M10": ("make live CLI always return 0 after campaign completion", "truthful live exit"),
    "PRM-M11": ("restore live default to deterministic dry-run filename", "live default path owner"),
    "PRM-M12": ("make progress write non-atomic/direct", "atomic progress writer"),
    "PRM-M13": ("skip progress write for environmental attempts", "environmental progress evidence"),
    "PRM-M14": ("ignore candidate identity during resume", "resume identity compatibility"),
    "PRM-M15": ("rerun already-valid repetition after resume", "resume call log"),
    "PRM-M16": ("permit duplicate semantic run identity", "duplicate identity validator"),
    "PRM-M17": ("remove root ignore for local audit material", "publication ignore policy"),
    "PRM-M18": ("restore one non-neutral fixture identifier", "fixture whitelist policy"),
    "PRM-M19": ("restore headless README directive without workspace", "headless documentation contract"),
    "PRM-M20": ("make preflight instantiate/call a live provider", "zero-call preflight"),
}

_BEHAVIORAL_MUTANTS = frozenset(
    {
        "PRM-M01",
        "PRM-M02",
        "PRM-M03",
        "PRM-M05",
        "PRM-M06",
        "PRM-M07",
        "PRM-M10",
        "PRM-M11",
        "PRM-M12",
        "PRM-M14",
        "PRM-M15",
        "PRM-M16",
        "PRM-M20",
    }
)


def _behavioral_probe_code(arm_id: str) -> str:
    """Return one deterministic, no-network detector for a mutation arm."""

    return textwrap.dedent(
        f"""
        import contextlib
        import copy
        import io
        import json
        import tempfile
        from pathlib import Path

        ARM = {arm_id!r}

        def fail(message):
            raise AssertionError(f"{{ARM}}:{{message}}")

        if ARM in {{"PRM-M01", "PRM-M02"}}:
            from agent.evaluation.campaign_serialization import (
                sanitize_campaign_report,
                write_campaign_report,
            )
            report = {{
                "runs": [{{"h_id": "H1", "evidence": {{}}}} for _ in range(164)],
                "scenario_results": [],
            }}
            if ARM == "PRM-M01":
                observed = sanitize_campaign_report(report)
            else:
                with tempfile.TemporaryDirectory() as raw:
                    destination = Path(raw) / "campaign.json"
                    write_campaign_report(destination, report)
                    observed = json.loads(destination.read_text(encoding="utf-8"))
            if len(observed["runs"]) != 164:
                fail("campaign runs were truncated")

        elif ARM == "PRM-M03":
            from agent.evaluation.analysis_support import secret_safe_report
            report = {{
                "runs": [{{"h_id": "H1", "evidence": {{}}}} for _ in range(164)],
                "scenario_results": [],
            }}
            report["runs"][-1]["evidence"]["final_answer"] = "token=TOPSECRET-LATE"
            observed = secret_safe_report(report)
            if observed["pass"] or observed["scanned_run_count"] != 164:
                fail("late secret was not detected")

        elif ARM in {{"PRM-M05", "PRM-M06"}}:
            import agent.evaluation.real_model_preflight as preflight_module
            from agent.evaluation.evaluation_identity import (
                candidate_identity,
                candidate_identity_string,
                fixture_identity,
                model_config_identity,
            )
            from agent.evaluation.scenario_contracts import H_SERIES_VERSION, RepetitionPolicy
            preflight_module.validate_release_prerequisite_projection = lambda value: ()
            preflight_module.deterministic_readiness_issues = lambda value: ()
            # The isolated mutation copy deliberately has no .git directory.
            # Keep this probe about stale artifact identity, not repository lookup.
            isolated_candidate = {{
                "head": "head",
                "semantic_candidate_fingerprint": "semantic",
                "semantic_manifest_hash": "manifest",
            }}
            preflight_module.candidate_identity = lambda root: dict(isolated_candidate)
            candidate = dict(isolated_candidate)
            candidate_id = candidate_identity_string(candidate)
            model = model_config_identity(".", profile_name="local_8gb", evidence_level="real_model")
            installed = {{
                "schema_version": 2,
                "status": "passed",
                "acceptance": True,
                "mode": "clean-acceptance",
                "clean": True,
                "evidence_level": "installed_deterministic",
                "candidate": dict(candidate),
                "candidate_identity": candidate_id,
                "semantic_manifest_hash": candidate["semantic_manifest_hash"],
                "wheel_sha256": "a" * 64,
                "task_files_in_wheel": False,
            }}
            readiness = {{
                "schema_version": "CORRECTIVE-READINESS-V1.0",
                "ready": True,
                "reason_codes": [],
                "campaign_started": False,
                "candidate": dict(candidate),
                "candidate_identity": candidate_id,
                "semantic_manifest_hash": candidate["semantic_manifest_hash"],
                "fixture_identity": fixture_identity(),
                "h_series_version": H_SERIES_VERSION,
                "epoch": "REAL-MODEL-EPOCH-2",
                "repetition_policy": RepetitionPolicy().to_dict(),
                "model_identity_schema": {{"model_config_fingerprint": model["model_config_fingerprint"]}},
                "dry_run": {{"summary": {{}}, "analysis": {{}}}},
            }}
            if ARM == "PRM-M05":
                installed["candidate_identity"] = "stale"
                expected = "INSTALLED_ACCEPTANCE_IDENTITY_MISMATCH"
            else:
                readiness["candidate_identity"] = "stale"
                expected = "DETERMINISTIC_READINESS_IDENTITY_MISMATCH"
            observed = preflight_module.validate_real_model_preflight(
                ".",
                installed_acceptance=installed,
                deterministic_readiness=readiness,
            )
            if observed["ready"] or expected not in observed["reason_codes"]:
                fail("stale identity was accepted")

        elif ARM == "PRM-M07":
            import mutation_gateway
            import scripts.run_evaluation_campaign as cli
            cli._load_live_resume = lambda *args, **kwargs: (None, None)
            cli.build_real_model_preflight = lambda *args, **kwargs: {{
                "ready": False,
                "reason_codes": ["blocked"],
                "candidate_identity": "candidate",
            }}
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = cli.main([
                    "--mode", "live-model", "--qwen-loaded", "--output", "live-probe.json"
                ])
            if mutation_gateway.events:
                fail("gateway was invoked before preflight")
            if result != 2:
                fail("ordering probe did not stop at preflight")

        elif ARM == "PRM-M08":
            from agent.evaluation.artifact_paths import canonical_artifact_paths
            if canonical_artifact_paths(".").installed_acceptance.name != "installed-acceptance.json":
                fail("installed acceptance path diverged")

        elif ARM == "PRM-M10":
            import scripts.run_evaluation_campaign as cli
            cli._load_live_resume = lambda *args, **kwargs: (None, None)
            cli.build_real_model_preflight = lambda *args, **kwargs: {{
                "ready": True,
                "candidate_identity": "candidate",
                "prerequisite_snapshots": {{}},
            }}
            cli.run_real_model_campaign = lambda *args, **kwargs: {{
                "candidate_identity": "candidate",
                "analysis": {{
                    "release_verdict": "NOT_RELEASE_READY_RUNTIME",
                    "reason_codes": ["instrumented"],
                }},
            }}
            result = cli.main([
                "--mode", "live-model", "--qwen-loaded", "--output", "live-probe.json"
            ])
            if result != 1:
                fail("non-ready live verdict returned success")

        elif ARM == "PRM-M11":
            import scripts.run_evaluation_campaign as cli
            cli._load_live_resume = lambda *args, **kwargs: (None, None)
            cli.build_real_model_preflight = lambda *args, **kwargs: {{
                "ready": False,
                "reason_codes": ["PRECONDITION"],
                "candidate_identity": "candidate",
            }}
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = cli.main(["--mode", "live-model", "--qwen-loaded"])
            output = stream.getvalue()
            if result != 2 or "PRECONDITION" not in output or "LIVE_OUTPUT_RESERVED_PATH" in output:
                fail("live default is not the canonical live owner")

        elif ARM == "PRM-M12":
            import agent.evaluation.campaign_progress as progress_module
            import agent.evaluation.campaign_serialization as serialization
            serialization.sanitize_campaign_progress = lambda value: value
            serialization.write_bytes_atomic = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("atomic-owner")
            )
            with tempfile.TemporaryDirectory() as raw:
                try:
                    progress_module.write_progress_document(
                        Path(raw) / "progress.json",
                        {{"runs_so_far": [], "scenario_results_so_far": []}},
                    )
                except AssertionError as exc:
                    if str(exc) != "atomic-owner":
                        fail("unexpected writer failure")
                else:
                    fail("progress bypassed atomic owner")

        elif ARM == "PRM-M14":
            from agent.evaluation import campaign

            # The isolated mutation copy deliberately has no .git directory.
            # Keep this probe about resume compatibility, not repository lookup.
            campaign.candidate_identity = lambda root: {{
                "head": "head",
                "semantic_candidate_fingerprint": "semantic",
                "semantic_manifest_hash": "manifest",
            }}

            class Sentinel(Exception):
                pass

            def gateway(*args, **kwargs):
                raise Sentinel("gateway reached after resume check")

            try:
                campaign.run_scripted_campaign(
                    ".",
                    resume_report={{"candidate_identity": "stale"}},
                    gateway_factory=gateway,
                )
            except campaign.CampaignExecutionError:
                pass
            except Sentinel:
                fail("identity-free resume reached gateway")
            else:
                fail("identity mismatch was accepted")

        elif ARM == "PRM-M15":
            from agent.evaluation import campaign
            from agent.evaluation.evaluation_identity import fake_model_identity
            from agent.evaluation.scenario_contracts import H_SERIES, EvidenceLevel, RepetitionPolicy

            class Sentinel(Exception):
                pass

            def gateway(*args, **kwargs):
                raise Sentinel("valid repetition was rerun")

            scenario = next(item for item in H_SERIES if item.h_id == "H1")
            try:
                records, summary = campaign._run_scenario(
                    scenario,
                    policy=RepetitionPolicy(),
                    gateway_factory=gateway,
                    candidate={{"head": "h", "semantic_candidate_fingerprint": "s", "semantic_manifest_hash": "m"}},
                    epoch="epoch",
                    evidence_level=EvidenceLevel.DETERMINISTIC,
                    model_identity=fake_model_identity(),
                    existing_summary={{
                        "scenario_repetitions": 3,
                        "scenario_results": [{{"passed": True}} for _ in range(3)],
                    }},
                )
            except Sentinel:
                fail("valid repetition was rerun")
            if records or summary.get("scenario_repetitions") != 3:
                fail("completed repetition state changed")

        elif ARM == "PRM-M16":
            # Import through the public analyzer first so the split identity /
            # structure modules complete their intentional import cycle.
            import agent.evaluation.analysis
            from agent.evaluation.analysis_structure import _campaign_structure_errors
            from agent.evaluation.scenario_contracts import H_SERIES

            scenario = next(item for item in H_SERIES if item.h_id == "H1")
            arm = scenario.arms[0]
            run = {{
                "h_id": "H1",
                "arm_id": arm.arm_id,
                "repetition": 1,
                "passed": True,
                "valid_repetition": True,
                "evidence": {{
                    "scenario_id": f"H1-{{arm.arm_id}}",
                    "epoch": "e",
                    "candidate_identity": "c",
                    "model_config_fingerprint": "m",
                    "scenario_set_version": "H-SERIES-V1.5",
                    "declared_model_identity": {{}},
                    "initial_fixture_digest": "x",
                    "objective": "x",
                    "observed_model_identity": {{}},
                    "model_call_identities": [],
                    "scenario_repetition": 1,
                }},
            }}
            errors = _campaign_structure_errors(
                {{"epoch": "e", "candidate_identity": "c", "model_config_fingerprint": "m"}},
                [run, copy.deepcopy(run)],
            )
            if not any("duplicate_semantic_scenario_identity" in item for item in errors):
                fail("duplicate semantic identity was accepted")

        elif ARM == "PRM-M20":
            import mutation_gateway
            import agent.evaluation.real_model_preflight as preflight_module
            preflight_module.candidate_identity = lambda root: {{
                "head": "head",
                "semantic_candidate_fingerprint": "semantic",
                "semantic_manifest_hash": "manifest",
            }}
            validate_real_model_preflight = preflight_module.validate_real_model_preflight
            validate_real_model_preflight(
                ".",
                installed_acceptance={{}},
                deterministic_readiness={{}},
            )
            if mutation_gateway.events:
                fail("preflight invoked provider boundary")

        else:
            raise AssertionError(f"unsupported behavioral arm: {{ARM}}")
        """
    )


def _run_behavioral_probe(root: Path, candidate: Path, arm_id: str) -> tuple[bool, str]:
    if arm_id in {"PRM-M07", "PRM-M20"}:
        (candidate / "mutation_gateway.py").write_text(
            "events = []\n\ndef record(kind):\n    events.append(kind)\n",
            encoding="utf-8",
        )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(candidate), str(root)) if item
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _behavioral_probe_code(arm_id)],
            cwd=candidate,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"probe-launch:{type(exc).__name__}:{exc}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, output[-8_000:]


def _candidate_file_digests(root: Path) -> dict[str, str]:
    files: list[Path] = []
    for relative in ("agent", "scripts", "docs"):
        directory = root / relative
        if directory.exists():
            files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
    for relative in ("README.md", ".gitignore", "pyproject.toml", "setup.py", "setup.cfg"):
        path = root / relative
        if path.is_file():
            files.append(path)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def _write_mutation_ledger(root: Path, entries: list[dict[str, object]]) -> Path:
    preferred = root / ".audit-local" / "prm-mutation-semantic-ledger-current.json"
    requested = Path(os.environ["PRM_MUTATION_LEDGER_PATH"]) if os.environ.get("PRM_MUTATION_LEDGER_PATH") else preferred
    payload = {
        "schema_version": "PRM-MUTATION-SEMANTICS-V2",
        "baseline_checker": "PASS",
        "network_calls": 0,
        "provider_calls": 0,
        "mutants": entries,
    }
    for destination in (requested, Path(tempfile.gettempdir()) / "prm-mutation-semantic-ledger-current.json"):
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return destination
        except OSError:
            continue
    raise OSError("could not persist PRM mutation ledger in local evidence locations")


def run_mutation_campaign(root: Path = ROOT) -> dict[str, str]:
    expected_findings = {
        "PRM-M01": ("serializer:canonical-runs-projection",),
        "PRM-M02": ("report:atomic-canonical-writer",),
        "PRM-M03": ("secret-scan:canonical-full-campaign",),
        "PRM-M04": ("serializer:generic-or-literal-campaign-bound",),
        "PRM-M05": ('preflight:_candidate_checks(reasons, installed, expected_candidate, "INSTALLED_ACCEPTANCE")',),
        "PRM-M06": ('preflight:_candidate_checks(reasons, readiness, expected_candidate, "DETERMINISTIC_READINESS")',),
        "PRM-M07": ("cli:preflight-before-provider",),
        "PRM-M08": ("paths:missing:installed-acceptance.json",),
        "PRM-M09": ("installed:wheel-digest",),
        "PRM-M10": ("cli:truthful-live-exit",),
        "PRM-M11": ("cli:live-output-owner",),
        "PRM-M12": ("progress:canonical-atomic-call",),
        "PRM-M13": ("progress:environmental-attempt",),
        "PRM-M14": ("resume:identity-compatibility",),
        "PRM-M15": ("resume:completed-repetitions-not-rerun",),
        "PRM-M16": ("validator:duplicate-semantic-identity",),
        "PRM-M17": ("hygiene:root-ignores",),
        "PRM-M18": ("hygiene:fixture-whitelist-unexpected",),
        "PRM-M19": ("docs:headless-workspace",),
        "PRM-M20": ("preflight:provider-or-network-call",),
    }
    results: dict[str, str] = {}
    ledger_entries: list[dict[str, object]] = []
    for arm_id, description, mutate in _mutations(root):
        with tempfile.TemporaryDirectory(prefix=f"{arm_id.lower()}-") as raw:
            candidate = Path(raw)
            _copy_candidate(root, candidate)
            baseline_findings = _check(candidate)
            baseline_probe_ok = True
            baseline_probe_output = ""
            if arm_id in _BEHAVIORAL_MUTANTS:
                baseline_probe_ok, baseline_probe_output = _run_behavioral_probe(
                    root, candidate, arm_id
                )
            if baseline_findings or not baseline_probe_ok:
                results[arm_id] = "INVALID_BASELINE"
                ledger_entries.append(
                    {
                        "mutant_id": arm_id,
                        "frozen_fault_semantics": _MUTATION_SEMANTICS[arm_id][0],
                        "mutated_owner": _MUTATION_OWNERS[arm_id],
                        "changed": False,
                        "changed_paths": [],
                        "causal_detector": _MUTATION_SEMANTICS[arm_id][1],
                        "baseline_clean": not baseline_findings and baseline_probe_ok,
                        "baseline_output": (str(baseline_findings) + baseline_probe_output)[-8_000:],
                        "detector_output": "",
                        "status": "INVALID_BASELINE",
                    }
                )
                print(f"{arm_id} INVALID_BASELINE: {baseline_findings or baseline_probe_output}")
                continue
            before = _candidate_file_digests(candidate)
            changed = mutate(candidate)
            after = _candidate_file_digests(candidate)
            changed_paths = sorted(
                path for path in set(before) | set(after) if before.get(path) != after.get(path)
            )
            findings = _check(candidate)
            expected = expected_findings[arm_id]
            if arm_id in _BEHAVIORAL_MUTANTS:
                probe_ok, probe_output = _run_behavioral_probe(root, candidate, arm_id)
                detected = changed and len(changed_paths) == 1 and not probe_ok and arm_id in probe_output
                detector_output = probe_output
            else:
                probe_output = ""
                detected = changed and len(changed_paths) == 1 and any(
                    any(token in finding for token in expected) for finding in findings
                )
                detector_output = "\n".join(findings)
            results[arm_id] = "DETECTED" if detected else "SURVIVED"
            ledger_entries.append(
                {
                    "mutant_id": arm_id,
                    "frozen_fault_semantics": _MUTATION_SEMANTICS[arm_id][0],
                    "mutated_owner": _MUTATION_OWNERS[arm_id],
                    "changed": changed,
                    "changed_paths": changed_paths,
                    "causal_detector": _MUTATION_SEMANTICS[arm_id][1],
                    "baseline_clean": True,
                    "baseline_output": baseline_probe_output,
                    "detector_output": detector_output[-8_000:],
                    "static_findings": findings,
                    "status": results[arm_id],
                }
            )
            print(f"{arm_id} {results[arm_id]}: {description}")
    ledger_path = _write_mutation_ledger(root, ledger_entries)
    print(f"PRM mutation semantic ledger: {ledger_path}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the pre-real-model evaluation harness")
    parser.add_argument("--mutation-campaign", action="store_true")
    arguments = parser.parse_args()
    findings = check_repository()
    if findings:
        print("Pre-real-model harness checker: FAIL")
        print("\n".join(findings))
        return 1
    print("Pre-real-model harness checker: PASS")
    if not arguments.mutation_campaign:
        return 0
    results = run_mutation_campaign()
    return 0 if all(value == "DETECTED" for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
