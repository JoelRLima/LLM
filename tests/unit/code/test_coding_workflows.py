import json
from pathlib import Path
from types import SimpleNamespace

from agent.approval import AutoApprove, RequireExplicitApproval
from agent.cancellation import CancellationToken
from agent.code.diagnostics import FailureCategory, FailureClassifier
from agent.code.validation import ValidationStatus
from agent.code.workflows import CodingWorkflowService
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.llm.model_profile import resolve_gateway_model_profile
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
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ModelResponse(content=response)

    def stream(self, request):
        raise NotImplementedError

    def count_tokens(self, text):
        return len(text) // 4


class ApproveAll:
    requires_explicit_approval = True

    def approve(self, preview, assessment):
        del preview, assessment
        return True


class RecordingApprover(ApproveAll):
    requires_explicit_approval = True

    def __init__(self):
        self.calls = 0

    def approve(self, preview, assessment):
        self.calls += 1
        return super().approve(preview, assessment)


class RecordingMetrics:
    def __init__(self):
        self.entries = []

    def record(self, metric):
        self.entries.append(metric)


class UnavailableValidator:
    def validate(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status=ValidationStatus.UNAVAILABLE, diagnostics=())


def _service(
    tmp_path: Path,
    gateway: FakeGateway,
    attempts: int = 2,
    metrics_sink=None,
):
    context = TaskExecutionContext(
        model_gateway=gateway,
        model_profile=resolve_gateway_model_profile({}, gateway),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_output_tokens=512, max_repair_attempts=attempts),
        metrics_sink=metrics_sink or RecordingMetrics(),
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


def test_review_rejects_external_target_before_reading_it(
    tmp_path: Path,
    monkeypatch,
):
    outside = tmp_path.parent / f"{tmp_path.name}-review-sentinel.py"
    outside.write_text("SENTINEL = True\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.resolve() == outside.resolve():
            raise AssertionError("review tentou ler a sentinela externa")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    result = _service(tmp_path, FakeGateway()).review([str(outside)])

    assert result.status == TaskStatus.FAILED
    assert "fora do workspace" in (result.error or "")


def test_generate_uses_changeset_and_real_syntax_validation(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "math_utils.py", "kind": "create", "content": "def add(a, b):\n    return a + b\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie add")

    assert result.status == TaskStatus.SUCCEEDED
    assert (tmp_path / "math_utils.py").exists()
    assert result.artifacts[0].metadata["validation"] == "passed"
    assert result.artifacts[0].metadata["validation_invocation_id"]
    assert result.artifacts[0].metadata["rollback_occurred"] is False
    assert result.artifacts[0].metadata["final_state"] == "applied"
    assert result.artifacts[0].metadata["mutation_occurred"] is True
    assert len(gateway.calls) == 1


def test_one_line_edit_uses_inclusive_eof_range_and_applies(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    gateway = FakeGateway(
        [
            _changes(
                {
                    "path": "controle.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "modificado",
                            "expected_text": "texto inventado pelo modelo",
                        }
                    ],
                }
            )
        ]
    )

    result = _service(tmp_path, gateway).change(
        "Altere controle.txt para modificado", ["controle.txt"], approver=ApproveAll()
    )

    assert result.status == TaskStatus.UNVERIFIED
    assert target.read_text(encoding="utf-8") == "modificado"
    assert result.artifacts[0].metadata["mutation_occurred"] is True
    proposal_prompt = gateway.calls[0].messages[1].content
    assert "--- controle.txt ---" in proposal_prompt
    assert "original" in proposal_prompt
    assert "inclusivas e 1-based" in proposal_prompt
    assert "end_line nunca pode exceder" in proposal_prompt
    assert "start_line=1 e end_line=1" in proposal_prompt
    assert "EOF+1 para replace/delete" in proposal_prompt
    assert "runtime vincula expected_text e base_hash" in proposal_prompt


