"""Permanent W20-B campaign; deterministic closure runs these tests later."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.engineering.backends.inspection as inspection_backend
from agent.discovery.contracts import (
    DISCOVERY_AVAILABILITY_UNKNOWN,
    DISCOVERY_NO_MATCH,
    DISCOVERY_QUERY_EMPTY,
    DISCOVERY_SEMANTIC_INVALID_RESPONSE,
    DISCOVERY_SEMANTIC_NOT_AUTHORIZED,
    DISCOVERY_SEMANTIC_UNAVAILABLE,
    DiscoveryAvailability,
    DiscoveryCandidateV1,
    DiscoveryControllerState,
    DiscoveryEntryV1,
    DiscoveryExecutionContext,
    DiscoveryMatchKind,
    DiscoverySourceKind,
)
from agent.discovery.index import DiscoveryCatalog
from agent.discovery.ranking import normalize_query, rank_entries, score_entry
from agent.discovery.semantic import (
    COMMAND_DISCOVERY_SCHEMA,
    MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES,
    SemanticCommandDiscovery,
    validate_command_discovery_response,
)
from agent.discovery.service import DiscoveryService
from agent.discovery.store import FrecencyStore
from agent.engineering.backends.health import HealthBackend
from agent.engineering.backends.inspection import InspectionBackend, project_inspection
from agent.engineering.contracts import (
    EngineeringEnvironmentV1,
    EngineeringOperationViewV1,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringScope,
    EngineeringTerminalStatus,
)
from agent.engineering.model_safe import (
    MODEL_SAFE_OPERATIONS,
    MODEL_SAFE_TOOL_DESCRIPTORS,
    ModelSafeEngineering,
    ModelSafeStatus,
    _context_from_owner,
    _project_health,
    _project_reference,
)
from agent.engineering.registry import production_registry
from agent.evaluation.comparison import (
    EVALUATION_COMPARISON_INCOMPATIBLE,
    EvaluationComparisonError,
    compare_receipt_groups,
)
from agent.evaluation.contracts import ExecutionObservation, ScenarioReport
from agent.evaluation.evaluation_identity import fake_model_identity
from agent.evaluation.experiment import evaluation_context
from agent.evaluation.practical import (
    MAX_ENGINEERING_FAULT_JSON_BYTES,
    FaultController,
    FaultEffect,
    FaultingEvaluationGateway,
    FaultPlanV1,
    FaultPlanV1Error,
    FaultStepV1,
    parse_fault_json,
    run_practical_scripted,
)
from agent.evaluation.receipt import build_evaluation_receipt
from agent.evaluation.scenario_contracts import EvidenceLevel
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY
from agent.interfaces.cli.completion import complete_command, completion_commands, powershell_completion_script
from agent.interfaces.cli.discovery_projection import availability_for_binding, build_catalog
from agent.interfaces.cli.discovery_ui import select_palette_entry
from agent.interfaces.cli.interactive_shell import InteractiveShell
from agent.interfaces.cli.parser import build_parser
from agent.interfaces.cli.workspace_recents import load_recent_workspaces
from agent.tools.contracts import ToolDescriptor
from agent.tools.extension_bootstrap import WorkspaceToolRegistryComposer
from agent.tools.extension_runtime import ExtensionRuntimeMaterialization

ROOT = Path(__file__).resolve().parents[3]
PRACTICAL_IDS = tuple(f"PV1-{index:02d}" for index in range(1, 9))


def _scenario_report(context, scenario_id: str = "PV1-01", *, passed: bool = True, failures=(), run_suffix: str = "1") -> ScenarioReport:
    return ScenarioReport(
        scenario_id=scenario_id,
        capability="w20-b",
        passed=passed,
        observation=ExecutionObservation(
            success=True,
            answer="deterministic",
            measurement={
                "variant_fingerprint": context.profile.composition.fingerprint,
                "variant_composition": context.profile.composition.normalized_dict(),
                "run_id": f"run-w20-b-{run_suffix}",
                "root_task_id": f"root-w20-b-{run_suffix}",
                "runtime_task_id": f"task-w20-b-{run_suffix}",
                "terminal_outcome": "succeeded",
                "duration_ms": 1,
                "model_calls": 1,
                "tool_calls": 0,
                "tool_history_count": 0,
                "accounted_tokens": 1,
                "reported_input_tokens": 1,
                "reported_output_tokens": 1,
                "reported_total_tokens": 2,
                "output_chars": 12,
                "token_usage_complete": True,
                "output_truncated": False,
                "rollback_occurred": False,
                "replan_count": 0,
            },
        ),
        failures=tuple(failures),
        changed_files=(),
    )


def _receipt_group(profile: str, candidate: str = "candidate-a", model=None) -> list[dict[str, object]]:
    context = evaluation_context(
        profile,
        experiment_id="w20-persona-router-practical-v1",
        trial_id=profile,
    )
    model_value = fake_model_identity() if model is None else model
    result: list[dict[str, object]] = []
    for index, scenario_id in enumerate(PRACTICAL_IDS, 1):
        receipt = build_evaluation_receipt(
            _scenario_report(context, scenario_id, run_suffix=str(index)),
            experiment=context,
            scenario_arm_id=scenario_id,
            repetition=1,
            attempt=1,
            evidence_level=EvidenceLevel.DETERMINISTIC.value,
        )
        result.append(
            {
                **receipt.to_dict(),
                "candidate_identity": candidate,
                "model_identity": model_value,
            }
        )
    return result


def _entry(entry_id: str, preferred: str, *, source=DiscoverySourceKind.CLI_COMMAND, available=True, reason=None) -> DiscoveryCandidateV1:
    item = DiscoveryEntryV1(
        1,
        entry_id,
        source,
        preferred,
        "bounded description",
        preferred,
        aliases=(),
        keywords=("command",),
        examples=(preferred,),
    )
    return DiscoveryCandidateV1(item, available, reason, DiscoveryMatchKind.NONE, {"match_kind_rank": 0, "field_rank": 6, "token_score": 0, "fuzzy_score": 0}, 0)


def _result(candidates, query="query", *, requested=True):
    from agent.discovery.contracts import DiscoveryResultV1

    return DiscoveryResultV1(query, tuple(candidates), (), requested, False)


def _model_safe_result(workspace_id: str) -> EngineeringRunResultV1:
    return EngineeringRunResultV1(
        run_id="engr-" + "a" * 32,
        operation_id="health.offline",
        phase=EngineeringRunPhase.TERMINAL,
        status=EngineeringTerminalStatus.SUCCEEDED,
        reason_codes=(),
        started_at_utc="2026-01-01T00:00:00.000000Z",
        finished_at_utc="2026-01-01T00:00:01.000000Z",
        duration_ms=1000,
        workspace_id=workspace_id,
        persisted=True,
        summary={"status": "ok", "path": "must-not-leak"},
        references=(),
        environment=EngineeringEnvironmentV1(workspace_id, None),
    )


def test_B01_oracle_failure_changes_receipt_truth() -> None:
    context = evaluation_context("current", experiment_id="w20-b-receipt", trial_id="current")
    receipt = build_evaluation_receipt(
        _scenario_report(context, passed=True),
        experiment=context,
        scenario_arm_id="current",
        repetition=1,
        attempt=1,
        evidence_level="deterministic",
        evaluator_failure_codes=("oracle:failed",),
        evaluator_passed=False,
    )
    assert receipt.technical.evaluator_passed is False
    assert receipt.technical.evaluator_failure_codes == ("oracle:failed",)


def test_B02_clean_current_has_eight_completed_receipts() -> None:
    report = run_practical_scripted(ROOT, profile_id="current")
    assert report["execution_complete"] is True
    assert report["experiment_id"] == "w20-practical-v1"
    assert tuple(item["scenario_id"] for item in report["scenarios"]) == PRACTICAL_IDS
    assert all("receipt" in item for item in report["scenarios"])


def test_B03_reference_profile_has_eight_completed_receipts() -> None:
    report = run_practical_scripted(ROOT, profile_id="persona-reference-w18")
    assert report["execution_complete"] is True
    assert report["trial_id"] == "persona-reference-w18"
    assert len([item["receipt"] for item in report["scenarios"]]) == 8


def test_B04_comparison_rejects_candidate_start_end_mismatch() -> None:
    current = _receipt_group("current", candidate="candidate-start")
    with pytest.raises(EvaluationComparisonError) as caught:
        compare_receipt_groups(
            {"current": current, "persona-reference-w18": _receipt_group("persona-reference-w18", candidate="candidate-end")},
            scenario_set_identity="|".join(f"{item}:{item}" for item in PRACTICAL_IDS),
            experiment_id="w20-persona-router-practical-v1",
            candidate_identity="candidate-start",
            model_identity=current[0]["model_identity"],
        )
    assert caught.value.reason_code == EVALUATION_COMPARISON_INCOMPATIBLE


def test_B05_comparison_rejects_current_reference_candidate_mismatch() -> None:
    with pytest.raises(EvaluationComparisonError):
        compare_receipt_groups(
            {"current": _receipt_group("current", candidate="candidate-a"), "persona-reference-w18": _receipt_group("persona-reference-w18", candidate="candidate-b")},
            experiment_id="w20-persona-router-practical-v1",
        )


def test_B06_comparison_rejects_model_identity_mismatch() -> None:
    with pytest.raises(EvaluationComparisonError):
        compare_receipt_groups(
            {"current": _receipt_group("current", model={"model": "a"}), "persona-reference-w18": _receipt_group("persona-reference-w18", model={"model": "b"})},
            experiment_id="w20-persona-router-practical-v1",
        )


def test_B07_comparison_harvests_mapping_envelopes() -> None:
    groups = {"current": _receipt_group("current"), "persona-reference-w18": _receipt_group("persona-reference-w18")}
    comparison = compare_receipt_groups(
        groups,
        scenario_set_identity="|".join(f"{item}:{item}" for item in PRACTICAL_IDS),
        experiment_id="w20-persona-router-practical-v1",
        candidate_identity="candidate-a",
        model_identity=groups["current"][0]["model_identity"],
    )
    assert len(comparison.aggregates) == 2
    assert {item.run_count for item in comparison.aggregates} == {8}


def test_B08_clean_provider_budget_is_bounded() -> None:
    report = run_practical_scripted(ROOT, profile_id="current")
    assert report["execution_policy"]["provider_call_count"] <= 16
    assert report["execution_policy"]["live_model_used"] is False


def test_B09_fault_json_is_bounded_before_parse() -> None:
    with pytest.raises(FaultPlanV1Error):
        parse_fault_json("{" + (" " * (MAX_ENGINEERING_FAULT_JSON_BYTES + 1)))


def test_B10_duplicate_fault_call_index_is_rejected() -> None:
    with pytest.raises(FaultPlanV1Error):
        FaultPlanV1.from_dict({"schema_version": 1, "steps": [{"call_index": 1, "effect": "timeout"}, {"call_index": 1, "effect": "provider_error"}]})


def test_fault_timeout_effect_is_typed_and_exact() -> None:
    gateway = FaultingEvaluationGateway(SimpleNamespace(complete=lambda _request: None), FaultController(FaultPlanV1(steps=(FaultStepV1(1, FaultEffect.TIMEOUT),))))
    from agent.llm.errors import ModelTimeoutError

    with pytest.raises(ModelTimeoutError, match="Injected W20 model timeout\\."):
        gateway.complete(object())


def test_fault_provider_effect_is_typed_and_exact() -> None:
    gateway = FaultingEvaluationGateway(SimpleNamespace(complete=lambda _request: None), FaultController(FaultPlanV1(steps=(FaultStepV1(1, FaultEffect.PROVIDER_ERROR),))))
    from agent.llm.errors import ModelProviderError

    with pytest.raises(ModelProviderError, match="Injected W20 provider failure\\."):
        gateway.complete(object())


def test_fault_invalid_structured_response_is_exact() -> None:
    from agent.llm.contracts import ModelResponse

    gateway = FaultingEvaluationGateway(SimpleNamespace(complete=lambda _request: None), FaultController(FaultPlanV1(steps=(FaultStepV1(1, FaultEffect.INVALID_STRUCTURED_RESPONSE),))))
    response = gateway.complete(object())
    assert isinstance(response, ModelResponse)
    assert response.content == "{"
    assert response.provider_metadata == {"observed_provider_model_id": "scripted-evaluation"}


def test_fault_index_is_global_and_monotone() -> None:
    controller = FaultController(FaultPlanV1(steps=(FaultStepV1(2, FaultEffect.TIMEOUT),)))
    assert controller.next() is None
    assert controller.call_index == 1
    assert controller.next() is FaultEffect.TIMEOUT
    assert controller.call_index == 2
    assert controller.next() is None
    assert controller.call_index == 3


def test_fault_fingerprint_is_key_order_stable() -> None:
    first = parse_fault_json('{"schema_version":1,"steps":[{"call_index":2,"effect":"timeout"},{"call_index":1,"effect":"provider_error"}]}')
    second = parse_fault_json('{"steps":[{"effect":"provider_error","call_index":1},{"effect":"timeout","call_index":2}],"schema_version":1}')
    assert first.fingerprint == second.fingerprint
    assert first.canonical_json() == second.canonical_json()


def test_faulted_identity_uses_exact_experiment_and_trial() -> None:
    plan = FaultPlanV1(steps=(FaultStepV1(1, FaultEffect.TIMEOUT),))
    context = evaluation_context("current", experiment_id="w20-practical-v1-fault", trial_id=f"fault-{plan.fingerprint[:16]}")
    assert context.experiment_id == "w20-practical-v1-fault"
    assert context.trial_id == f"fault-{plan.fingerprint[:16]}"


def test_fault_summary_contains_only_safe_normalized_steps() -> None:
    plan = FaultPlanV1(steps=(FaultStepV1(1, FaultEffect.TIMEOUT),))
    summary = FaultController(plan).summary()
    assert summary["fault_steps"] == [{"call_index": 1, "effect": "timeout"}]
    assert "raw_json" not in summary


def test_fault_untriggered_calls_delegate_unchanged() -> None:
    seen = []
    wrapped = SimpleNamespace(complete=lambda request: seen.append(request) or "ok")
    gateway = FaultingEvaluationGateway(wrapped, FaultController(FaultPlanV1.empty()))
    assert gateway.complete("request") == "ok"
    assert seen == ["request"]


def test_B11_health_backend_reuses_offline_standalone_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.health.standalone as standalone

    monkeypatch.setattr(
        standalone,
        "run_standalone_health_check",
        lambda **kwargs: {"readiness": {"offline_ready": True}, "checks": [{"id": "paths", "status": "ok", "message": "hidden"}]},
    )
    context = SimpleNamespace(workspace=SimpleNamespace(root=ROOT), app_paths=SimpleNamespace())
    outcome = HealthBackend().execute(SimpleNamespace(parameters={}), context)
    assert outcome.summary["offline"] is True
    assert outcome.summary["write_report"] is False
    assert outcome.summary["checks"] == [{"id": "paths", "status": "ok"}]


def test_B12_inspection_has_the_closed_bounded_owner_shape() -> None:
    descriptor = production_registry().get("inspection.completed-run")
    assert descriptor is not None
    assert descriptor.parameter_schema["required"] == ("trace_run_id", "limit")
    assert descriptor.parameter_schema["additionalProperties"] is False
    assert InspectionBackend().inspect("trace", 1, SimpleNamespace(workspace=None)) is None


def test_inspection_projection_omits_adversarial_paths_and_freeform_payloads() -> None:
    sentinels = {
        "absolute": r"C:\W20-SENTINEL\private\source.py",
        "relative": "../W20-SENTINEL/relative/source.py",
        "changed": r"..\W20-SENTINEL\changed.txt",
        "cwd": r"C:\W20-SENTINEL\working-directory",
        "argv": "--W20-SENTINEL-ARGV secret-argument",
        "message": "W20_SENTINEL_FREEFORM_MESSAGE",
        "summary": "W20_SENTINEL_FREEFORM_SUMMARY",
        "exception": "W20_SENTINEL_EXCEPTION_ERROR_PROSE",
        "error": "W20_SENTINEL_ERROR_PROSE",
        "nested": "W20_SENTINEL_NESTED_DATA_MESSAGE",
    }
    activity = {
        "sequence": 3,
        "source": "runtime_event",
        "category": "plan",
        "kind": "plan_created",
        "severity": "info",
        "status": "completed",
        "title": sentinels["message"],
        "summary": sentinels["summary"],
        "data": {
            "absolute_path": sentinels["absolute"],
            "relative_path": sentinels["relative"],
            "changed_files": [sentinels["changed"]],
            "cwd": sentinels["cwd"],
            "argv": [sentinels["argv"]],
            "message": sentinels["message"],
            "summary": sentinels["summary"],
            "exception": sentinels["exception"],
            "error": sentinels["error"],
            "data": {"message": sentinels["nested"]},
        },
    }
    source = {
        "schema_version": 1,
        "run": {
            "run_id": sentinels["absolute"],
            "root_task_id": sentinels["relative"],
            "start_time": sentinels["cwd"],
            "active": False,
            "status": "complete",
            "completeness": "complete",
            "mode": "normal",
            "highest_sequence": 3,
            "semantic_count": 1,
            "final_outcome": {"message": sentinels["message"]},
            "liveness": {"reason": sentinels["exception"]},
        },
        "current": activity,
        "plan_steps": {
            "status": "available",
            "plan_count": 1,
            "step_event_count": 1,
            "plans": [{
                "sequence": 1,
                "kind": "plan_created",
                "step_count": 1,
                "plan": [{"path": sentinels["absolute"], "data": {"message": sentinels["nested"]}}],
            }],
            "steps": [{"sequence": 3, "kind": "step_completed", "summary": sentinels["summary"]}],
        },
        "timeline": [activity],
        "tools": {"status": "available", "count": 1, "events": [{"sequence": 3, "kind": "tool_end", "tool": sentinels["absolute"], "message": sentinels["message"]}]},
        "validation": {"status": "unavailable", "reason": sentinels["exception"]},
        "recovery": {"status": "unavailable", "reason": sentinels["error"]},
        "changes": {"status": "available", "count": 1, "events": [{"sequence": 3, "kind": "step_completed", "changed_files": [sentinels["changed"]]}]},
        "metrics": {"status": "available", "count": 1, "events": [{"sequence": 3, "kind": "model_call_completed", "message": sentinels["message"]}]},
        "warnings": [activity],
        "heartbeat": {"silence": "normal", "cwd": sentinels["cwd"], "active_context": {"message": sentinels["message"]}},
        "issues": [sentinels["exception"]],
        "convergence": {"status": "unavailable", "reason": sentinels["summary"]},
    }

    projected = project_inspection(source)
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    decoded = json.loads(serialized)

    def contains_sentinel(value: object, sentinel: str) -> bool:
        if isinstance(value, str):
            return sentinel in value
        if isinstance(value, dict):
            return any(
                contains_sentinel(key, sentinel) or contains_sentinel(item, sentinel)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_sentinel(item, sentinel) for item in value)
        return False

    assert projected["schema_version"] == 1
    assert projected["run"]["completeness"] == "complete"
    assert projected["plan_steps"]["plan_count"] == 1
    for sentinel in sentinels.values():
        assert not contains_sentinel(decoded, sentinel)
    for key in (
        "absolute_path", "relative_path", "path", "relative", "changed_files", "cwd",
        "argv", "message", "summary", "exception", "error", "data", "title", "reason",
    ):
        assert f'"{key}"' not in serialized


@pytest.mark.parametrize(
    "list_path",
    (
        ("timeline",),
        ("warnings",),
        ("plan_steps", "plans"),
        ("plan_steps", "steps"),
        ("tools", "events"),
        ("validation", "events"),
        ("recovery", "events"),
        ("changes", "events"),
        ("metrics", "events"),
        ("convergence", "events"),
    ),
)
def test_inspection_projection_honors_requested_list_limit(list_path: tuple[str, ...]) -> None:
    activity_rows = [
        {"sequence": index, "source": "runtime_event", "category": "tool", "kind": "tool_start"}
        for index in range(1, 4)
    ]
    event_rows = [{"sequence": index, "kind": "tool_start"} for index in range(1, 4)]
    plan_rows = [
        {"sequence": index, "kind": "plan_created", "step_count": index}
        for index in range(1, 4)
    ]
    step_rows = [{"sequence": index, "kind": "step_completed"} for index in range(1, 4)]
    if list_path[0] in {"timeline", "warnings"}:
        source = {list_path[0]: activity_rows}
    elif list_path == ("plan_steps", "plans"):
        source = {"plan_steps": {"status": "available", "plan_count": 3, "plans": plan_rows}}
    elif list_path == ("plan_steps", "steps"):
        source = {"plan_steps": {"status": "available", "step_event_count": 3, "steps": step_rows}}
    else:
        source = {list_path[0]: {"status": "available", "count": 3, "events": event_rows}}
    source["issues"] = ["issue-one", "issue-two", "issue-three"]

    for limit in (1, 2):
        projection = project_inspection(source, limit=limit)
        items = projection
        for key in list_path:
            items = items[key]
        assert isinstance(items, list)
        assert len(items) == limit
        assert len(items) <= 64
        assert projection["issues"] == 3


def test_inspection_backend_honors_requested_tools_event_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {
        "tools": {
            "status": "available",
            "count": 3,
            "events": [{"sequence": index, "kind": "tool_start"} for index in range(1, 4)],
        }
    }
    observed_limits: list[int] = []

    class SnapshotService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def snapshot(self, _trace_run_id: str, *, limit: int) -> SimpleNamespace:
            observed_limits.append(limit)
            return SimpleNamespace(to_dict=lambda: source)

    class BookmarkReader:
        def __init__(self, _workspace_paths: object) -> None:
            self.reader = None

    monkeypatch.setattr(inspection_backend, "InspectionService", SnapshotService)
    monkeypatch.setattr(inspection_backend, "BookmarkStore", BookmarkReader)
    context = SimpleNamespace(workspace=SimpleNamespace(workspace_id="workspace"))
    outcome = InspectionBackend(object()).execute(
        SimpleNamespace(parameters={"trace_run_id": "trace", "limit": 1}),
        context,
    )
    assert observed_limits == [1]
    tool_events = outcome.summary["tools"]["events"]
    assert isinstance(tool_events, list)
    assert len(tool_events) == 1


def test_B13_model_safe_has_exact_five_internal_tools() -> None:
    assert MODEL_SAFE_OPERATIONS == (
        "engineering_list",
        "engineering_describe",
        "engineering_result",
        "engineering_inspect_completed",
        "engineering_health_offline",
    )
    assert tuple(item.name for item in MODEL_SAFE_TOOL_DESCRIPTORS) == MODEL_SAFE_OPERATIONS
    assert all(item.result_data_schema is None and item.cacheable is False for item in MODEL_SAFE_TOOL_DESCRIPTORS)


def test_B14_non_model_safe_operations_are_hidden() -> None:
    hidden = EngineeringOperationViewV1("evaluation.practical-v1", EngineeringScope.SOURCE_REPOSITORY, production_registry().get("evaluation.practical-v1").effects, True, None, {}, model_safe=False)
    response = ModelSafeEngineering(SimpleNamespace(list_operations=lambda _context: (hidden,)), object()).engineering_list(SimpleNamespace())
    assert response.status is ModelSafeStatus.AVAILABLE
    assert response.data == {"operations": []}


def test_B15_workspace_mismatch_does_not_leak_result_existence() -> None:
    service = SimpleNamespace(describe=lambda _operation, _context: SimpleNamespace(model_safe=True, scope=EngineeringScope.WORKSPACE))
    store = SimpleNamespace(result_for_workspace=lambda _run_id, _context: _model_safe_result("other"))
    response = ModelSafeEngineering(service, store).engineering_result("engr-" + "a" * 32, SimpleNamespace(workspace=SimpleNamespace(workspace_id="trusted")))
    assert response.status is ModelSafeStatus.UNKNOWN
    assert response.reason_code == "ENGINEERING_RUN_NOT_FOUND"


def test_B16_health_projection_exposes_only_id_and_status() -> None:
    assert _project_health({"checks": [{"id": "paths", "status": "ok", "message": "secret"}]}) == [{"id": "paths", "status": "ok"}]


def test_B17_reference_projection_omits_label() -> None:
    reference = SimpleNamespace(owner="owner", kind="kind", reference_id="ref", sha256="a" * 64, label="secret")
    assert "label" not in _project_reference(reference)


def test_B18_model_safe_context_owns_no_cancellation_or_deadline() -> None:
    owner = SimpleNamespace(_app_paths=SimpleNamespace(), _workspace_context=SimpleNamespace(workspace_id="workspace"))
    context = _context_from_owner(owner)
    assert context.cancellation_token is None
    assert context.deadline_monotonic is None


def test_B19_inspection_backend_does_not_require_application_instance() -> None:
    assert "application" not in InspectionBackend.__init__.__annotations__
    assert InspectionBackend()._workspace_paths is None


def test_B20_internal_tools_survive_degraded_extension_composition() -> None:
    class Adapter:
        def __init__(self, descriptor):
            self._descriptor = descriptor

        def descriptors(self):
            return (self._descriptor,)

        def invoke(self, invocation):
            del invocation
            raise AssertionError("not executed")

    builtin = Adapter(ToolDescriptor("builtin", "builtin"))
    internal = Adapter(ToolDescriptor("engineering_list", "internal"))
    result = WorkspaceToolRegistryComposer().compose(
        builtin,
        ExtensionRuntimeMaterialization(),
        internal_adapters=(internal,),
    )
    assert "builtin" in result.registry.names()
    assert "engineering_list" in result.registry.names()


def test_B21_discovery_duplicate_entry_fails_closed() -> None:
    item = _entry("cli:duplicate", "duplicate").entry
    with pytest.raises(ValueError):
        DiscoveryCatalog((item, item))


def test_B22_empty_query_browse_is_deterministic() -> None:
    catalog = DiscoveryCatalog((_entry("cli:a", "a").entry, _entry("cli:b", "b").entry))
    service = DiscoveryService(catalog)
    first = service.search("")
    second = service.search("")
    assert first.to_dict() == second.to_dict()
    assert DISCOVERY_QUERY_EMPTY in first.reasons


def test_B23_discovery_normalizes_nfkc_casefold_and_whitespace() -> None:
    assert normalize_query("  A\u212A  B\n") == "ak b"


def test_B24_exact_precedes_prefix_token_substring_and_fuzzy() -> None:
    entries = (
        DiscoveryEntryV1(1, "cli:exact", DiscoverySourceKind.CLI_COMMAND, "Run", "desc", "run"),
        DiscoveryEntryV1(1, "cli:prefix", DiscoverySourceKind.CLI_COMMAND, "Run all", "desc", "run all"),
        DiscoveryEntryV1(1, "cli:token", DiscoverySourceKind.CLI_COMMAND, "Token", "desc", "token run"),
        DiscoveryEntryV1(1, "cli:substring", DiscoverySourceKind.CLI_COMMAND, "Substring", "desc", "rerun"),
    )
    display, _ = rank_entries(entries, "run", availability_by_entry_id={item.entry_id: DiscoveryAvailability(True) for item in entries})
    assert display[0].match_kind is DiscoveryMatchKind.EXACT
    field_entries = (
        DiscoveryEntryV1(1, "cli:preferred", DiscoverySourceKind.CLI_COMMAND, "title-preferred", "description-preferred", "needle"),
        DiscoveryEntryV1(1, "cli:alias", DiscoverySourceKind.CLI_COMMAND, "title-alias", "description-alias", "preferred-alias", aliases=("needle",)),
        DiscoveryEntryV1(1, "cli:title", DiscoverySourceKind.CLI_COMMAND, "needle", "description-title", "preferred-title"),
        DiscoveryEntryV1(1, "cli:keyword", DiscoverySourceKind.CLI_COMMAND, "title-keyword", "description-keyword", "preferred-keyword", keywords=("needle",)),
        DiscoveryEntryV1(1, "cli:description", DiscoverySourceKind.CLI_COMMAND, "title-description", "needle", "preferred-description"),
        DiscoveryEntryV1(1, "cli:example", DiscoverySourceKind.CLI_COMMAND, "title-example", "description-example", "preferred-example", examples=("needle",)),
    )
    ordered, _ = rank_entries(
        field_entries,
        "needle",
        availability_by_entry_id={item.entry_id: DiscoveryAvailability(True) for item in field_entries},
    )
    assert tuple(item.entry.entry_id for item in ordered) == tuple(
        f"cli:{field}" for field in ("preferred", "alias", "title", "keyword", "description", "example")
    )


def test_B25_fuzzy_matching_uses_the_frozen_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.discovery.ranking as ranking

    observed: dict[str, object] = {}

    class Matcher:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            observed["autojunk"] = kwargs.get("autojunk")

        def ratio(self) -> float:
            return 0.7

    monkeypatch.setattr(ranking, "SequenceMatcher", Matcher)
    entry = DiscoveryEntryV1(1, "cli:fuzzy", DiscoverySourceKind.CLI_COMMAND, "abce", "desc", "abce")
    candidate = score_entry(entry, "abcd", availability=DiscoveryAvailability(True), frecency_score=0)
    assert candidate.match_kind is DiscoveryMatchKind.FUZZY
    assert candidate.local_score["fuzzy_score"] >= 650
    assert observed["autojunk"] is False


def test_B26_discovery_tie_breaks_by_title_and_entry_id() -> None:
    entries = (
        DiscoveryEntryV1(1, "cli:z", DiscoverySourceKind.CLI_COMMAND, "Same", "desc", "same"),
        DiscoveryEntryV1(1, "cli:a", DiscoverySourceKind.CLI_COMMAND, "Same", "desc", "same"),
    )
    display, _ = rank_entries(entries, "same", availability_by_entry_id={item.entry_id: DiscoveryAvailability(True) for item in entries})
    assert display[0].entry.entry_id == "cli:a"


def test_B27_availability_maps_each_busy_policy() -> None:
    assert availability_for_binding(SimpleNamespace(busy_policy="ALWAYS_LOCAL"), None).available is True
    assert availability_for_binding(SimpleNamespace(busy_policy="IDLE_ONLY"), DiscoveryControllerState.RUNNING).disabled_reason == "DISCOVERY_REQUIRES_IDLE"
    assert availability_for_binding(SimpleNamespace(busy_policy="ATTENTION_ONLY"), DiscoveryControllerState.WAITING_ATTENTION).available is True
    assert availability_for_binding(SimpleNamespace(busy_policy="AGENTIC_SUBMIT"), DiscoveryControllerState.TERMINAL).available is True


def test_B28_missing_availability_is_conservative() -> None:
    entry = _entry("action:missing", "missing", source=DiscoverySourceKind.ACTION).entry
    result = DiscoveryService(DiscoveryCatalog((entry,))).search("missing")
    assert result.candidates[0].available is False
    assert DISCOVERY_AVAILABILITY_UNKNOWN in result.reasons


def test_B29_frecency_is_bounded_and_merged(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    duplicate_path = tmp_path / "duplicates.json"
    duplicate_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {"entry_id": "cli:duplicate", "use_count": 5, "last_used_utc": "2025-12-20T00:00:00Z"},
                    {"entry_id": "cli:duplicate", "use_count": 2, "last_used_utc": "2025-12-31T00:00:00Z"},
                ],
            }
        ),
        encoding="utf-8",
    )
    merged = FrecencyStore(duplicate_path, clock=lambda: now)
    assert merged.snapshot() == (
        {"entry_id": "cli:duplicate", "use_count": 5, "last_used_utc": "2025-12-31T00:00:00Z"},
    )
    store = FrecencyStore(tmp_path / "frecency.json", clock=lambda: now)
    for index in range(300):
        store.record(f"cli:{index}", now=now)
    assert len(store.snapshot()) <= 256
    assert store.score("cli:299") > 0


def test_B30_frecency_ignores_future_and_corrupt_documents(tmp_path: Path) -> None:
    path = tmp_path / "frecency.json"
    future = (datetime(2026, 1, 2, tzinfo=timezone.utc)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps({"schema_version": 1, "entries": [{"entry_id": "cli:future", "use_count": 3, "last_used_utc": future}]}), encoding="utf-8")
    store = FrecencyStore(path, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert store.score("cli:future") == 0
    path.write_text("{not-json", encoding="utf-8")
    corrupt = FrecencyStore(path)
    assert corrupt.snapshot() == ()
    assert corrupt.diagnostics == ("DISCOVERY_FRECENCY_CORRUPT",)


def test_B31_frecency_never_persists_query_text(tmp_path: Path) -> None:
    path = tmp_path / "frecency.json"
    store = FrecencyStore(path)
    store.record("action:discovery.commands")
    document = path.read_text(encoding="utf-8")
    assert "some user query" not in document
    assert "action:discovery.commands" in document


def test_B32_commands_binding_is_the_canonical_action_owner() -> None:
    binding = DEFAULT_CLI_ACTION_REGISTRY.binding_for_action("discovery.commands")
    assert binding.preferred_path == ("/commands",)
    assert binding.aliases == ()
    assert binding.routing_kind == "CONTROL"
    assert binding.busy_policy == "ALWAYS_LOCAL"
    assert binding.busy_submit == "NOT_APPLICABLE"
    assert binding.model_use == "NEVER"
    assert binding.mutation_policy == "UI_SESSION"
    assert binding.handler_owner == "agent.interfaces.cli.discovery_ui.commands_action"


def test_B33_f2_palette_reuses_the_active_buffer_without_nested_prompt() -> None:
    class Buffer:
        text = "draft text"

        def validate_and_handle(self):
            self.validated = True

    buffer = Buffer()
    subject = SimpleNamespace(session=SimpleNamespace(default_buffer=buffer), _palette_return_draft=None)
    InteractiveShell.request_command_palette(subject)
    assert buffer.text == "/commands"
    assert subject._palette_return_draft == "draft text"


def test_B34_palette_action_selection_restores_action_draft() -> None:
    class Shell:
        def __init__(self):
            self.value = "draft"

        def take_palette_return_draft(self):
            return self.value

        def set_draft(self, value):
            self.value = value

        def print_background(self, _value):
            raise AssertionError("action selection should draft, not execute")

    candidate = _entry("action:demo", "/demo", source=DiscoverySourceKind.ACTION)
    shell = Shell()
    select_palette_entry(candidate, SimpleNamespace(shell=shell))
    assert shell.value == "/demo"


def test_B35_recent_workspaces_are_newest_first_and_skip_stale(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    payload = {
        "schema_version": 1,
        "items": [
            {"path": str(second), "last_opened_utc": "2026-01-01T00:00:02Z"},
            {"path": str(first), "last_opened_utc": "2026-01-01T00:00:01Z"},
            {"path": str(tmp_path / "missing"), "last_opened_utc": "2026-01-01T00:00:03Z"},
        ],
    }
    recent = tmp_path / "recent.json"
    recent.write_text(json.dumps(payload), encoding="utf-8")
    paths = SimpleNamespace(recent_workspaces_file=recent)
    assert load_recent_workspaces(paths) == (second, first)


def test_B36_path_completion_delegates_path_values_to_filesystem() -> None:
    seen = []
    values = complete_command("test run --workspace", filesystem=lambda option: seen.append(option) or ("/tmp/workspace",))
    assert values == ("/tmp/workspace",)
    assert seen == ["--workspace"]


def test_B37_semantic_false_never_constructs_a_gateway() -> None:
    calls = []
    gateway = SemanticCommandDiscovery(gateway_factory=lambda _config: calls.append(True))
    result = gateway.rerank(_result((_entry("cli:a", "a"),), requested=False), semantic_allowed=True, candidate_pool=())
    assert result.semantic_used is False
    assert calls == []


def test_B38_semantic_unauthorized_makes_zero_calls() -> None:
    calls = []
    gateway = SemanticCommandDiscovery(gateway_factory=lambda _config: calls.append(True))
    result = gateway.rerank(_result((_entry("cli:a", "a"),)), semantic_allowed=False, candidate_pool=(_entry("cli:a", "a"),))
    assert DISCOVERY_SEMANTIC_NOT_AUTHORIZED in result.reasons
    assert calls == []


def test_B39_semantic_uses_one_complete_and_merges_offered_ids() -> None:
    from agent.llm.contracts import ModelResponse, ProviderCapabilities, StructuredOutputMode

    class Gateway:
        model = "fake"

        def __init__(self):
            self.calls = 0
            self.capabilities = ProviderCapabilities(structured_output_modes=(StructuredOutputMode.GBNF,))

        def complete(self, _request):
            self.calls += 1
            return ModelResponse('{"candidate_ids":["cli:b"]}')

    first, second = _entry("cli:a", "a"), _entry("cli:b", "b", available=False, reason="DISCOVERY_REQUIRES_IDLE")
    fake = Gateway()
    subject = SemanticCommandDiscovery(gateway_factory=lambda _config: fake)
    result = subject.rerank(_result((first, second)), semantic_allowed=True, candidate_pool=(first, second))
    assert fake.calls == 1
    assert result.semantic_used is True
    assert result.candidates[0].entry.entry_id == "cli:b"
    assert result.candidates[0].available is False


def test_B40_semantic_payload_is_bounded() -> None:
    candidate = _entry("cli:a", "a")
    payload, offered = SemanticCommandDiscovery._payload("query", tuple(candidate for _ in range(64)))
    assert len(payload) <= MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES
    assert len(offered) <= 64
    assert COMMAND_DISCOVERY_SCHEMA["additionalProperties"] is False
    with pytest.raises(ValueError):
        validate_command_discovery_response({"candidate_ids": ["cli:other"]}, ["cli:a"])


def test_semantic_payload_tail_trimming_preserves_prefix_and_projection_bounds() -> None:
    candidates = tuple(
        DiscoveryCandidateV1(
            DiscoveryEntryV1(
                1,
                f"cli:long-{index}",
                DiscoverySourceKind.CLI_COMMAND,
                "t" * 128,
                "d" * 512,
                "p" * 512,
                aliases=("a" * 192,),
                keywords=("k" * 96,),
                examples=("e" * 256,),
            ),
            True,
            None,
            DiscoveryMatchKind.FUZZY,
            {"match_kind_rank": 1, "field_rank": 0, "token_score": 0, "fuzzy_score": 700},
            0,
        )
        for index in range(64)
    )
    payload, offered = SemanticCommandDiscovery._payload("long query", candidates)
    document = json.loads(payload)
    assert len(payload) <= MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES
    assert 0 < len(offered) < len(candidates)
    assert tuple(item.entry.entry_id for item in offered) == tuple(
        item.entry.entry_id for item in candidates[: len(offered)]
    )
    assert tuple(item["entry_id"] for item in document["candidates"]) == tuple(
        item.entry.entry_id for item in offered
    )
    assert all(len(item["description"]) <= 512 for item in document["candidates"])


def test_B41_semantic_returned_ids_are_independently_validated() -> None:
    with pytest.raises(ValueError):
        validate_command_discovery_response({"candidate_ids": ["cli:a", "cli:a"]}, ["cli:a"])
    long_id = "cli:" + ("x" * 189)
    with pytest.raises(ValueError):
        validate_command_discovery_response({"candidate_ids": [long_id]}, [long_id])


def test_B42_semantic_merge_never_changes_local_availability() -> None:
    from agent.llm.contracts import ModelResponse, ProviderCapabilities, StructuredOutputMode

    class Gateway:
        capabilities = ProviderCapabilities(structured_output_modes=(StructuredOutputMode.GBNF,))
        model = "fake"

        def complete(self, _request):
            return ModelResponse('{"candidate_ids":[]}')

    disabled = _entry("cli:disabled", "disabled", available=False, reason="DISCOVERY_REQUIRES_IDLE")
    result = SemanticCommandDiscovery(gateway_factory=lambda _config: Gateway()).rerank(
        _result((disabled,)), semantic_allowed=True, candidate_pool=(disabled,)
    )
    assert result.semantic_used is True
    assert result.candidates[0].available is False
    assert result.candidates[0].disabled_reason == "DISCOVERY_REQUIRES_IDLE"


def test_B43_semantic_invalid_response_falls_back_locally() -> None:
    from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode

    class Gateway:
        capabilities = ProviderCapabilities(structured_output_modes=(StructuredOutputMode.GBNF,))
        model = "fake"

        def complete(self, _request):
            return SimpleNamespace(content="{")

    result = SemanticCommandDiscovery(gateway_factory=lambda _config: Gateway()).rerank(
        _result((_entry("cli:a", "a"),)), semantic_allowed=True, candidate_pool=(_entry("cli:a", "a"),)
    )
    assert result.semantic_used is False
    assert DISCOVERY_SEMANTIC_INVALID_RESPONSE in result.reasons


def test_B44_semantic_provider_failure_is_unavailable_and_empty_pool_is_zero_calls() -> None:
    calls = []
    failing = SemanticCommandDiscovery(gateway_factory=lambda _config: calls.append(True) or (_ for _ in ()).throw(RuntimeError()))
    candidate = _entry("cli:a", "a")
    result = failing.rerank(_result((candidate,)), semantic_allowed=True, candidate_pool=(candidate,))
    assert DISCOVERY_SEMANTIC_UNAVAILABLE in result.reasons
    assert len(calls) == 1
    assert DISCOVERY_NO_MATCH in failing.rerank(_result(()), semantic_allowed=True, candidate_pool=()).reasons


def test_B45_cli_parser_flags_are_canonical() -> None:
    parsed = build_parser().parse_args(["commands", "help", "--semantic", "--json", "--plain"])
    assert parsed.semantic is True and parsed.json_output is True and parsed.plain is True
    assert build_parser().parse_args(["test", "compare"]).test_command == "compare"


def test_B46_commands_projection_is_local_and_json_serializable() -> None:
    catalog, availability = build_catalog()
    result = DiscoveryService(catalog).search("commands", context=DiscoveryExecutionContext(availability_by_entry_id=availability))
    document = result.to_dict()
    assert json.loads(json.dumps(document, ensure_ascii=False)) == document
    assert document["semantic_used"] is False


def test_engineering_views_reach_commands_discovery_without_application(tmp_path: Path) -> None:
    from agent.engineering.cli import discovery_operation_views
    from agent.interfaces.cli.discovery_projection import discover_commands
    from agent.runtime.paths import AppPaths

    paths = AppPaths.discover(tmp_path / "home")
    result = discover_commands(
        "health.offline",
        app_paths=paths,
        engineering_views=discovery_operation_views(paths),
    )
    engineering = next(item for item in result.candidates if item.entry.entry_id == "engineering:health.offline")
    assert engineering.available is False
    assert engineering.disabled_reason == "ENGINEERING_WORKSPACE_REQUIRED"


def test_B47_powershell_completion_is_parser_and_registry_derived() -> None:
    commands = completion_commands(engineering_operation_ids=("health.offline",))
    assert "test run health.offline" in commands
    assert all(not item.startswith("/") for item in commands)
    assert complete_command("test run health", engineering_operation_ids=("health.offline",)) == ("health.offline",)


def test_B48_internal_adapters_are_present_before_extensions() -> None:
    from agent.engineering.model_safe import build_model_safe_internal_adapters

    owner = ModelSafeEngineering(SimpleNamespace(), object())
    adapters = build_model_safe_internal_adapters(owner)
    assert tuple(adapter.descriptors()[0].name for adapter in adapters) == MODEL_SAFE_OPERATIONS


def test_native_completion_uses_ast_cursor_and_is_deterministic() -> None:
    first = powershell_completion_script(engineering_operation_ids=("health.offline",))
    second = powershell_completion_script(engineering_operation_ids=("health.offline",))
    assert first == second
    assert "Register-ArgumentCompleter -Native" in first
    assert "$commandAst" in first and "$cursorPosition" in first
    assert "CommandElements" in first
    assert "Select-Object -SkipLast" not in first


def test_completion_returns_current_token_and_delegates_only_path_values() -> None:
    assert "commands" in complete_command("")
    assert "--semantic" in complete_command("commands --")
    assert complete_command("test run health", engineering_operation_ids=("health.offline",)) == ("health.offline",)
    seen: list[str] = []
    assert complete_command("commands --config ", filesystem=lambda option: seen.append(option) or ("config.toml",)) == ("config.toml",)
    assert seen == ["--config"]


def test_semantic_projection_reuses_selected_profile_without_application_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import agent.interfaces.cli.discovery_projection as projection

    selected_profile = object()
    observed: dict[str, object] = {}

    class Repository:
        def __init__(self, _paths, config_path=None):
            observed["config_path"] = config_path

        def load(self, *, overrides=None):
            observed["overrides"] = overrides
            return SimpleNamespace(model_profile=selected_profile)

    class SemanticOwner:
        def __init__(self, *, gateway_config):
            observed["profile"] = gateway_config

    monkeypatch.setattr("agent.runtime.config_repository.ConfigRepository", Repository)
    monkeypatch.setattr("agent.runtime.paths.AppPaths.discover", lambda app_home=None: SimpleNamespace(discovery_frecency_file=tmp_path / "frecency.json"))
    monkeypatch.setattr(projection, "SemanticCommandDiscovery", SemanticOwner)
    projection.build_service(
        semantic=True,
        config_path="selected.json",
        profile="selected-profile",
    )
    assert observed == {
        "config_path": "selected.json",
        "overrides": {"default_model_profile": "selected-profile"},
        "profile": selected_profile,
    }


def test_missing_semantic_config_falls_back_without_first_run_or_model_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import agent.interfaces.cli.discovery_projection as projection

    class MissingRepository:
        def __init__(self, *_args, **_kwargs):
            pass

        def load(self, **_kwargs):
            raise RuntimeError("missing")

    monkeypatch.setattr("agent.runtime.config_repository.ConfigRepository", MissingRepository)
    home = tmp_path / "missing-home"
    before = home.exists()
    result = projection.discover_commands("commands", semantic=True, home=home)
    assert result.semantic_used is False
    assert home.exists() is before


def test_static_cli_projection_marks_parser_commands_explicitly_available() -> None:
    catalog, availability = build_catalog()
    commands = next(
        item for item in catalog.entries() if item.preferred_invocation == "commands"
    )
    assert availability[commands.entry_id] == DiscoveryAvailability(True)


def test_commands_local_cli_uses_zero_model_calls_and_no_first_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent.interfaces.cli.app as cli_app
    import agent.interfaces.cli.discovery_projection as projection

    def fail_if_semantic_is_constructed(**_kwargs: object) -> None:
        raise AssertionError("local commands must not construct semantic Discovery")

    monkeypatch.setattr(projection, "SemanticCommandDiscovery", fail_if_semantic_is_constructed)
    home = tmp_path / "local-home"
    args = build_parser().parse_args(["commands", "--json", "--home", str(home)])

    assert cli_app._run_commands(args) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert document["semantic_requested"] is False
    assert document["semantic_used"] is False
    assert home.exists() is False


def test_standalone_commands_projects_explicit_workspace_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent.interfaces.cli.app as cli_app
    import agent.interfaces.cli.discovery_projection as projection

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("commands must not bootstrap application, config, or model")

    monkeypatch.setattr(cli_app, "_create_application", unexpected)
    monkeypatch.setattr(cli_app.first_run, "recover_first_run_config", unexpected)
    monkeypatch.setattr("agent.runtime.config_repository.ConfigRepository.load", unexpected)
    monkeypatch.setattr(projection, "SemanticCommandDiscovery", unexpected)
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for extra, expected_reason in (([], "ENGINEERING_WORKSPACE_REQUIRED"), (["--workspace", str(workspace)], None)):
        args = build_parser().parse_args(["commands", "health.offline", "--json", "--home", str(home), *extra])
        assert cli_app._run_commands(args) == 0
        payload = json.loads(capsys.readouterr().out)
        entry = next(item for item in payload["candidates"] if item["entry"]["entry_id"] == "engineering:health.offline")
        assert entry["disabled_reason"] == expected_reason
        assert entry["available"] is (expected_reason is None)
    assert not home.exists()


def test_commands_semantic_cli_propagates_profile_and_bounds_one_model_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent.discovery.semantic as semantic_module
    import agent.interfaces.cli.app as cli_app
    import agent.interfaces.cli.discovery_projection as projection
    from agent.llm.contracts import ModelResponse, ProviderCapabilities, StructuredOutputMode
    from agent.runtime.context import TaskExecutionContext

    selected_profile = object()
    observed: dict[str, object] = {}

    class Repository:
        def __init__(self, _paths: object, config_path: object = None) -> None:
            observed["config_path"] = config_path

        def load(self, *, overrides: object = None) -> object:
            observed["overrides"] = overrides
            return SimpleNamespace(model_profile=selected_profile)

    class Gateway:
        model = "fake"
        provider_name = "fake"
        capabilities = ProviderCapabilities(
            structured_output_modes=(StructuredOutputMode.GBNF,)
        )

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, _request: object) -> ModelResponse:
            self.calls += 1
            return ModelResponse('{"candidate_ids":[]}')

    gateway = Gateway()

    def semantic_owner(*, gateway_config: object) -> SemanticCommandDiscovery:
        observed["profile"] = gateway_config
        return SemanticCommandDiscovery(
            gateway_factory=lambda _config: gateway,
            gateway_config=gateway_config,
        )

    monkeypatch.setattr(projection, "SemanticCommandDiscovery", semantic_owner)
    monkeypatch.setattr("agent.runtime.config_repository.ConfigRepository", Repository)
    monkeypatch.setattr(
        "agent.runtime.paths.AppPaths.discover",
        lambda app_home=None: SimpleNamespace(
            discovery_frecency_file=tmp_path / "frecency.json"
        ),
    )

    original_for_context = semantic_module.ModelCallService.for_context.__func__

    def capture_context(cls: object, context: TaskExecutionContext) -> object:
        limits = context.limits
        observed["max_model_calls"] = limits.max_model_calls
        return original_for_context(cls, context)

    monkeypatch.setattr(
        semantic_module.ModelCallService,
        "for_context",
        classmethod(capture_context),
    )
    original_complete = semantic_module.ModelCallService.complete

    def capture_operation(self: object, request: object, *, operation: str = "model_call") -> object:
        observed["operation"] = operation
        return original_complete(self, request, operation=operation)

    monkeypatch.setattr(semantic_module.ModelCallService, "complete", capture_operation)

    home = tmp_path / "semantic-home"
    args = build_parser().parse_args(
        [
            "commands",
            "--semantic",
            "--json",
            "--home",
            str(home),
            "--config",
            "selected.json",
            "--profile",
            "selected-profile",
        ]
    )

    assert cli_app._run_commands(args) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert document["semantic_requested"] is True
    assert document["semantic_used"] is True
    assert gateway.calls == 1
    assert observed == {
        "config_path": "selected.json",
        "overrides": {"default_model_profile": "selected-profile"},
        "profile": selected_profile,
        "max_model_calls": 1,
        "operation": "command_discovery",
    }
    assert home.exists() is False


def test_powershell_completion_cli_is_deterministic_and_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent.discovery.semantic as semantic_module
    import agent.interfaces.cli.app as cli_app

    def fail_if_model_is_touched(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PowerShell completion must not touch a model")

    monkeypatch.setattr(semantic_module, "create_model_gateway", fail_if_model_is_touched)
    home = tmp_path / "completion-home"
    args = build_parser().parse_args(
        ["completion", "powershell", "--home", str(home)]
    )

    assert cli_app._run_completion(args) == 0
    first = capsys.readouterr()
    assert cli_app._run_completion(args) == 0
    second = capsys.readouterr()
    assert first.err == second.err == ""
    assert first.out == second.out
    assert "Register-ArgumentCompleter -Native" in first.out
    assert "llm-agent" in first.out
    assert "$commandAst" in first.out and "$cursorPosition" in first.out
    assert "CommandElements" in first.out
    assert "CompletionResult" in first.out
    assert "Get-ChildItem" in first.out
    assert home.exists() is False
