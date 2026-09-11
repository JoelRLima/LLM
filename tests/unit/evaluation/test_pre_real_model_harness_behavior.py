"""Focused behavioral evidence for the pre-real-model harness boundary."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast

import pytest

from agent.evaluation import campaign
from agent.evaluation.analysis import analyze_campaign, secret_safe_report, validate_campaign_report
from agent.evaluation.artifact_paths import (
    canonical_artifact_paths,
    live_owned_artifact_paths,
    progress_path_for,
)
from agent.evaluation.campaign_artifacts import (
    deterministic_readiness,
    deterministic_summary_from_readiness,
)
from agent.evaluation.campaign_progress import (
    CampaignProgressError,
    ProgressWriter,
    build_progress_document,
    load_campaign_progress,
    resume_report_from_progress,
)
from agent.evaluation.campaign_report import _write_report, run_real_model_campaign
from agent.evaluation.campaign_serialization import write_campaign_report
from agent.evaluation.evaluation_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    DEFAULT_REAL_MODEL_EPOCH,
    candidate_identity,
    candidate_identity_string,
    fake_model_identity,
    fixture_identity,
    model_config_identity,
    resume_compatible,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.evaluation_snapshot_projection import snapshot_evaluation_projection
from agent.evaluation.evidence import MAX_EVIDENCE_DEPTH, sanitize_evidence
from agent.evaluation.real_model_preflight import build_real_model_preflight
from agent.evaluation.release_prerequisites import (
    project_release_prerequisite_snapshot,
    validate_release_prerequisite_projection,
)
from agent.evaluation.scenario_contracts import H_SERIES, H_SERIES_VERSION, EvidenceLevel, RepetitionPolicy
from agent.evaluation.scripted_gateway import _scripted_factory
from agent.evaluation.scripted_gateway_logic import _engineering_response
from agent.runtime.filesystem_primitives import write_bytes_atomic
from tests.unit.evaluation.test_campaign_corrective import _analysis_report

ROOT = Path(__file__).resolve().parents[3]
_LOCAL_EVIDENCE_MARKERS = (
    "prm" + "-",
    "." + "audit" + "-" + "local",
    "." + "agent" + "-" + "local",
)
_VOLATILE_KEYS = frozenset({
    "run_id",
    "root_task_id",
    "task_id",
    "parent_task_id",
    "node_id",
    "plan_id",
    "step_id",
    "_step_id",
    "from_step",
    "invocation_id",
    "correlation",
    "measurement",
    "call_identities",
    "model_call_identities",
})


def _semantic(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _campaign_semantics(report: Mapping[str, Any]) -> dict[str, Any]:
    return _semantic({
        "summary": report.get("summary"),
        "scenario_results": report.get("scenario_results"),
        "runs": report.get("runs"),
    })


def _semantic_outcomes(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in ("h_id", "arm_id", "scenario_repetition", "attempt", "passed", "valid_repetition")
        }
        | {
            "outcome": {
                key: item.get("evidence", {}).get(key)
                for key in (
                    "final_answer",
                    "terminal_status",
                    "deterministic_failures",
                    "causal_classification",
                    "causal_reason_codes",
                    "critical_incidents",
                )
            }
        }
        for item in records
    ]


def _readiness_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    analysis = analyze_campaign(report, require_final_epoch=False)
    return {
        "schema_version": "CORRECTIVE-READINESS-V1.0",
        "ready": True,
        "reason_codes": [],
        "campaign_started": False,
        "candidate": dict(report["candidate"]),
        "candidate_identity": report["candidate_identity"],
        "semantic_manifest_hash": report["semantic_manifest_hash"],
        "fixture_identity": report["fixture_identity"],
        "h_series_version": H_SERIES_VERSION,
        "epoch": report["epoch"],
        "repetition_policy": RepetitionPolicy().to_dict(),
        "model_identity_schema": dict(report["model_identity"]),
        "deterministic_readiness": {
            "recorded": True,
            "complete": True,
            "source": "test",
            "reason": "all_deterministic_gates_recorded",
            "reason_codes": [],
        },
        "dry_run": {
            "summary": dict(report["summary"]),
            "analysis": analysis,
        },
    }


def _versioned_release_prerequisite_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the deep prerequisite fixture from versioned H-series contracts."""

    report = _analysis_report()
    return copy.deepcopy(report["installed_acceptance"]), _readiness_snapshot(report)