def test_noop_changeset_is_applied_without_claiming_a_mutation(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("modificado", encoding="utf-8")
    gateway = FakeGateway(
        [_changes({"path": "controle.txt", "kind": "modify", "content": "modificado"})]
    )

    result = _service(tmp_path, gateway).change(
        "Mantenha controle.txt como modificado",
        ["controle.txt"],
        approver=ApproveAll(),
    )

    assert result.status == TaskStatus.UNVERIFIED
    assert target.read_text(encoding="utf-8") == "modificado"
    assert result.artifacts[0].content == ""
    assert result.artifacts[0].metadata["applied"] is True
    assert result.artifacts[0].metadata["mutation_occurred"] is False


def test_invalid_model_range_is_not_clamped_or_sent_to_approval(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    invalid = _changes(
        {
            "path": "controle.txt",
            "kind": "edit",
            "edits": [
                {
                    "operation": "replace",
                    "start_line": 1,
                    "end_line": 3,
                    "content": "modificado",
                }
            ],
        }
    )
    gateway = FakeGateway([invalid, invalid])
    approver = RecordingApprover()
    metrics = RecordingMetrics()

    result = _service(tmp_path, gateway, metrics_sink=metrics).change(
        "Altere controle.txt para modificado",
        ["controle.txt"],
        approver=approver,
    )

    assert result.status == TaskStatus.FAILED
    assert "fora do arquivo" in (result.error or "")
    assert len(gateway.calls) == 2
    assert "Faixa fora do arquivo: 1..3" in gateway.calls[1].messages[1].content
    assert "1 linhas disponiveis; limites 1..1" in gateway.calls[1].messages[1].content
    assert "Traceback" not in gateway.calls[1].messages[1].content
    assert invalid not in gateway.calls[1].messages[1].content
    assert [item["call_number"] for item in metrics.entries] == [1, 2]
    assert approver.calls == 0
    assert target.read_text(encoding="utf-8") == "original"


def test_invalid_model_range_gets_one_bounded_retry_then_applies_valid_range(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    invalid = _changes({
        "path": "controle.txt",
        "kind": "edit",
        "edits": [{
            "operation": "replace", "start_line": 1, "end_line": 3,
            "content": "modificado",
        }],
    })
    valid = _changes({
        "path": "controle.txt",
        "kind": "edit",
        "edits": [{
            "operation": "replace", "start_line": 1, "end_line": 1,
            "content": "modificado", "expected_text": "modelo incorreto",
        }],
    })
    gateway = FakeGateway([invalid, valid])
    approver = RecordingApprover()
    metrics = RecordingMetrics()

    result = _service(tmp_path, gateway, metrics_sink=metrics).change(
        "Altere controle.txt para modificado",
        ["controle.txt"],
        approver=approver,
    )

    assert result.status == TaskStatus.UNVERIFIED
    assert len(gateway.calls) == 2
    assert [item["call_number"] for item in metrics.entries] == [1, 2]
    assert approver.calls == 1
    assert target.read_text(encoding="utf-8") == "modificado"


def test_modify_repairs_one_malformed_structured_proposal(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    gateway = FakeGateway(
        [
            "{}",
            _changes(
                {
                    "path": "controle.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "modificado",
                            "expected_text": "original",
                        }
                    ],
                }
            ),
        ]
    )

    result = _service(tmp_path, gateway).change(
        "Altere controle.txt para modificado", ["controle.txt"], approver=ApproveAll()
    )

    assert result.status == TaskStatus.UNVERIFIED
    assert target.read_text(encoding="utf-8") == "modificado"
    assert len(gateway.calls) == 2
    assert "Não retorne {}" in gateway.calls[1].messages[1].content


def test_modify_repairs_one_canonically_empty_proposal(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    gateway = FakeGateway(
        [
            '{"changes":[]}',
            _changes(
                {
                    "path": "controle.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "modificado",
                            "expected_text": "original",
                        }
                    ],
                }
            ),
        ]
    )

    result = _service(tmp_path, gateway).change(
        "Altere controle.txt para modificado", ["controle.txt"], approver=ApproveAll()
    )

    assert result.status == TaskStatus.UNVERIFIED
    assert target.read_text(encoding="utf-8") == "modificado"
    assert len(gateway.calls) == 2


def test_proposal_recovery_stops_after_second_invalid_response(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    gateway = FakeGateway(["{}", '{"changes":[]}'])

    result = _service(tmp_path, gateway).change(
        "Altere controle.txt para modificado", ["controle.txt"], approver=ApproveAll()
    )

    assert result.status == TaskStatus.FAILED
    assert "lista não vazia" in (result.error or "")
    assert len(gateway.calls) == 2
    assert target.read_text(encoding="utf-8") == "original"


def test_second_proposal_provider_failure_is_measured_once(tmp_path: Path):
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    gateway = FakeGateway(["{}", RuntimeError("provider failed")])
    metrics = RecordingMetrics()

    result = _service(tmp_path, gateway, metrics_sink=metrics).change(
        "Altere controle.txt para modificado", ["controle.txt"], approver=ApproveAll()
    )

    assert result.status == TaskStatus.FAILED
    assert result.error == "provider failed"
    assert len(gateway.calls) == 2
    assert [(item["call_number"], item["success"]) for item in metrics.entries] == [
        (1, True),
        (2, False),
    ]
    assert target.read_text(encoding="utf-8") == "original"


def test_code_task_blocks_high_confidence_write_without_explicit_authority(
    tmp_path: Path,
    monkeypatch,
):
    gateway = FakeGateway(
        [
            _changes(
                {
                    "path": "safe_create.py",
                    "kind": "create",
                    "content": "VALUE = 1\n",
                }
            )
        ]
    )
    skill = CodeTaskSkill(
        str(tmp_path),
        model_gateway=gateway,
        approval_policy=RequireExplicitApproval(),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("headless não pode acessar stdin")
        ),
    )

    result = skill.execute(
        {
            "action": "generate",
            "objective": "Crie safe_create.py",
        }
    )

    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"
    assert result["data"]["artifacts"][0]["metadata"]["requires_confirmation"] is False
    assert result["data"]["artifacts"][0]["metadata"]["applied"] is False
    assert not (tmp_path / "safe_create.py").exists()


def test_code_task_auto_approval_applies_high_confidence_write(tmp_path: Path):
    gateway = FakeGateway(
        [
            _changes(
                {
                    "path": "approved_create.py",
                    "kind": "create",
                    "content": "VALUE = 2\n",
                }
            )
        ]
    )
    skill = CodeTaskSkill(
        str(tmp_path),
        model_gateway=gateway,
        approval_policy=AutoApprove(),
    )

    result = skill.execute(
        {
            "action": "generate",
            "objective": "Crie approved_create.py",
        }
    )

    assert result["status"] == "succeeded"
    assert result["ok"] is True
    assert (tmp_path / "approved_create.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_proposal_only_code_task_never_applies_even_with_auto_approval(tmp_path: Path):
    original = "value = 1\n"
    (tmp_path / "module.py").write_text(original, encoding="utf-8")
    gateway = FakeGateway(
        [_changes({"path": "module.py", "kind": "modify", "content": "value = 2\n"})]
    )

    result = CodeTaskSkill(
        str(tmp_path),
        model_gateway=gateway,
        approval_policy=AutoApprove(),
    ).execute(
        {
            "action": "modify",
            "objective": "Proponha uma modificacao sem aplicar.",
            "targets": ["module.py"],
        }
    )

    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"
    assert result["data"]["artifacts"][0]["metadata"]["applied"] is False
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == original


def test_failed_validation_rolls_back_generated_file(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "broken.py", "kind": "create", "content": "def broken(:\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie broken")

    assert result.status == TaskStatus.FAILED
    assert result.error == "validation:failed"
    assert result.artifacts[0].metadata["validation"] == "failed"
    assert result.artifacts[0].metadata["validation_invocation_id"]
    assert result.artifacts[0].metadata["rollback_occurred"] is True
    assert result.artifacts[0].metadata["final_state"] == "restored"
    assert result.artifacts[0].metadata["mutation_occurred"] is True
    assert not (tmp_path / "broken.py").exists()


def test_unavailable_validation_rolls_back_autonomous_non_code_mutation(tmp_path: Path):
    gateway = FakeGateway(
        [_changes({"path": "notes.txt", "kind": "create", "content": "documentação\n"})]
    )

    result = _service(tmp_path, gateway).change("Crie documentação")

    assert result.status == TaskStatus.FAILED
    assert not (tmp_path / "notes.txt").exists()
    assert result.artifacts[0].metadata["approval_mode"] == "autonomous"
    assert result.artifacts[0].metadata["rollback_occurred"] is True
    assert result.artifacts[0].metadata["final_state"] == "restored"


def test_validation_unavailable_is_canonical_and_never_retries_repair(
    tmp_path: Path,
) -> None:
    target = tmp_path / "notes.txt"
    gateway = FakeGateway(
        [_changes({"path": target.name, "kind": "create", "content": "documentacao\n"})]
    )
    service = _service(tmp_path, gateway, attempts=2)
    service.validator = UnavailableValidator()

    result = service.change("Crie notes.txt", repair=True)

    assert result.status == TaskStatus.FAILED
    assert result.failure_code == "TOOL_UNAVAILABLE"
    classification = FailureClassifier().classify(result)
    assert classification.category is FailureCategory.TOOL_UNAVAILABLE
    assert classification.retryable is False
    assert len(gateway.calls) == 1
    assert not target.exists()


def test_unavailable_autonomous_mutation_is_extension_independent(tmp_path: Path):
    for extension in ("py", "json", "md"):
        target = tmp_path / f"autonomous.{extension}"
        gateway = FakeGateway(
            [_changes({"path": target.name, "kind": "create", "content": "new\n"})]
        )
        service = _service(tmp_path, gateway)
        service.validator = UnavailableValidator()

        result = service.change(f"Crie {target.name}")

        assert result.status == TaskStatus.FAILED
        assert not target.exists()
        metadata = result.artifacts[0].metadata
        assert metadata["approval_mode"] == "autonomous"
        assert metadata["rollback_occurred"] is True
        assert metadata["final_state"] == "restored"


def test_unavailable_explicit_approval_is_extension_independent(tmp_path: Path):
    for extension in ("py", "json", "md"):
        target = tmp_path / f"approved.{extension}"
        target.write_text("old\n", encoding="utf-8")
        gateway = FakeGateway(
            [_changes({"path": target.name, "kind": "modify", "content": "new\n"})]
        )
        service = _service(tmp_path, gateway)
        service.validator = UnavailableValidator()

        result = service.change(
            f"Altere {target.name}", [target.name], approver=ApproveAll()
        )

        assert result.status == TaskStatus.UNVERIFIED
        assert target.read_text(encoding="utf-8") == "new\n"
        metadata = result.artifacts[0].metadata
        assert metadata["approval_mode"] == "explicit_approved"
        assert metadata["approval"]["explicit"] is True
        assert metadata["validation"] == "unavailable"


def test_unavailable_explicit_approval_can_be_disabled_by_product_policy(tmp_path: Path):
    target = tmp_path / "approved.json"
    target.write_text("old\n", encoding="utf-8")
    gateway = FakeGateway(
        [_changes({"path": target.name, "kind": "modify", "content": "new\n"})]
    )
    service = _service(tmp_path, gateway)
    service.validation_config = {"allow_unverified_approved": False}
    service.validator = UnavailableValidator()

    result = service.change(
        f"Altere {target.name}", [target.name], approver=ApproveAll()
    )

    assert result.status == TaskStatus.FAILED
    assert target.read_text(encoding="utf-8") == "old\n"
    assert result.artifacts[0].metadata["approval_mode"] == "explicit_approved"
    assert result.artifacts[0].metadata["final_state"] == "restored"


def test_unavailable_rollback_failure_preserves_surviving_mutation_truth(
    tmp_path: Path, monkeypatch
):
    target = tmp_path / "surviving.md"
    gateway = FakeGateway(
        [_changes({"path": target.name, "kind": "create", "content": "new\n"})]
    )
    service = _service(tmp_path, gateway)
    service.validator = UnavailableValidator()
    monkeypatch.setattr(
        "agent.code.workflow_application.ChangeSetTransaction.rollback",
        lambda _transaction: False,
    )

    result = service.change(f"Crie {target.name}")

    assert result.status == TaskStatus.FAILED
    assert target.read_text(encoding="utf-8") == "new\n"
    metadata = result.artifacts[0].metadata
    assert metadata["final_state"] == "unknown"
    assert metadata["surviving_mutation"] is True
    assert result.error == "rollback:incomplete"


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
