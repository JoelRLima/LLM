from __future__ import annotations

from pathlib import Path

import pytest

from agent.approval import AutoApprove
from agent.execution_incidents import MAX_EXECUTION_INCIDENTS, MAX_INCIDENT_FILES
from agent.reporting.operational_outcome import project_operational_outcome
from agent.reporting.run_receipt import build_run_receipt
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.events import RuntimeEvent
from agent.state import AgentState
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolInvocationRequest, ToolResult, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def _registry(adapter: object) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    return registry


class _EchoAdapter:
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (ToolDescriptor("echo_commit", "echo"),)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, data="ok")


def _event_projection(events: list[tuple[str, dict[str, object]]]) -> tuple[RuntimeEventDispatcher, RunCorrelation]:
    correlation = RunCorrelation.fresh()

    def collect(event: RuntimeEvent) -> None:
        payload = event.to_legacy_dict()["data"]
        assert isinstance(payload, dict)
        events.append((event.kind.value, payload))

    return RuntimeEventDispatcher([collect]), correlation


def test_semantic_rejection_leaves_all_canonical_projections_unchanged() -> None:
    state = AgentState()
    state.task_semantics.register_observation(
        "seed",
        {"invocation_id": "seed", "ok": True, "status": "succeeded", "data": "old"},
        evidence_ref=1,
        args={},
    )
    before_semantics = state.task_semantics.to_checkpoint_dict()

    with pytest.raises(ValueError, match="referencia de evidencia reutilizada"):
        state.record_tool_result(
            "echo_commit",
            {},
            {
                "invocation_id": "new",
                "ok": True,
                "done": True,
                "status": "succeeded",
                "data": "new",
            },
        )

    assert state.last_tool is None
    assert state.last_args is None
    assert state.last_result is None
    assert state.tool_history == []
    assert state.task_semantics.to_checkpoint_dict() == before_semantics


def test_gateway_publishes_unverified_when_canonical_commit_fails() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    dispatcher, correlation = _event_projection(events)

    def failing_recorder(_name: str, _args: dict[str, object], _result: ToolResult) -> None:
        raise RuntimeError("semantic commit rejected")

    gateway = ToolInvocationGateway(
        _registry(_EchoAdapter()),
        event_dispatcher=dispatcher,
        correlation_provider=lambda: correlation,
        state_recorder=failing_recorder,
    )
    request = ToolInvocationRequest("commit-failure", "echo_commit")

    result = gateway.run(request)

    assert result.status is ToolStatus.UNVERIFIED
    assert result.error is not None
    assert result.error.code == "CANONICAL_COMMIT_FAILED"
    assert result.executed is True
    terminal = [data for kind, data in events if kind == "tool_end"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == ToolStatus.UNVERIFIED.value
    assert not any(data["status"] == ToolStatus.SUCCEEDED.value for data in terminal)

    retry = gateway.run(request)
    assert retry.status is ToolStatus.UNVERIFIED
    assert retry.error is not None
    assert retry.error.code == "CANONICAL_COMMIT_RETRY_BLOCKED"


def test_telemetry_recorder_failure_does_not_change_success_truth() -> None:
    gateway = ToolInvocationGateway(
        _registry(_EchoAdapter()),
        state_recorder=lambda _name, _args, _result: (_ for _ in ()).throw(RuntimeError("telemetry")),
        state_recorder_is_canonical=False,
    )

    result = gateway.run("echo_commit", {})

    assert result.status is ToolStatus.SUCCEEDED
    assert result.executed is True


def test_physical_mutation_plus_commit_failure_preserves_effect_uncertainty(tmp_path: Path) -> None:
    target = tmp_path / "written.txt"
    state = AgentState()

    class Writer:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("writer_commit", "writer", capabilities=frozenset({"write"})),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            Path(str(invocation.args["path"])).write_text("persisted", encoding="utf-8")
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.SUCCEEDED,
                artifacts=(
                    {
                        "metadata": {
                            "applied": True,
                            "mutation_occurred": True,
                            "persisted_mutation": True,
                            "final_state": "applied",
                            "affected_files": [str(target)],
                        }
                    },
                ),
                executed=True,
            )

    gateway = ToolInvocationGateway(
        _registry(Writer()),
        approval_port=AutoApprove(),
        state_recorder=lambda _name, _args, _result: (_ for _ in ()).throw(RuntimeError("commit")),
        incident_recorder=state.record_execution_incident,
    )

    result = gateway.run("writer_commit", {"path": str(target)})

    assert target.read_text(encoding="utf-8") == "persisted"
    assert result.status is ToolStatus.UNVERIFIED
    assert result.executed is True
    assert result.error is not None
    assert result.error.detail is not None
    assert result.error.detail["physical_effect_unknown"] is False
    assert state.tool_history == []
    assert len(state.execution_incidents) == 1
    incident = state.execution_incidents[0]
    assert incident["effect_state"] == "PROVEN"
    assert incident["affected_files"] == [str(target).replace("\\", "/")]
    outcome = project_operational_outcome(state)
    receipt = build_run_receipt(tmp_path, state, "succeeded", None)
    assert outcome.terminal_status == "unverified"
    assert outcome.mutation_occurred is True
    assert str(target).replace("\\", "/") in outcome.files_affected
    assert receipt["status"] == "unverified"
    assert receipt["mutation_occurred"] is True
    assert str(target).replace("\\", "/") in receipt["files_affected"]
    assert receipt["execution_incidents"][0]["effect_state"] == "PROVEN"


