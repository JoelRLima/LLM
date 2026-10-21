from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.agent.engineering.contracts import EngineeringCaller, EngineeringPermission
from llm_agent.agent.engineering.policy import fault_plan_error
from llm_agent.agent.evaluation.contracts import (
    MAX_ENGINEERING_FAULT_JSON_BYTES,
    MAX_FAULTS_PER_PLAN,
    FaultEffect,
    FaultPlanV1,
    FaultStep,
)
from llm_agent.agent.evaluation.evaluation_identity import candidate_identity, candidate_identity_string
from llm_agent.application.actions import engineering as engineering_actions
from llm_agent.application.actions.evaluation import (
    EngineeringFaultInputError,
    compare_practical_evaluation,
    prepare_engineering_fault,
    resolve_source_candidate_identity,
)
from llm_agent.interfaces.cli import app as cli_app
from llm_agent.interfaces.cli import engineering


@pytest.mark.parametrize(
    "steps, expected_permission",
    [([], False), ([{"call_index": 2, "effect": "timeout"}, {"call_index": 1, "effect": "provider_error"}], True)]
    + [([{"call_index": 1, "effect": effect.value}], True) for effect in FaultEffect],
)
def test_fault_input_returns_canonical_normalized_projection(steps, expected_permission) -> None:
    raw = {"schema_version": 1, "steps": steps}
    result = prepare_engineering_fault(None, {"fault": raw})
    canonical = FaultPlanV1.from_dict(raw)
    assert result.fault == canonical.to_dict()
    assert result.requires_inject_fault_permission is expected_permission


def test_fault_json_route_preserves_canonical_projection_and_cause() -> None:
    result = prepare_engineering_fault(
        '{"schema_version":1,"steps":[{"call_index":2,"effect":"timeout"},{"call_index":1,"effect":"provider_error"}]}',
        {},
    )
    assert result.fault == FaultPlanV1(
        steps=(FaultStep(1, FaultEffect.PROVIDER_ERROR), FaultStep(2, FaultEffect.TIMEOUT))
    ).to_dict()
    with pytest.raises(EngineeringFaultInputError, match="fault JSON is invalid") as caught:
        prepare_engineering_fault("{", {})
    assert caught.value.__cause__ is not None


def test_fault_application_error_keeps_cli_value_error_projection(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli_app, "build_parser", lambda: SimpleNamespace(parse_args=lambda _argv: Namespace(json_output=True)))

    def fail(_args):
        raise EngineeringFaultInputError("fault JSON is invalid")

    monkeypatch.setattr(cli_app, "_dispatch", fail)
    assert cli_app.main([]) == 2
    assert '"error": "fault JSON is invalid"' in capsys.readouterr().out
    with pytest.raises(EngineeringFaultInputError, match="^unsupported fault plan schema$") as caught:
        prepare_engineering_fault('{"schema_version":2,"steps":[]}', {})
    assert caught.value.__cause__ is not None


def test_fault_boundaries_and_utf8_size_message() -> None:
    steps = [
        {"call_index": index, "effect": "timeout"}
        for index in range(1, MAX_FAULTS_PER_PLAN + 1)
    ]
    result = prepare_engineering_fault(None, {"fault": {"schema_version": 1, "steps": steps}})
    assert result.fault["steps"][0]["call_index"] == 1
    assert result.fault["steps"][-1]["call_index"] == MAX_FAULTS_PER_PLAN
    with pytest.raises(EngineeringFaultInputError, match="fault JSON exceeds its bound"):
        prepare_engineering_fault("é" * (MAX_ENGINEERING_FAULT_JSON_BYTES // 2 + 1), {})


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": 1},
        {"schema_version": 1, "steps": [], "extra": True},
        {"schema_version": True, "steps": []},
        {"schema_version": 2, "steps": []},
        {"schema_version": 1, "steps": [{"call_index": True, "effect": "timeout"}]},
        {"schema_version": 1, "steps": [{"call_index": 0, "effect": "timeout"}]},
        {"schema_version": 1, "steps": [{"call_index": 1, "effect": "timeout", "extra": True}]},
        {"schema_version": 1, "steps": [{"call_index": 1, "effect": "unknown"}]},
        {"schema_version": 1, "steps": [{"call_index": index, "effect": "timeout"} for index in range(1, 18)]},
    ],
)
def test_parameter_fault_failures_keep_public_message(value) -> None:
    with pytest.raises(EngineeringFaultInputError, match="^invalid Engineering fault plan$") as caught:
        prepare_engineering_fault(None, {"fault": value})
    assert caught.value.__cause__ is not None


def test_source_identity_matches_canonical_pair() -> None:
    root = Path(__file__).resolve().parents[3]
    assert resolve_source_candidate_identity(root) == candidate_identity_string(candidate_identity(root))


def test_comparison_projection_preserves_document_and_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {"status": "comparable", "aggregate": {"runs": 8}}
    monkeypatch.setattr("llm_agent.application.actions.evaluation._compare_practical_profiles", lambda _root: document)
    assert compare_practical_evaluation("repo").document is document
    assert compare_practical_evaluation("repo").exit_code == 0
    monkeypatch.setattr(
        "llm_agent.application.actions.evaluation._compare_practical_profiles",
        lambda _root: {"status": "not_comparable"},
    )
    assert compare_practical_evaluation("repo").exit_code == 1