def _source_has_local_evidence_root(source: str) -> bool:
    """Reject local-evidence path literals without maintaining an allowlist."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    literal_text = "\n".join(
        str(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return any(marker in literal_text for marker in _LOCAL_EVIDENCE_MARKERS)


def test_resume_progress_adapts_to_v2_and_is_crash_equivalent(
    tmp_path: Path,
) -> None:
    progress_path = tmp_path / "campaign-progress.json"
    scenario = next(item for item in H_SERIES if item.h_id == "H2")
    candidate = {
        "head": "head",
        "semantic_candidate_fingerprint": "semantic",
        "semantic_manifest_hash": "manifest",
    }
    model_identity = fake_model_identity()
    policy = RepetitionPolicy()
    candidate_id = candidate_identity_string(candidate)

    class CrashAfterFirstProgress(ProgressWriter):
        raised = False

        def __call__(self, h_id: str, group_records: Any, summary: Mapping[str, Any]) -> None:
            super().__call__(h_id, group_records, summary)
            if not self.raised:
                self.raised = True
                raise RuntimeError("simulated crash after atomic progress publication")

    writer = CrashAfterFirstProgress(
        progress_path,
        candidate=candidate,
        candidate_identity=candidate_id,
        semantic_manifest_hash="manifest",
        fixture_identity=fixture_identity(),
        epoch="TEST-EPOCH",
        model_identity=model_identity,
        repetition_policy=policy.to_dict(),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        campaign._run_scenario(
            scenario,
            policy=policy,
            gateway_factory=cast(Any, _scripted_factory),
            candidate=cast(Mapping[str, str], candidate),
            epoch="TEST-EPOCH",
            evidence_level=EvidenceLevel.DETERMINISTIC,
            model_identity=model_identity,
            progress_callback=writer,
        )

    persisted = load_campaign_progress(progress_path)
    resumed_envelope = resume_report_from_progress(persisted)
    assert resumed_envelope["schema_version"] == CAMPAIGN_SCHEMA_VERSION
    assert resumed_envelope["schema_version"] != persisted["schema_version"]
    assert resumed_envelope["runs"]
    assert resumed_envelope["scenario_results"]
    current = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "scenario_set_version": H_SERIES_VERSION,
        "fixture_identity": fixture_identity(),
        "epoch": "TEST-EPOCH",
        "candidate": candidate,
        "candidate_identity": candidate_id,
        "semantic_manifest_hash": "manifest",
        "model_identity": model_identity,
        "model_config_fingerprint": model_identity["model_config_fingerprint"],
        "repetition_policy": policy.to_dict(),
        "observed_model_identity": resumed_envelope["observed_model_identity"],
    }
    assert resume_compatible(resumed_envelope, current)

    resumed_calls: list[str] = []

    def resumed_factory(
        objective: str,
        workspace: Path,
        *,
        fixture_marker: str | None = None,
    ) -> Any:
        resumed_calls.append(objective)
        return _scripted_factory(objective, workspace, fixture_marker=fixture_marker)

    resumed_factory._accepts_fixture_marker = True  # type: ignore[attr-defined]

    resumed_records, resumed_summary = campaign._run_scenario(
        scenario,
        policy=policy,
        gateway_factory=cast(Any, resumed_factory),
        candidate=cast(Mapping[str, str], candidate),
        epoch="TEST-EPOCH",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=model_identity,
        existing_runs=resumed_envelope["runs"],
        existing_summary=resumed_envelope["scenario_results"][0],
    )
    uninterrupted_calls: list[str] = []

    def uninterrupted_factory(
        objective: str,
        workspace: Path,
        *,
        fixture_marker: str | None = None,
    ) -> Any:
        uninterrupted_calls.append(objective)
        return _scripted_factory(objective, workspace, fixture_marker=fixture_marker)

    uninterrupted_factory._accepts_fixture_marker = True  # type: ignore[attr-defined]

    uninterrupted_records, uninterrupted_summary = campaign._run_scenario(
        scenario,
        policy=policy,
        gateway_factory=cast(Any, uninterrupted_factory),
        candidate=cast(Mapping[str, str], candidate),
        epoch="TEST-EPOCH",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=model_identity,
    )
    resumed_all = [*resumed_envelope["runs"], *(item.to_dict() for item in resumed_records)]
    uninterrupted_all = [item.to_dict() for item in uninterrupted_records]
    assert len(resumed_calls) == 4
    assert len(uninterrupted_calls) == 5
    assert [item["scenario_repetition"] for item in resumed_all] == [1, 2, 3, 4, 5]
    assert [item["attempt"] for item in resumed_all] == [1, 2, 3, 4, 5]
    assert _semantic_outcomes(resumed_all) == _semantic_outcomes(uninterrupted_all)
    assert _semantic(resumed_summary) == _semantic(uninterrupted_summary)


def test_state_owned_projection_retains_h_series_facts() -> None:
    snapshot = SimpleNamespace(
        projection_facts=SimpleNamespace(
            invocation_evidence=(),
            canonical_plan=(),
            route_events=(),
            validation_events=(),
            code_outcome={},
            validation_detail={},
            output_chars=0,
            output_truncated=False,
        )
    )
    state = SimpleNamespace(
        tool_history=[{
            "tool": "file_reader",
            "args": {"file_path": "fonte_h2.txt"},
            "result": {
                "status": "succeeded",
                "data": "orion_584271",
                "evidence_provenance": "EXACT_SOURCE",
            },
        }],
        plan=None,
    )
    projection = snapshot_evaluation_projection(snapshot, state=state)
    assert projection["history"][0]["tool"] == "file_reader"
    assert projection["history"][0]["result"]["data"] == "orion_584271"


def test_scripted_engineering_response_uses_canonical_envelope() -> None:
    payload = json.loads(
        _engineering_response(
            "H12: altere h12_module.py para retornar 2",
            "",
        )
    )
    assert payload == {
        "decision": "CHANGE",
        "rationale": "A menor mudança necessária foi identificada na evidência do workspace.",
        "reason_code": "NONE",
        "question": "",
        "changes": [{
            "path": "h12_module.py",
            "kind": "edit",
            "edits": [{
                "operation": "replace",
                "start_line": 2,
                "end_line": 2,
                "content": "    return 2\n",
            }],
        }],
    }


def test_prerequisite_snapshots_are_frozen_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.evaluation.artifact_paths as artifact_paths_module
    import agent.evaluation.real_model_preflight as preflight_module

    base = _analysis_report()
    old_installed = {
        **cast(dict[str, Any], base["installed_acceptance"]),
        "evidence_marker": "validated-before-campaign",
    }
    old_readiness = _readiness_snapshot(base)
    old_readiness["evidence_marker"] = "validated-before-campaign"
    frozen_preflight = {
        "ready": True,
        "reason_codes": [],
        "candidate_identity": base["candidate_identity"],
        "installed_acceptance": old_installed,
        "prerequisite_snapshots": {
            "installed_acceptance": copy.deepcopy(old_installed),
            "deterministic_readiness": copy.deepcopy(old_readiness),
        },
    }
    monkeypatch.setattr(preflight_module, "validate_real_model_preflight", lambda *args, **kwargs: frozen_preflight)
    temp_paths = canonical_artifact_paths(tmp_path)
    monkeypatch.setattr(artifact_paths_module, "canonical_artifact_paths", lambda _root: temp_paths)

    def replace_files_after_preflight(*args: Any, **kwargs: Any) -> dict[str, Any]:
        temp_paths.installed_acceptance.parent.mkdir(parents=True, exist_ok=True)
        temp_paths.installed_acceptance.write_text(
            json.dumps({"status": "failed", "acceptance": False, "evidence_marker": "after"}),
            encoding="utf-8",
        )
        temp_paths.corrective_ready.write_text(
            json.dumps({"dry_run": {"analysis": {"unknown_failed_run_count": 99}}}),
            encoding="utf-8",
        )
        return copy.deepcopy(base)

    monkeypatch.setattr(campaign, "run_scripted_campaign", replace_files_after_preflight)
    report = run_real_model_campaign(
        ROOT,
        gateway_factory=cast(Any, lambda _objective, _workspace: None),
        installed_acceptance=old_installed,
        deterministic_readiness=old_readiness,
    )
    expected_projection = project_release_prerequisite_snapshot(
        old_installed,
        old_readiness,
    )
    assert report["installed_acceptance"] == expected_projection["installed_acceptance"]
    assert report["prerequisite_snapshots"] == expected_projection
    assert report["deterministic_summary"]["analysis"] == expected_projection["deterministic_readiness"]["analysis"]
    assert report["analysis"]["release_verdict"] == "RELEASE_READY"


def test_more_than_64_runs_round_trip_and_reanalysis(tmp_path: Path) -> None:
    report = _analysis_report()
    destination = tmp_path / "campaign-report.json"
    write_campaign_report(destination, report)
    reloaded = json.loads(destination.read_text(encoding="utf-8"))
    assert len(reloaded["runs"]) == 164
    assert "[ITEM_LIMIT]" not in reloaded["runs"]
    assert validate_campaign_report(reloaded, require_final_epoch=False)["valid"]
    assert analyze_campaign(reloaded, require_final_epoch=False)["repetition"]["complete"] is True


def test_late_run_secret_scan_covers_the_full_campaign() -> None:
    report = _analysis_report()
    report["runs"][-1]["evidence"]["final_answer"] = "token=TOPSECRET-LATE-RUN"
    scan = secret_safe_report(report)
    assert scan["pass"] is False
    assert scan["scanned_run_count"] == 164
    assert scan["hits"]


def test_truthful_live_cli_exits_for_ready_not_ready_and_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_evaluation_campaign as cli

    monkeypatch.setattr(cli, "build_real_model_preflight", lambda *args, **kwargs: {
        "ready": True,
        "candidate_identity": "candidate",
        "prerequisite_snapshots": {},
    })
    monkeypatch.setattr(cli, "run_real_model_campaign", lambda *args, **kwargs: {
        "candidate_identity": "candidate",
        "analysis": {"release_verdict": "NOT_RELEASE_READY_RUNTIME", "reason_codes": ["x"]},
    })
    not_ready = cli.main([
        "--mode", "live-model", "--qwen-loaded", "--output", str(tmp_path / "not-ready.json")
    ])
    assert not_ready == 1

    monkeypatch.setattr(cli, "build_real_model_preflight", lambda *args, **kwargs: {
        "ready": False,
        "reason_codes": ["PRECONDITION"],
        "candidate_identity": "candidate",
    })
    blocked = cli.main([
        "--mode", "live-model", "--qwen-loaded", "--output", str(tmp_path / "blocked.json")
    ])
    assert blocked == 2

    no_authorization = cli.main([
        "--mode", "live-model", "--output", str(tmp_path / "unauthorized.json")
    ])
    assert no_authorization == 2


def test_progress_schema_and_adapted_types_fail_closed(tmp_path: Path) -> None:
    candidate = {
        "head": "head",
        "semantic_candidate_fingerprint": "semantic",
        "semantic_manifest_hash": "manifest",
    }
    document = build_progress_document(
        candidate=candidate,
        candidate_identity=candidate_identity_string(candidate),
        semantic_manifest_hash="manifest",
        fixture_identity=fixture_identity(),
        epoch="TEST-EPOCH",
        model_identity=fake_model_identity(),
        repetition_policy=RepetitionPolicy().to_dict(),
        last_attempt={"h_id": "H1"},
    )
    invalid_documents = []
    missing_campaign_schema = dict(document)
    missing_campaign_schema.pop("campaign_schema_version")
    invalid_documents.append(missing_campaign_schema)
    wrong_scenario_schema = dict(document)
    wrong_scenario_schema["scenario_set_version"] = "H-SERIES-OLD"
    invalid_documents.append(wrong_scenario_schema)
    malformed_candidate = dict(document)
    malformed_candidate["candidate"] = []
    invalid_documents.append(malformed_candidate)
    malformed_runs = dict(document)
    malformed_runs["runs_so_far"] = {}
    invalid_documents.append(malformed_runs)

    for index, invalid in enumerate(invalid_documents):
        path = tmp_path / f"invalid-progress-{index}.json"
        path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(CampaignProgressError):
            load_campaign_progress(path)
        with pytest.raises(CampaignProgressError):
            resume_report_from_progress(invalid)


def test_malformed_existing_live_progress_uses_bounded_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.run_evaluation_campaign as cli

    output = tmp_path / "live-result.json"
    progress_path_for(output, root=ROOT).parent.mkdir(parents=True, exist_ok=True)
    progress_path_for(output, root=ROOT).write_text(
        json.dumps({"schema_version": "CAMPAIGN-PROGRESS-V1", "complete": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "build_real_model_preflight", lambda *args, **kwargs: {
        "ready": True,
        "candidate_identity": "candidate",
        "prerequisite_snapshots": {},
    })
    assert cli.main([
        "--mode", "live-model", "--qwen-loaded", "--resume", "--output", str(output)
    ]) == 2


def test_persisted_report_cannot_drop_frozen_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.evaluation.campaign_report as report_module

    report = _analysis_report()
    installed = copy.deepcopy(report["installed_acceptance"])
    projection = copy.deepcopy(report["prerequisite_snapshots"])
    report["analysis"] = analyze_campaign(report)
    original_writer = report_module.write_campaign_report

    def tampering_writer(path: Path, value: Mapping[str, Any]) -> None:
        original_writer(path, value)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        persisted.pop("installed_acceptance", None)
        path.write_text(json.dumps(persisted), encoding="utf-8")

    monkeypatch.setattr(report_module, "write_campaign_report", tampering_writer)
    with pytest.raises(ValueError, match="installed_acceptance"):
        _write_report(
            tmp_path / "tampered-report.json",
            report,
            installed_acceptance=installed,
            deterministic_readiness=projection["deterministic_readiness"],
        )


def test_live_output_collision_is_rejected_before_preflight_or_provider(tmp_path: Path) -> None:
    import scripts.run_evaluation_campaign as cli

    reserved = canonical_artifact_paths(ROOT).corrective_dry_run
    before = reserved.read_bytes() if reserved.exists() else None
    assert cli.main([
        "--mode", "live-model", "--qwen-loaded", "--output", str(reserved)
    ]) == 2
    after = reserved.read_bytes() if reserved.exists() else None
    assert after == before


def _patch_live_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, dict[str, Any]]:
    import scripts.run_evaluation_campaign as cli

    calls: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_load_live_resume", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(
        cli,
        "build_real_model_preflight",
        lambda *args, **kwargs: {
            "ready": True,
            "candidate_identity": "candidate",
            "prerequisite_snapshots": {},
        },
    )

    def fake_campaign(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        return {
            "candidate_identity": "candidate",
            "analysis": {
                "release_verdict": "NOT_RELEASE_READY_RUNTIME",
                "reason_codes": ["instrumented"],
            },
        }

    monkeypatch.setattr(cli, "run_real_model_campaign", fake_campaign)
    return cli, calls


def test_canonical_live_final_and_partial_paths_are_owned_implicitly_and_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli, calls = _patch_live_cli(monkeypatch)
    paths = canonical_artifact_paths(ROOT)
    owned = {path.resolve() for path in live_owned_artifact_paths(ROOT)}

    assert cli.main(["--mode", "live-model", "--qwen-loaded"]) == 1
    assert Path(calls["output_path"]) == paths.real_model_epoch_2.resolve()
    assert Path(calls["progress_path"]) == paths.real_model_epoch_2_partial.resolve()

    calls.clear()
    assert cli.main([
        "--mode",
        "live-model",
        "--qwen-loaded",
        "--output",
        str(paths.real_model_epoch_2),
    ]) == 1
    assert Path(calls["output_path"]) == paths.real_model_epoch_2.resolve()

    calls.clear()
    assert cli.main([
        "--mode",
        "live-model",
        "--qwen-loaded",
        "--output",
        str(paths.real_model_epoch_2_partial),
    ]) == 1
    assert Path(calls["output_path"]) == paths.real_model_epoch_2_partial.resolve()
    assert paths.real_model_epoch_2.resolve() in owned
    assert paths.real_model_epoch_2_partial.resolve() in owned


def test_protected_live_reserved_paths_remain_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    cli, calls = _patch_live_cli(monkeypatch)
    paths = canonical_artifact_paths(ROOT)
    protected = (
        paths.installed_acceptance,
        paths.corrective_dry_run,
        paths.corrective_ready,
        paths.real_model_preflight,
        paths.prior_real_model_epoch,
    )
    for path in protected:
        calls.clear()
        assert cli.main([
            "--mode",
            "live-model",
            "--qwen-loaded",
            "--output",
            str(path),
        ]) == 2
        assert calls == {}


def test_live_relative_output_is_root_resolved_for_guard_writer_and_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli, calls = _patch_live_cli(monkeypatch)
    monkeypatch.chdir(tmp_path)
    relative = Path("relative-live.json")
    expected = (ROOT / relative).resolve()
    assert cli.main([
        "--mode",
        "live-model",
        "--qwen-loaded",
        "--output",
        str(relative),
    ]) == 1
    assert Path(calls["output_path"]) == expected
    assert Path(calls["progress_path"]) == progress_path_for(expected)
    assert not (tmp_path / relative).exists()

    calls.clear()
    protected_relative = Path("." + "audit" + "-local") / "out" / "evaluation-corrective-ready.json"
    assert cli.main([
        "--mode",
        "live-model",
        "--qwen-loaded",
        "--output",
        str(protected_relative),
    ]) == 2
    assert calls == {}

    calls.clear()
    absolute = (tmp_path / "absolute-live.json").resolve()
    assert cli.main([
        "--mode",
        "live-model",
        "--qwen-loaded",
        "--output",
        str(absolute),
    ]) == 1
    assert Path(calls["output_path"]) == absolute
    assert Path(calls["progress_path"]) == progress_path_for(absolute)


def test_release_prerequisite_projection_is_lossless_and_bounded(
    tmp_path: Path,
) -> None:
    installed, readiness = _versioned_release_prerequisite_fixture()
    projection = project_release_prerequisite_snapshot(installed, readiness)
    assert validate_release_prerequisite_projection(projection) == ()
    assert len(projection["deterministic_readiness"]["analysis"]["repetition"]["per_scenario"]) == len(H_SERIES)
    encoded = (json.dumps(projection, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    destination = tmp_path / "prerequisite-projection.json"
    write_bytes_atomic(destination, encoded)
    reloaded = json.loads(destination.read_text(encoding="utf-8"))
    assert reloaded == projection
    assert "[DEPTH_LIMIT]" not in json.dumps(reloaded, ensure_ascii=False)
    assert "[ITEM_LIMIT]" not in json.dumps(reloaded, ensure_ascii=False)


def test_versioned_tests_do_not_embed_local_evidence_roots() -> None:
    offending = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "tests").rglob("*.py"))
        if _source_has_local_evidence_root(path.read_text(encoding="utf-8"))
    ]
    assert offending == []


def test_persisted_report_is_self_sufficient_and_fails_closed_on_prerequisite_corruption(
    tmp_path: Path,
) -> None:
    report = _analysis_report()
    destination = tmp_path / "self-sufficient-report.json"
    _write_report(
        destination,
        report,
        require_final_epoch=True,
        installed_acceptance=report["installed_acceptance"],
        deterministic_readiness=report["prerequisite_snapshots"]["deterministic_readiness"],
    )
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    pristine_installed = copy.deepcopy(persisted["installed_acceptance"])
    assert analyze_campaign(persisted)["release_verdict"] == "RELEASE_READY"

    def corrupted(field: str) -> dict[str, Any]:
        candidate = copy.deepcopy(persisted)
        snapshot = candidate["prerequisite_snapshots"]
        if field == "candidate_identity":
            snapshot["deterministic_readiness"]["candidate_identity"] = "corrupt"
        elif field == "manifest":
            snapshot["deterministic_readiness"]["semantic_manifest_hash"] = "corrupt"
        elif field == "ready":
            snapshot["deterministic_readiness"]["ready"] = False
        elif field == "repetition":
            snapshot["deterministic_readiness"]["analysis"]["repetition"]["complete"] = False
        elif field == "wheel_sha":
            snapshot["installed_acceptance"].pop("wheel_sha256")
        elif field == "status":
            snapshot["installed_acceptance"]["status"] = "failed"
        elif field == "installed":
            snapshot.pop("installed_acceptance")
        return candidate

    for field in ("candidate_identity", "manifest", "ready", "repetition", "wheel_sha", "status", "installed"):
        with pytest.raises(ValueError, match="persisted|prerequisite|campaign evidence"):
            analyze_campaign(corrupted(field), installed_acceptance=pristine_installed)


def test_generic_evidence_sanitizer_depth_bound_remains_unchanged() -> None:
    value: Any = {"leaf": True}
    for _ in range(MAX_EVIDENCE_DEPTH + 2):
        value = {"nested": value}
    sanitized = sanitize_evidence(value)
    assert "[DEPTH_LIMIT]" in json.dumps(sanitized)


def test_known_deterministic_failure_blocks_readiness_and_preflight(tmp_path: Path) -> None:
    candidate = candidate_identity(ROOT)
    candidate_id = candidate_identity_string(candidate)
    model = model_config_identity(ROOT, profile_name="local_8gb", evidence_level="real_model")
    report = _analysis_report()
    failed_run = next(item for item in report["runs"] if item["h_id"] == "H2")
    failed_run["passed"] = False
    failed_run["evidence"]["causal_classification"] = "HARNESS_DEFECT"
    failed_run["evidence"]["deterministic_failures"] = ["harness:known"]
    analysis = analyze_campaign(report, require_final_epoch=False)
    projection = {
        "candidate": candidate,
        "candidate_identity": candidate_id,
        "semantic_manifest_hash": candidate["semantic_manifest_hash"],
        "fixture_identity": fixture_identity(),
        "h_series_version": H_SERIES_VERSION,
        "repetition_policy": RepetitionPolicy().to_dict(),
        "summary": {"total": len(report["runs"]), "passed": len(report["runs"]) - 1, "failed": 1},
        "analysis": analysis,
        "path": "memory",
    }
    readiness_result = deterministic_readiness(projection)
    assert readiness_result["complete"] is False
    assert "DETERMINISTIC_FAILURES_REMAIN" in readiness_result["reason_codes"]
    assert "DETERMINISTIC_CAUSAL_FAILURES_REMAIN" in readiness_result["reason_codes"]

    installed = {
        "schema_version": 2,
        "status": "passed",
        "acceptance": True,
        "mode": "clean-acceptance",
        "clean": True,
        "evidence_level": "installed_deterministic",
        "task_files_in_wheel": False,
        "candidate": dict(candidate),
        "candidate_identity": candidate_id,
        "semantic_manifest_hash": candidate["semantic_manifest_hash"],
        "wheel_sha256": "a" * 64,
    }
    readiness = {
        **{key: projection[key] for key in (
            "candidate", "candidate_identity", "semantic_manifest_hash", "fixture_identity",
            "h_series_version", "repetition_policy",
        )},
        "epoch": DEFAULT_REAL_MODEL_EPOCH,
        "campaign_started": False,
        "model_identity_schema": {"model_config_fingerprint": model["model_config_fingerprint"]},
        "dry_run": {"summary": projection["summary"], "analysis": analysis},
    }
    result = build_real_model_preflight(
        ROOT,
        output_path=tmp_path / "blocked-preflight.json",
        installed_acceptance=installed,
        deterministic_readiness=readiness,
    )
    assert result["ready"] is False
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert "DETERMINISTIC_FAILURES_REMAIN" in result["reason_codes"]


def test_corrective_ready_cli_does_not_greenlight_blocked_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_evaluation_campaign as cli

    monkeypatch.setattr(cli, "run_scripted_campaign", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "build_corrective_readiness", lambda *args, **kwargs: {
        "ready": False,
        "reason_codes": ["DETERMINISTIC_FAILURES_REMAIN"],
    })
    paths = canonical_artifact_paths(tmp_path)
    assert cli._run_corrective_ready(
        paths,
        tmp_path / "ready.json",
        profile_name="local_8gb",
        external_identity=None,
    ) == 1


def test_installed_acceptance_rejects_candidate_identity_change(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.verify_installed_package as installed_module

    captured = {"head": "a", "semantic_candidate_fingerprint": "b", "semantic_manifest_hash": "c"}
    changed = {**captured, "semantic_candidate_fingerprint": "changed"}
    monkeypatch.setattr(installed_module, "candidate_identity", lambda _root: changed)
    with pytest.raises(installed_module.VerificationError, match="changed during installed acceptance"):
        installed_module._assert_candidate_identity_unchanged(ROOT, captured)


def test_moved_campaign_payloads_are_utf8_and_not_mojibake() -> None:
    for relative in (
        "agent/evaluation/scripted_gateway_logic.py",
        "agent/evaluation/scripted_gateway_plans.py",
        "agent/evaluation/scripted_gateway_plan_core.py",
        "agent/evaluation/structured_proof_scenarios.py",
        "scripts/verify_installed_package.py",
    ):
        raw = (ROOT / relative).read_bytes()
        text_value = raw.decode("utf-8")
        assert not any(marker in text_value for marker in ("Ã", "ƒ", "Â")), relative


def test_external_identity_readiness_round_trips_into_preflight(tmp_path: Path) -> None:
    from agent.evaluation.analysis import build_corrective_readiness

    external_identity = "https://provider.example/model-a"
    candidate = candidate_identity(ROOT)
    candidate_id = candidate_identity_string(candidate)
    manifest = semantic_candidate_manifest(ROOT)
    dry_report = _analysis_report()
    dry_report["candidate"] = candidate
    dry_report["candidate_identity"] = candidate_id
    dry_report["semantic_candidate_manifest"] = manifest
    dry_report["semantic_manifest_hash"] = semantic_manifest_hash(manifest)
    for run in dry_report["runs"]:
        run["evidence"]["candidate_identity"] = candidate_id
    readiness = build_corrective_readiness(
        ROOT,
        dry_report,
        profile_name="local_8gb",
        external_identity=external_identity,
    )
    assert readiness["ready"] is True
    assert readiness["model_identity_schema"]["external_identity"] == external_identity
    assert "--qwen-loaded" in readiness["planned_live_model_command"]

    installed = {
        "schema_version": 2,
        "status": "passed",
        "acceptance": True,
        "mode": "clean-acceptance",
        "clean": True,
        "evidence_level": "installed_deterministic",
        "task_files_in_wheel": False,
        "candidate": dict(candidate),
        "candidate_identity": candidate_id,
        "semantic_manifest_hash": candidate["semantic_manifest_hash"],
        "wheel_sha256": "b" * 64,
    }
    preflight = build_real_model_preflight(
        ROOT,
        profile_name="local_8gb",
        external_identity=external_identity,
        output_path=tmp_path / "external-preflight.json",
        installed_acceptance=installed,
        deterministic_readiness=readiness,
    )
    assert preflight["ready"] is True
    assert preflight["provider_calls"] == 0
    assert preflight["network_calls"] == 0


def test_preflight_is_zero_call_and_binds_both_snapshots(tmp_path: Path) -> None:
    candidate = candidate_identity(ROOT)
    candidate_id = candidate_identity_string(candidate)
    model = model_config_identity(
        ROOT,
        profile_name="local_8gb",
        evidence_level="real_model",
        external_identity=None,
    )
    installed = {
        "schema_version": 2,
        "status": "passed",
        "acceptance": True,
        "mode": "clean-acceptance",
        "clean": True,
        "evidence_level": "installed_deterministic",
        "task_files_in_wheel": False,
        "candidate": dict(candidate),
        "candidate_identity": candidate_id,
        "semantic_manifest_hash": candidate["semantic_manifest_hash"],
        "wheel_sha256": "a" * 64,
    }
    readiness = _readiness_snapshot(_analysis_report())
    readiness["candidate"] = dict(candidate)
    readiness["candidate_identity"] = candidate_id
    readiness["semantic_manifest_hash"] = candidate["semantic_manifest_hash"]
    readiness["model_identity_schema"] = {
        "model_config_fingerprint": model["model_config_fingerprint"],
    }
    destination = tmp_path / "real-model-preflight.json"
    result = build_real_model_preflight(
        ROOT,
        output_path=destination,
        installed_acceptance=installed,
        deterministic_readiness=readiness,
    )
    assert result["ready"] is True
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    expected_projection = project_release_prerequisite_snapshot(installed, readiness)
    assert result["prerequisite_snapshots"] == expected_projection
    assert json.loads(destination.read_text(encoding="utf-8"))["ready"] is True


def test_readiness_projection_is_io_free() -> None:
    value = {"dry_run": {"summary": {"total": 1}, "analysis": {"evidence_envelope": {"valid": True}}}}
    assert deterministic_summary_from_readiness(value, source="memory")["path"] == "memory"
    assert deterministic_summary_from_readiness(None)["recorded"] is False


def test_complete_progress_cannot_be_resumed() -> None:
    with pytest.raises(CampaignProgressError, match="already complete"):
        resume_report_from_progress({"complete": True})
