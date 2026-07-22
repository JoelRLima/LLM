import json
from pathlib import Path

from agent.cancellation import CancellationToken
from agent.code.workflows import CodingWorkflowService
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskStatus
from agent.skills.code_task import CodeTaskSkill


class FakeGateway:
    provider_name = "fake"
    capabilities = ProviderCapabilities()

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        return ModelResponse(content=self.responses.pop(0))

    def stream(self, request):
        raise NotImplementedError

    def count_tokens(self, text):
        return len(text) // 4


class ApproveAll:
    def approve(self, preview, assessment):
        del preview, assessment
        return True


def _service(tmp_path: Path, gateway: FakeGateway, attempts: int = 2):
    context = TaskExecutionContext(
        model_gateway=gateway,
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_output_tokens=512, max_repair_attempts=attempts),
    )
    return CodingWorkflowService(tmp_path, context)


def _changes(*changes):
    return json.dumps({"changes": list(changes)})


def test_analyze_and_review_do_not_call_model_or_mutate(tmp_path: Path):
    source = "def load(expression):\n    return eval(expression)\n"
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    gateway = FakeGateway()
    service = _service(tmp_path, gateway)

    analysis = service.analyze("service.py")
    review = service.review(["service.py"])

    assert analysis.status == TaskStatus.SUCCEEDED
    assert review.status == TaskStatus.SUCCEEDED
    assert review.diagnostics[0]["code"] == "PYSEC001"
    assert (tmp_path / "service.py").read_text(encoding="utf-8") == source
    assert gateway.calls == []


def test_generate_uses_changeset_and_real_syntax_validation(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "math_utils.py", "kind": "create", "content": "def add(a, b):\n    return a + b\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie add")

    assert result.status == TaskStatus.SUCCEEDED
    assert (tmp_path / "math_utils.py").exists()
    assert result.artifacts[0].metadata["validation"] == "passed"
    assert len(gateway.calls) == 1


def test_failed_validation_rolls_back_generated_file(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "broken.py", "kind": "create", "content": "def broken(:\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie broken")

    assert result.status == TaskStatus.FAILED
    assert result.error == "validation:failed"
    assert not (tmp_path / "broken.py").exists()


def test_unavailable_validation_is_explicit_and_keeps_non_code_artifact(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "notes.txt", "kind": "create", "content": "documentação\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie documentação")

    assert result.status == TaskStatus.UNVERIFIED
    assert (tmp_path / "notes.txt").exists()


def test_repair_retries_with_bounded_model_calls_and_rolls_back_failed_attempt(tmp_path: Path):
    (tmp_path / "module.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    gateway = FakeGateway(
        [
            _changes({"path": "module.py", "kind": "modify", "content": "def value(:\n"}),
            _changes({"path": "module.py", "kind": "modify", "content": "def value():\n    return 1\n"}),
        ]
    )

    result = _service(tmp_path, gateway, attempts=2).change(
        "Corrija value", ["module.py"], repair=True, approver=ApproveAll()
    )

    assert result.status == TaskStatus.SUCCEEDED
    assert "return 1" in (tmp_path / "module.py").read_text(encoding="utf-8")
    assert len(gateway.calls) == 2


def test_low_confidence_changeset_is_not_applied_without_approval(tmp_path: Path):
    original = "value = 0\n"
    (tmp_path / "module.py").write_text(original, encoding="utf-8")
    gateway = FakeGateway(
        [_changes({"path": "module.py", "kind": "modify", "content": "value = 1\n"})]
    )

    result = _service(tmp_path, gateway).change("Altere value", ["module.py"])

    assert result.status == TaskStatus.BLOCKED
    assert result.error == "confirmation_required"
    assert result.artifacts[0].metadata["applied"] is False
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == original


def test_code_task_can_analyze_without_configured_model(tmp_path: Path):
    (tmp_path / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    result = CodeTaskSkill(str(tmp_path)).execute(
        {"action": "analyze", "targets": ["service.py"]}
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "succeeded"


def test_multitask_code_node_must_declare_required_capabilities(tmp_path: Path):
    result = CodeTaskSkill(str(tmp_path)).execute(
        {
            "action": "multitask",
            "objective": "Criar arquivo",
            "graph": {
                "nodes": [
                    {
                        "id": "write",
                        "objective": "Criar module.py",
                        "metadata": {"action": "generate"},
                    }
                ]
            },
        }
    )

    assert result["ok"] is False
    assert result["data"]["metadata"]["states"]["write"] == "blocked"
    assert not (tmp_path / "module.py").exists()


def test_code_task_executes_deterministic_analysis_template(tmp_path: Path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("B = 2\n", encoding="utf-8")

    result = CodeTaskSkill(str(tmp_path)).execute(
        {
            "action": "template",
            "template": "parallel_analyze",
            "targets": ["a.py", "b.py"],
        }
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "succeeded"
    assert set(result["data"]["metadata"]["states"].values()) == {"succeeded"}