def test_missing_source_projection_and_cli_context(monkeypatch: pytest.MonkeyPatch) -> None:
    result = compare_practical_evaluation(None)
    assert result.document["query_status"] == "blocked"
    assert result.document["code"] == "ENGINEERING_SOURCE_REPOSITORY_REQUIRED"
    assert result.exit_code == 1
    monkeypatch.setattr(engineering, "discover_source_repository_context", lambda: None)
    assert engineering.discover_source_repository_context() is None


def test_engineering_reconstructs_and_authorizes_request_mapping() -> None:
    normalized = prepare_engineering_fault(
        None,
        {"fault": {"schema_version": 1, "steps": [{"call_index": 1, "effect": "timeout"}]}},
    )
    context = SimpleNamespace(
        fault_plan=None,
        permissions=frozenset({EngineeringPermission.INJECT_FAULT}),
        caller=EngineeringCaller.HUMAN_INTERACTIVE,
    )
    assert fault_plan_error(
        context, {"fault": normalized.fault}, operation_id="evaluation.practical-v1"
    ) is None
    denied = SimpleNamespace(
        fault_plan=None,
        permissions=frozenset(),
        caller=EngineeringCaller.HUMAN_INTERACTIVE,
    )
    error = fault_plan_error(denied, {"fault": normalized.fault}, operation_id="evaluation.practical-v1")
    assert error is not None
    assert error.code == "ENGINEERING_FAULT_INJECTION_NOT_AUTHORIZED"
    model_safe = SimpleNamespace(
        fault_plan=None,
        permissions=frozenset({EngineeringPermission.INJECT_FAULT}),
        caller=EngineeringCaller.MCP,
    )
    safe_error = fault_plan_error(
        model_safe, {"fault": normalized.fault}, operation_id="evaluation.practical-v1"
    )
    assert safe_error is not None
    assert safe_error.code == "ENGINEERING_FAULT_INJECTION_NOT_AUTHORIZED"


def test_evaluation_backend_reconstructs_request_mapping_without_context_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_agent.agent.engineering.backends.evaluation import EvaluationBackend
    from llm_agent.agent.engineering.contracts import EngineeringBackendStatus
    from llm_agent.agent.evaluation import practical

    observed = {}

    def run(_root, *, profile_id, fault_plan):
        observed.update(profile_id=profile_id, fault_plan=fault_plan)
        return {
            "summary": {"failed": 0, "passed": 8},
            "execution_complete": True,
            "candidate_unchanged": True,
            "scenarios": [],
        }

    monkeypatch.setattr(practical, "run_practical_scripted", run)
    prepared = prepare_engineering_fault(
        None,
        {"fault": {"schema_version": 1, "steps": [{"call_index": 1, "effect": "timeout"}]}},
    )
    request = SimpleNamespace(parameters={"profile": "current", "fault": prepared.fault})
    context = SimpleNamespace(source_repository=SimpleNamespace(root=Path("repo")), fault_plan=None)
    result = EvaluationBackend().execute(request, context)
    assert result.status is EngineeringBackendStatus.SUCCEEDED
    assert isinstance(observed["fault_plan"], FaultPlanV1)
    assert observed["fault_plan"].to_dict() == prepared.fault


def test_cli_passes_canonical_mapping_and_keeps_context_plan_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_agent.agent.engineering.contracts import EngineeringTerminalStatus

    captured = {}
    paths = SimpleNamespace(home_dir=Path("home"))

    class FakeService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def preflight(self, request, context):
            captured["request"] = request
            captured["context"] = context
            return object()

        def run(self, request, context):
            captured["request"] = request
            captured["context"] = context
            return SimpleNamespace(
                status=EngineeringTerminalStatus.SUCCEEDED,
                to_dict=lambda: {"status": "succeeded"},
            )

    from contextlib import contextmanager

    @contextmanager
    def fake_startup_operation(_paths):
        yield None

    monkeypatch.setattr(engineering.AppPaths, "discover", lambda **_kwargs: paths)
    monkeypatch.setattr(engineering, "startup_home_operation", fake_startup_operation)
    monkeypatch.setattr(engineering, "discover_source_repository_context", lambda: None)
    monkeypatch.setattr(
        engineering_actions,
        "_runtime",
        lambda: SimpleNamespace(registry=object(), store=object(), service=FakeService()),
    )
    output = []
    args = Namespace(
        home=None,
        test_command=None,
        workspace=None,
        operation_id="evaluation.practical-v1",
        params_json=None,
        fault_json=(
            '{"schema_version":1,"steps":[{"call_index":2,"effect":"timeout"},'
            '{"call_index":1,"effect":"provider_error"}]}'
        ),
        json_output=True,
        allow_external=False,
        allow_network=False,
    )
    assert engineering.run_engineering_cli(args, print_json=output.append) == 0
    expected = FaultPlanV1(
        steps=(FaultStep(1, FaultEffect.PROVIDER_ERROR), FaultStep(2, FaultEffect.TIMEOUT))
    ).to_dict()
    assert captured["request"].parameters["fault"] == expected
    assert captured["context"].fault_plan is None
    assert EngineeringPermission.INJECT_FAULT in captured["context"].permissions
    assert output == [{"status": "succeeded"}]
