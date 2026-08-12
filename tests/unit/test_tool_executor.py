from types import SimpleNamespace

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
    assert 'Usando file_reader... Recurso: "cli.py\\nforged".' in output
    assert "\nforged" not in output
