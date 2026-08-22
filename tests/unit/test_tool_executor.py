from types import SimpleNamespace

from agent.planning.step_contracts import PreparedInvocation
from agent.tool_executor import ToolExecutor
from agent.tools.contracts import ToolResult, ToolStatus


class _Gateway:
    def invoke(self, request, **kwargs):
        del kwargs
        return ToolResult(
            invocation_id=request.invocation_id,
            status=ToolStatus.SUCCEEDED,
            message="Arquivo lido completamente.",
            executed=True,
        )


def test_tool_output_identifies_primary_resource_without_control_character_injection(
    capsys,
) -> None:
    orchestrator = SimpleNamespace(
        tool_invocation_gateway=_Gateway(),
        skills={"file_reader": object()},
        active_skills=[],
        allowed_capabilities=None,
        cancellation_token=None,
        verbose=False,
    )

    result = ToolExecutor(orchestrator).run_tool(
        "file_reader", {"file_path": "cli.py\nforged"}
    )

    output = capsys.readouterr().out
    assert result["ok"] is True
    assert result["executed"] is True
    assert 'Usando file_reader... Recurso: "cli.py\\nforged".' in output
    assert "\nforged" not in output


def test_stale_prepared_invocation_is_blocked_before_gateway_dispatch() -> None:
    calls = []

    class _Gateway:
        def invoke(self, request, **kwargs):
            calls.append(request)
            return ToolResult(
                invocation_id=request.invocation_id,
                status=ToolStatus.SUCCEEDED,
                executed=True,
            )

    state = SimpleNamespace(
        plan_identity="new-plan",
        plan=[{"_step_id": "step-1", "tool": "echo", "args": {}}],
        objective="",
        tool_history=[],
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_invocation_gateway=_Gateway(),
        skills={"echo": object()},
        active_skills=[],
        allowed_capabilities=None,
        cancellation_token=None,
        verbose=False,
    )

    result = ToolExecutor(orchestrator).run_prepared_invocation(
        PreparedInvocation(
            index=0,
            step_id="step-1",
            tool="echo",
            args={},
            file_path="",
            plan_id="old-plan",
        )
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "prepared_invocation_stale"
    assert calls == []
