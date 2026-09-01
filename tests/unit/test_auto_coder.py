from __future__ import annotations

from agent.code.application import CodeRequest, CodingApplicationService, build_code_context
from agent.llm.contracts import ModelResponse


class _Gateway:
    provider_name = "test-provider"
    model = "test-model"

    def complete(self, request):
        return ModelResponse(content="unused")


def test_code_workflow_analyze_is_the_canonical_code_entrypoint(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    context = build_code_context({"model": "test-model"}, _Gateway())

    result = CodingApplicationService(tmp_path, context, {}).execute(
        CodeRequest(action="analyze", targets=("sample.py",))
    )

    assert result.status.value == "succeeded"
    assert result.artifacts
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_code_workflow_refuses_a_target_outside_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    context = build_code_context({"model": "test-model"}, _Gateway())

    result = CodingApplicationService(workspace, context, {}).execute(
        CodeRequest(action="analyze", targets=(str(outside),))
    )

    assert result.status.value != "succeeded"