def test_unknown_effect_after_canonical_commit_failure_remains_publicly_uncertain() -> None:
    state = AgentState()

    class Writer:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("unknown_writer", "writer", capabilities=frozenset({"write"})),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)

    gateway = ToolInvocationGateway(
        _registry(Writer()),
        approval_port=AutoApprove(),
        state_recorder=lambda _name, _args, _result: (_ for _ in ()).throw(RuntimeError("commit")),
        incident_recorder=state.record_execution_incident,
    )

    result = gateway.run("unknown_writer", {})

    assert result.status is ToolStatus.UNVERIFIED
    assert state.tool_history == []
    assert state.execution_incidents[0]["effect_state"] == "UNKNOWN"
    outcome = project_operational_outcome(state)
    receipt = build_run_receipt(Path("."), state, "succeeded", None)
    assert outcome.terminal_status == "unverified"
    assert outcome.mutation_occurred is False
    assert outcome.physical_effect_unknown is True
    assert receipt["status"] == "unverified"
    assert receipt["operational_outcome"]["physical_effect_unknown"] is True
    assert receipt["execution_incidents"][0]["effect_state"] == "UNKNOWN"


def test_oversized_incident_footprint_truncates_detail_without_dropping_proven_effect() -> None:
    state = AgentState()
    files = [f"file-{index}.txt" for index in range(MAX_INCIDENT_FILES + 1)]

    state.record_execution_incident(
        {
            "incident_type": "CANONICAL_COMMIT_FAILED",
            "invocation_id": "oversized-footprint",
            "tool": "writer",
            "original_tool_status": "succeeded",
            "executed": True,
            "effect_state": "PROVEN",
            "affected_files": files,
            "rollback_occurred": False,
            "error_code": "CANONICAL_COMMIT_FAILED",
        }
    )

    incident = state.execution_incidents[0]
    assert incident["effect_state"] == "PROVEN"
    assert len(incident["affected_files"]) == MAX_INCIDENT_FILES
    assert incident["detail_truncated"] is True
    outcome = project_operational_outcome(state)
    assert outcome.mutation_occurred is True


def test_incident_normalization_failure_falls_back_to_bounded_effect_truth() -> None:
    state = AgentState()

    state.record_execution_incident(
        {
            "incident_type": "CANONICAL_COMMIT_FAILED",
            "invocation_id": "normalization-failure",
            "tool": "writer",
            "original_tool_status": "succeeded",
            "executed": True,
            "effect_state": "PROVEN",
            "affected_files": object(),
            "rollback_occurred": False,
            "error_code": "CANONICAL_COMMIT_FAILED",
        }
    )

    incident = state.execution_incidents[0]
    assert incident["normalization_failed"] is True
    assert incident["effect_state"] == "PROVEN"
    assert project_operational_outcome(state).mutation_occurred is True


def test_execution_incidents_restore_as_uncertain_reporting_only() -> None:
    state = AgentState()
    state.record_execution_incident(
        {
            "incident_type": "CANONICAL_COMMIT_FAILED",
            "invocation_id": "incident-1",
            "tool": "writer",
            "original_tool_status": "succeeded",
            "executed": True,
            "effect_state": "UNKNOWN",
            "affected_files": [],
            "rollback_occurred": False,
            "error_code": "CANONICAL_COMMIT_FAILED",
        }
    )

    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert len(restored.execution_incidents) == 1
    restored_incident = restored.execution_incidents[0]
    assert restored_incident["incident_type"] == "CANONICAL_COMMIT_FAILED"
    assert restored_incident["invocation_id"] == "incident-1"
    assert restored_incident["tool"] == "writer"
    assert restored_incident["original_tool_status"] == "unverified"
    assert restored_incident["executed"] is None
    assert restored_incident["effect_state"] == "UNKNOWN"
    assert restored_incident["affected_files"] == []
    assert restored_incident["rollback_occurred"] is None
    assert restored.executed_effects == []
    assert restored.terminal_disposition is None

    forged = state.to_checkpoint_dict()
    forged["execution_incidents"][0]["raw_payload"] = {"secret": "discard"}
    with pytest.raises(ValueError, match="incident"):
        AgentState().from_checkpoint_dict(forged)


