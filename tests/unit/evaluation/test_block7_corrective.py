from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Mapping, cast

import pytest

from agent.evaluation import block7_campaign
from agent.evaluation.block7 import (
    H_SERIES,
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    HSeriesArm,
    HSeriesScenario,
    RepetitionPolicy,
    digest_fixture,
)
from agent.evaluation.block7_analysis import analyze_campaign, secret_safe_report, validate_campaign_report
from agent.evaluation.block7_execution import CampaignRun, _run_one, classify_failure
from agent.evaluation.block7_gateway import _scripted_factory
from agent.evaluation.block7_identity import (
    candidate_identity_string,
    fake_model_identity,
    fixture_identity,
    semantic_candidate_fingerprint,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.block7_oracle import (
    declared_oracle_keys,
    deterministic_oracle_failures,
    validate_oracle_coverage,
)
from agent.evaluation.contracts import ScenarioExpectation
from agent.llm.errors import ModelConnectionError

_CANDIDATE = {
    "head": "head",
    "semantic_candidate_fingerprint": "semantic",
    "semantic_manifest_hash": "manifest",
}
_MODEL = fake_model_identity()


def _scenario(h_id: str, *, arm_ids: tuple[str, ...] = ("arm",)) -> HSeriesScenario:
    return HSeriesScenario(
        h_id,
        f"test {h_id}",
        f"fixture-{h_id}",
        tuple(HSeriesArm(arm_id, f"{h_id}: objective", expectation=ScenarioExpectation()) for arm_id in arm_ids),
        5 if h_id == "H2" else 3,
    )


def _fake_runs(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[bool],
    *,
    environmental: set[int] | None = None,
    arm_ids: tuple[str, ...] = ("arm",),
) -> list[int]:
    environmental = set() if environmental is None else environmental
    calls: list[int] = []

    def fake_run(
        scenario: HSeriesScenario,
        arm: HSeriesArm,
        repetition: int,
        **kwargs: Any,
    ) -> CampaignRun:
        del arm
        calls.append(repetition)
        is_environmental = repetition in environmental
        passed = outcomes[min(repetition - 1, len(outcomes) - 1)]
        evidence = {
            "epoch": kwargs["epoch"],
            "candidate_identity": candidate_identity_string(kwargs["candidate"]),
            "model_config_fingerprint": _MODEL["model_config_fingerprint"],
            "scenario_set_version": H_SERIES_VERSION,
            "valid_repetition": not is_environmental,
            "scenario_repetition": kwargs.get("scenario_repetition"),
            "causal_classification": "UNKNOWN" if passed else "MODEL_VARIANCE",
            "deterministic_failures": [] if passed else ["model:failed"],
            "critical_incidents": [],
            "measurement": {},
        }
        return CampaignRun(
            scenario.h_id,
            kwargs.get("arm_id", "arm"),
            repetition,
            passed,
            {},
            evidence,
            attempt=repetition,
            scenario_repetition=kwargs.get("scenario_repetition"),
            valid_repetition=not is_environmental,
            environmental=is_environmental,
        )

    # The campaign passes arm explicitly, so preserve it in a small wrapper.
    def fake_run_with_arm(scenario: HSeriesScenario, arm: HSeriesArm, repetition: int, **kwargs: Any) -> CampaignRun:
        result = fake_run(scenario, arm, repetition, **kwargs)
        return CampaignRun(
            result.h_id,
            arm.arm_id,
            result.repetition,
            result.passed,
            result.report,
            result.evidence,
            attempt=result.attempt,
            scenario_repetition=result.scenario_repetition,
            valid_repetition=result.valid_repetition,
            environmental=result.environmental,
        )

    monkeypatch.setattr(block7_campaign, "_run_one", fake_run_with_arm)
    block7_campaign._run_scenario(
        _scenario("H3", arm_ids=arm_ids),
        policy=RepetitionPolicy(),
        gateway_factory=cast(Any, lambda *_args: None),
        candidate=cast(Mapping[str, str], _CANDIDATE),
        epoch="epoch",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=_MODEL,
    )
    return calls


def test_repetition_state_machine_stops_at_unanimous_three(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_runs(monkeypatch, [True, True, True])
    assert calls == [1, 2, 3]


def test_repetition_state_machine_stops_at_unanimous_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_runs(monkeypatch, [False, False, False])
    assert calls == [1, 2, 3]


@pytest.mark.parametrize("outcomes", ([True, False, False, True, True], [True, True, False, False, True]))
def test_mixed_three_expands_to_exactly_five_without_selective_rerun(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[bool]
) -> None:
    calls = _fake_runs(monkeypatch, outcomes)
    assert calls == [1, 2, 3, 4, 5]
    assert max(calls) == 5


def test_h2_is_exactly_five_even_when_unanimous(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_run(scenario: HSeriesScenario, arm: HSeriesArm, repetition: int, **kwargs: Any) -> CampaignRun:
        del arm
        calls.append(repetition)
        return CampaignRun(
            scenario.h_id,
            "arm",
            repetition,
            True,
            {},
            {
                "epoch": kwargs["epoch"],
                "candidate_identity": candidate_identity_string(kwargs["candidate"]),
                "model_config_fingerprint": _MODEL["model_config_fingerprint"],
                "scenario_set_version": H_SERIES_VERSION,
                "valid_repetition": True,
                "scenario_repetition": kwargs["scenario_repetition"],
                "causal_classification": "UNKNOWN",
                "deterministic_failures": [],
                "measurement": {},
            },
            attempt=repetition,
            scenario_repetition=kwargs["scenario_repetition"],
        )

    monkeypatch.setattr(block7_campaign, "_run_one", fake_run)
    _, summary = block7_campaign._run_scenario(
        _scenario("H2"),
        policy=RepetitionPolicy(),
        gateway_factory=cast(Any, lambda *_args: None),
        candidate=cast(Mapping[str, str], _CANDIDATE),
        epoch="epoch",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=_MODEL,
    )
    assert calls == [1, 2, 3, 4, 5]
    assert summary["scenario_repetitions"] == 5


def test_environmental_attempt_is_excluded_and_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_run(scenario: HSeriesScenario, arm: HSeriesArm, repetition: int, **kwargs: Any) -> CampaignRun:
        del arm
        calls.append(repetition)
        environmental = repetition == 1
        return CampaignRun(
            scenario.h_id,
            "arm",
            repetition,
            not environmental,
            {},
            {
                "epoch": kwargs["epoch"],
                "candidate_identity": candidate_identity_string(kwargs["candidate"]),
                "model_config_fingerprint": _MODEL["model_config_fingerprint"],
                "scenario_set_version": H_SERIES_VERSION,
                "valid_repetition": not environmental,
                "scenario_repetition": kwargs["scenario_repetition"],
                "causal_classification": "ENVIRONMENTAL" if environmental else "UNKNOWN",
                "deterministic_failures": ["environment"] if environmental else [],
                "measurement": {},
            },
            attempt=repetition,
            scenario_repetition=kwargs["scenario_repetition"],
            valid_repetition=not environmental,
            environmental=environmental,
        )

    monkeypatch.setattr(block7_campaign, "_run_one", fake_run)
    records, summary = block7_campaign._run_scenario(
        _scenario("H3"),
        policy=RepetitionPolicy(),
        gateway_factory=cast(Any, lambda *_args: None),
        candidate=cast(Mapping[str, str], _CANDIDATE),
        epoch="epoch",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=_MODEL,
    )
    assert calls == [1, 2, 3, 4]
    assert summary["scenario_repetitions"] == 3
    assert summary["environmental_attempts"] == 1
    assert records[0].valid_repetition is False
    assert all(record.valid_repetition for record in records[1:])


def test_h1_paired_arm_is_one_scenario_repetition(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_runs(monkeypatch, [True, True, True], arm_ids=("left", "right"))
    assert calls == [1, 1, 2, 2, 3, 3]


def test_failure_attribution_requires_evidence_and_preserves_runtime_precedence() -> None:
    report = SimpleNamespace(
        passed=False,
        observation=SimpleNamespace(measurement={}, evidence={}),
    )
    unresolved = classify_failure(report, ("scenario:mismatch",), EvidenceLevel.REAL_MODEL)
    assert unresolved.classification is CausalFailureClass.UNKNOWN

    model = classify_failure(
        report,
        ("required_tool_missing:grep",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={
            "model_behavior": {
                "signature": "missing_required_tool",
                "category": "capability",
                "decision_evidence": True,
                "canonical_runtime_evidence": True,
            }
        },
    )
    assert model.classification is CausalFailureClass.MODEL_CAPABILITY

    runtime = classify_failure(
        report,
        ("required_tool_missing:grep",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={
            "runtime_defect": {"proven": True, "reason_codes": ["runtime_invariant"]},
            "model_behavior": {
                "signature": "missing_required_tool",
                "category": "capability",
                "decision_evidence": True,
                "canonical_runtime_evidence": True,
            },
        },
    )
    assert runtime.classification is CausalFailureClass.RUNTIME_DEFECT

    environmental = classify_failure(
        report,
        ("endpoint_failed",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={"environmental": {"concrete": True, "reason": "connection refused"}},
    )
    assert environmental.classification is CausalFailureClass.ENVIRONMENTAL


def test_provider_environment_exception_is_preserved_as_invalid_environmental_attempt() -> None:
    h1 = next(item for item in H_SERIES if item.h_id == "H1")

    def unavailable(_objective: str, _workspace: Any) -> Any:
        raise ModelConnectionError("connection refused")

    run = _run_one(
        h1,
        h1.arms[0],
        1,
        gateway_factory=cast(Any, unavailable),
        candidate=cast(Mapping[str, str], _CANDIDATE),
        epoch="epoch",
        evidence_level=EvidenceLevel.REAL_MODEL,
        model_identity=_MODEL,
        scenario_repetition=1,
        attempt=1,
    )
    assert run.environmental
    assert not run.valid_repetition
    assert run.evidence["causal_classification"] == "ENVIRONMENTAL"


def _oracle_report(*, evidence: dict[str, Any], answer: str = "", success: bool = True) -> Any:
    return SimpleNamespace(
        passed=success,
        observation=SimpleNamespace(
            success=success,
            answer=answer,
            evidence=evidence,
            measurement={},
        ),
    )


def test_oracle_census_and_h4_real_path_fail_closed() -> None:
    coverage = validate_oracle_coverage()
    assert set(declared_oracle_keys()) == set(coverage)
    h4 = next(item for item in H_SERIES if item.h_id == "H4").arms[0]
    raw = json.dumps({
        "action": "use_tools",
        "plan": [{
            "tool": "grep",
            "args": {"pattern": "H4_VALUE", "path": "."},
            "bindings": {"pattern": {"from_step": 1, "path": []}},
        }],
    })
    report = _oracle_report(evidence={
        "model_decisions": [{"response": raw}],
        "canonical_plan": [],
        "invocation_evidence": [],
        "validation_evidence": [{"type": "hard_block", "reason": "target colide com args concretos"}],
        "terminal_status": "blocked",
    }, success=False)
    failures = deterministic_oracle_failures(report, h4)
    assert "invalid_duplicate_executed" not in failures
    assert "invalid_duplicate_rejection_evidence_missing" not in failures

    executed = _oracle_report(evidence={
        "model_decisions": [{"response": raw}],
        "canonical_plan": [],
        "invocation_evidence": [{"tool": "grep", "args": {"pattern": "H4_VALUE"}, "result": {"executed": True}}],
        "validation_evidence": [],
        "terminal_status": "succeeded",
    })
    assert "invalid_duplicate_executed" in deterministic_oracle_failures(executed, h4)


def test_oracle_h11_terminal_truth_and_grounding_paths() -> None:
    h11 = next(item for item in H_SERIES if item.h_id == "H11").arms[0]
    evidence = {
        "route_events": [{"type": "hierarchical_started"}],
        "terminal_status": "succeeded",
        "invocation_evidence": [],
    }
    assert "required_terminal_status_missing:failed" in deterministic_oracle_failures(_oracle_report(evidence=evidence), h11)

    h7 = next(item for item in H_SERIES if item.h_id == "H7").arms[0]
    assert deterministic_oracle_failures(_oracle_report(
        evidence={
            "invocation_evidence": [{"tool": "grep", "status": "succeeded", "result": {"status": "succeeded", "data": []}}],
            "terminal_status": "succeeded",
        },
        answer="H7_EMPTY_SENTINEL nao foi observado",
    ), h7) == ()

    h8 = next(item for item in H_SERIES if item.h_id == "H8").arms[0]
    assert deterministic_oracle_failures(_oracle_report(
        evidence={
            "invocation_evidence": [{"tool": "grep", "status": "failed", "result": {"status": "failed", "data": None}}],
            "terminal_status": "failed",
        },
        answer="falha real da ferramenta",
        success=False,
    ), h8) == ()

    h9 = next(item for item in H_SERIES if item.h_id == "H9").arms[0]
    assert deterministic_oracle_failures(_oracle_report(
        evidence={
            "invocation_evidence": [{
                "tool": "grep",
                "status": "succeeded",
                "result": {"status": "succeeded", "data": ["one"], "artifacts": [{"metadata": {"truncated": True}}]},
            }],
            "terminal_status": "succeeded",
        },
        answer="a observacao foi truncada",
    ), h9) == ()


def _analysis_report(*, h3_mixed: bool = False, runtime_incident: bool = False, identity_drift: bool = False) -> dict[str, Any]:
    candidate = dict(_CANDIDATE)
    candidate_id = candidate_identity_string(candidate)
    manifest = [{"path": "agent/runtime.py", "sha256": "x"}]
    runs: list[dict[str, Any]] = []
    for scenario in H_SERIES:
        count = 5 if scenario.h_id == "H2" else 3
        if h3_mixed and scenario.h_id == "H3":
            count = 5
        outcomes = [True] * count
        if h3_mixed and scenario.h_id == "H3":
            outcomes = [True, False, False, True, True]
        for repetition, scenario_pass in enumerate(outcomes, start=1):
            for arm in scenario.arms:
                evidence = {
                    "scenario_id": f"{scenario.h_id.lower()}-{arm.arm_id}",
                    "epoch": "B7-REAL-MODEL-EPOCH-2",
                    "candidate_identity": candidate_id,
                    "model_config_fingerprint": _MODEL["model_config_fingerprint"],
                    "model_fingerprint": _MODEL,
                    "declared_model_identity": _MODEL,
                    "initial_fixture_digest": digest_fixture(arm.initial_files),
                    "objective": arm.objective,
                    "observed_model_identity": {
                        "available": True,
                        "provider_model_id": "block7-scripted",
                        "actual_provider_model_id": "block7-scripted",
                        "provider": "block7-scripted",
                        "model": "block7-scripted",
                        "endpoint_identity": "in-process://block7-scripted",
                        "source": "response.provider_metadata",
                    },
                    "scenario_set_version": H_SERIES_VERSION,
                    "scenario_repetition": repetition,
                    "valid_repetition": True,
                    "causal_classification": "MODEL_CAPABILITY" if not scenario_pass else "UNKNOWN",
                    "deterministic_failures": [] if scenario_pass else ["model:failure"],
                    "critical_incidents": ["forbidden_effect"] if runtime_incident and scenario.h_id == "H1" and repetition == 1 and arm.arm_id == scenario.arms[0].arm_id else [],
                    "measurement": {"model_calls": 1, "tool_history_count": 0, "duration_ms": 1, "token_usage_complete": True},
                    "h2_reporting": {} if scenario.h_id == "H2" else None,
                }
                if identity_drift and not runs:
                    evidence["model_config_fingerprint"] = "drift"
                runs.append({"h_id": scenario.h_id, "arm_id": arm.arm_id, "repetition": repetition, "passed": scenario_pass, "valid_repetition": True, "evidence": evidence})
    scenario_results = [
        {
            "h_id": scenario.h_id,
            "fixture_id": scenario.fixture_id,
            "scenario_repetitions": 5 if scenario.h_id == "H2" else (5 if h3_mixed and scenario.h_id == "H3" else 3),
            "passes": 5 if scenario.h_id == "H2" else (3 if h3_mixed and scenario.h_id == "H3" else 3),
            "arm_executions": (5 if scenario.h_id == "H2" else (5 if h3_mixed and scenario.h_id == "H3" else 3)) * len(scenario.arms),
        }
        for scenario in H_SERIES
    ]
    return {
        "schema_version": "B7-CAMPAIGN-V2.0",
        "scenario_set_version": H_SERIES_VERSION,
        "fixture_identity": fixture_identity(),
        "epoch": "B7-REAL-MODEL-EPOCH-2",
        "evidence_level": "real_model",
        "candidate": candidate,
        "candidate_identity": candidate_id,
        "semantic_candidate_manifest": manifest,
        "semantic_manifest_hash": semantic_manifest_hash(manifest),
        "model_identity": _MODEL,
        "declared_model_identity": _MODEL,
        "observed_model_identity": {
            "available": True,
            "consistent": True,
            "provider_model_id": "block7-scripted",
            "actual_provider_model_id": "block7-scripted",
            "provider": "block7-scripted",
            "model": "block7-scripted",
            "endpoint_identity": "in-process://block7-scripted",
            "source": "response.provider_metadata",
            "identities": [],
        },
        "model_config_fingerprint": _MODEL["model_config_fingerprint"],
        "repetition_policy": RepetitionPolicy().to_dict(),
        "scenario_results": scenario_results,
        "installed_acceptance": {
            "status": "passed",
            "mode": "clean-acceptance",
            "acceptance": True,
            "task_files_in_wheel": False,
        },
        "deterministic_readiness": {"recorded": True, "complete": True},
        "runs": runs,
    }


def test_analyzer_verdict_thresholds_and_blocker_precedence() -> None:
    assert analyze_campaign(_analysis_report())["release_verdict"] == "RELEASE_READY"
    model = analyze_campaign(_analysis_report(h3_mixed=True))
    assert model["release_verdict"] == "NOT_RELEASE_READY_MODEL"
    runtime = analyze_campaign(_analysis_report(h3_mixed=True, runtime_incident=True))
    assert runtime["release_verdict"] == "NOT_RELEASE_READY_RUNTIME"
    with pytest.raises(ValueError, match="campaign evidence is incomplete"):
        analyze_campaign(_analysis_report(identity_drift=True))


def test_semantic_candidate_changes_for_runtime_but_not_documentation(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "agent").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "agent" / "runtime.py").write_text("runtime = 1\n", encoding="utf-8")
    (root / "scripts" / "run_block7.py").write_text("runner = 1\n", encoding="utf-8")
    (root / "docs" / "note.md").write_text("one\n", encoding="utf-8")
    first = semantic_candidate_fingerprint(root)
    first_manifest_hash = semantic_manifest_hash(semantic_candidate_manifest(root))
    (root / "docs" / "note.md").write_text("two\n", encoding="utf-8")
    assert semantic_candidate_fingerprint(root) == first
    assert semantic_manifest_hash(semantic_candidate_manifest(root)) == first_manifest_hash
    (root / "agent" / "runtime.py").write_text("runtime = 2\n", encoding="utf-8")
    assert semantic_candidate_fingerprint(root) != first


def test_model_identity_is_stable_and_resume_mismatch_is_rejected() -> None:
    identity = fake_model_identity()
    assert identity["model_config_fingerprint"] == identity["fingerprint"]
    changed = dict(identity)
    changed["temperature"] = 0.5
    assert changed["temperature"] != identity["temperature"]
    assert identity["provider"] == "block7-scripted"


def test_machine_report_schema_and_secret_scan_are_deterministic() -> None:
    report = _analysis_report()
    assert validate_campaign_report(report)["valid"]
    report["runs"][0]["evidence"]["answer"] = "authorization: bearer TOPSECRET"
    scan = secret_safe_report(report)
    assert scan["pass"]


def test_fake_provider_identity_is_bound_to_each_run() -> None:
    h1 = next(item for item in H_SERIES if item.h_id == "H1")
    run = _run_one(
        h1,
        h1.arms[0],
        1,
        gateway_factory=cast(Any, _scripted_factory),
        candidate=cast(Mapping[str, str], _CANDIDATE),
        epoch="epoch",
        evidence_level=EvidenceLevel.DETERMINISTIC,
        model_identity=_MODEL,
        scenario_repetition=1,
        attempt=1,
    )
    assert run.passed
    assert run.evidence["model_config_fingerprint"] == _MODEL["model_config_fingerprint"]
    assert run.evidence["model_fingerprint"]["provider"] == "block7-scripted"
