from types import SimpleNamespace

from agent.orchestration.security_service import SecurityAnalysisService
from agent.tools.contracts import ToolResult, ToolStatus


class _FakeGateway:
    def __init__(self):
        self.calls = []

    def run(
        self,
        tool_name: str,
        args: dict,
        *,
        active_skills=None,
        allowed_capabilities=None,
        **kwargs,
    ) -> ToolResult:
        self.calls.append(
            {
                "tool_name": tool_name,
                "args": args,
                "active_skills": active_skills,
                "allowed_capabilities": allowed_capabilities,
            }
        )
        return ToolResult(
            invocation_id="test",
            status=ToolStatus.SUCCEEDED,
            data={"issues": []},
            message="ok",
        )


class _FakeContextManager:
    def get_file_hints(self, objective: str) -> str:
        return "- app.py"


def test_security_analysis_service_passes_authorization_context_to_gateway() -> None:
    gateway = _FakeGateway()
    orchestrator = SimpleNamespace(
        tool_invocation_gateway=gateway,
        active_skills=["code_analyzer"],
        allowed_capabilities=frozenset({"read", "analyze"}),
        context_manager=_FakeContextManager(),
        session=SimpleNamespace(messages=[{"content": "system"}], config={}),
        final_responder=SimpleNamespace(build_final_answer=lambda prompt, on_chunk=None: "ok"),
        execution_gateway=SimpleNamespace(
            execute_validated_plan=lambda plan, objective, usage: SimpleNamespace(
                aborted=False,
                final_answer="ok",
                validated_plan=plan,
            )
        ),
    )

    service = SecurityAnalysisService(orchestrator)
    result = service.run("analisar segurança de app.py")

    assert result == "ok"
    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["tool_name"] == "code_analyzer"
    assert call["args"] == {"target": "app.py", "mode": "security"}
    assert call["active_skills"] == ["code_analyzer"]
    assert call["allowed_capabilities"] == frozenset({"read", "analyze"})