@pytest.mark.parametrize("forged_effect_state", ["NONE", "PROVEN"])
def test_forged_checkpoint_incident_cannot_manufacture_effect_authority(
    forged_effect_state: str,
) -> None:
    state = AgentState()
    state.record_execution_incident(
        {
            "incident_type": "CANONICAL_COMMIT_FAILED",
            "invocation_id": "incident-forged",
            "tool": "writer",
            "original_tool_status": "succeeded",
            "executed": True,
            "effect_state": "UNKNOWN",
            "affected_files": [],
            "rollback_occurred": False,
            "error_code": "CANONICAL_COMMIT_FAILED",
        }
    )
    forged = state.to_checkpoint_dict()
    forged_incident = forged["execution_incidents"][0]
    forged_incident["effect_state"] = forged_effect_state
    forged_incident["executed"] = True
    forged_incident["affected_files"] = ["forged.py"]
    forged_incident["rollback_occurred"] = True
    forged_incident["original_tool_status"] = "succeeded"

    restored = AgentState()
    restored.from_checkpoint_dict(forged)

    restored_incident = restored.execution_incidents[0]
    assert restored_incident["effect_state"] == "UNKNOWN"
    assert restored_incident["executed"] is None
    assert restored_incident["affected_files"] == []
    assert restored_incident["rollback_occurred"] is None
    assert restored_incident["original_tool_status"] == "unverified"
    outcome = project_operational_outcome(restored, terminal_status="succeeded")
    receipt = build_run_receipt(Path("."), restored, "succeeded", None)
    assert outcome.terminal_status == "unverified"
    assert outcome.physical_effect_unknown is True
    assert outcome.mutation_occurred is False
    assert outcome.files_affected == ()
    assert restored.executed_effects == []
    assert receipt["status"] == "unverified"
    assert receipt["executed"] is None
    assert receipt["mutation_occurred"] is False
    assert receipt["files_affected"] == []
    assert receipt["rollback"] == {"occurred": False, "outcome": None}
    assert receipt["operational_outcome"]["physical_effect_unknown"] is True


def test_execution_incident_journal_is_bounded_and_oversized_restore_fails() -> None:
    state = AgentState()
    for index in range(MAX_EXECUTION_INCIDENTS + 2):
        state.record_execution_incident(
            {
                "incident_type": "CANONICAL_COMMIT_FAILED",
                "invocation_id": f"incident-{index}",
                "tool": "writer",
                "original_tool_status": "succeeded",
                "executed": True,
                "effect_state": "UNKNOWN",
                "affected_files": [],
                "rollback_occurred": False,
                "error_code": "CANONICAL_COMMIT_FAILED",
            }
        )

    assert len(state.execution_incidents) == MAX_EXECUTION_INCIDENTS
    assert state.execution_incidents[0]["invocation_id"] == "incident-2"
    assert state.execution_incidents[0]["journal_overflow"] is True
    assert state.execution_incidents[0]["omitted_incidents"] == 2
    assert state.execution_incidents[0]["omitted_effect_states"] == ["UNKNOWN"]
    assert project_operational_outcome(state).physical_effect_unknown is True
    oversized = state.to_checkpoint_dict()
    oversized["execution_incidents"].append(dict(oversized["execution_incidents"][-1]))
    with pytest.raises(ValueError, match="incident"):
        AgentState().from_checkpoint_dict(oversized)


def test_incident_overflow_preserves_omitted_proven_and_unknown_states() -> None:
    state = AgentState()
    for index in range(MAX_EXECUTION_INCIDENTS + 2):
        state.record_execution_incident(
            {
                "incident_type": "CANONICAL_COMMIT_FAILED",
                "invocation_id": f"mixed-{index}",
                "tool": "writer",
                "original_tool_status": "succeeded",
                "executed": True,
                "effect_state": "PROVEN" if index == 0 else "UNKNOWN",
                "affected_files": ["proven.txt"] if index == 0 else [],
                "rollback_occurred": False,
                "error_code": "CANONICAL_COMMIT_FAILED",
            }
        )

    summary = state.execution_incidents[0]
    assert summary["omitted_effect_states"] == ["PROVEN", "UNKNOWN"]
    outcome = project_operational_outcome(state)
    assert outcome.mutation_occurred is True
    assert outcome.physical_effect_unknown is True


def test_agent_state_checkpoint_has_no_partial_observation_after_gateway_failure() -> None:
    state = AgentState()
    state.task_semantics.register_observation(
        "seed",
        {"invocation_id": "seed", "ok": True, "status": "succeeded", "data": "old"},
        evidence_ref=1,
        args={},
    )
    gateway = ToolInvocationGateway(
        _registry(_EchoAdapter()),
        state_recorder=lambda name, args, result: state.record_tool_result(
            name,
            args,
            result.to_legacy_dict(include_details=True),
        ),
    )

    result = gateway.run(ToolInvocationRequest("state-commit-failure", "echo_commit"))

    assert result.status is ToolStatus.UNVERIFIED
    assert state.tool_history == []
    checkpoint = state.to_checkpoint_dict()
    assert checkpoint["tool_history"] == []
