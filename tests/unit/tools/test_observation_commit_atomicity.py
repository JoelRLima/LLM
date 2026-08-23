from __future__ import annotations

from pathlib import Path

import pytest

from agent.approval import AutoApprove
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

    def failing_recorder(_name: str, _args: dict[str, object], _result: ToolResult) -> None:
        raise RuntimeError("semantic commit rejected")

    gateway = ToolInvocationGateway(
        _registry(_EchoAdapter()),
        event_emitter=lambda kind, data: events.append((kind, data)),
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

    class Writer:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("writer_commit", "writer", capabilities=frozenset({"write"})),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            Path(str(invocation.args["path"])).write_text("persisted", encoding="utf-8")
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)

    gateway = ToolInvocationGateway(
        _registry(Writer()),
        approval_port=AutoApprove(),
        state_recorder=lambda _name, _args, _result: (_ for _ in ()).throw(RuntimeError("commit")),
    )

    result = gateway.run("writer_commit", {"path": str(target)})

    assert target.read_text(encoding="utf-8") == "persisted"
    assert result.status is ToolStatus.UNVERIFIED
    assert result.executed is True
    assert result.error is not None
    assert result.error.detail is not None
    assert result.error.detail["physical_effect_unknown"] is True


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
