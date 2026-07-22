import hashlib
from pathlib import Path

import pytest

from agent.code.changes import changeset_from_dict
from agent.code.commands import CodeCommandError, parse_code_command
from agent.code.context_selection import ContextSelector
from agent.code.diagnostics import FailureCategory, FailureClassifier
from agent.code.intelligence import CodeIntelligenceService
from agent.code.policy import ChangeApprovalPolicy, change_policy_from_config
from agent.code.task_templates import build_code_task_template
from agent.planning.task_graph import ResourceMode
from agent.runtime.context import TaskResult, TaskStatus


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_context_selection_uses_targets_symbols_and_imports(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "models.py").write_text(
        "class User:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.models import User\n\ndef load() -> User:\n    return User()\n",
        encoding="utf-8",
    )
    selector = ContextSelector(tmp_path, CodeIntelligenceService(tmp_path))

    selected = selector.select("Ajuste User", ["pkg/api.py"])

    assert [item.path for item in selected.files] == ["pkg/api.py", "pkg/models.py"]
    assert selected.files[0].content_hash in selected.text
    assert "target explícito" in selected.text
    assert "importado por pkg/api.py" in selected.text


def test_context_selection_expands_explicit_directory(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("B = 2\n", encoding="utf-8")
    selector = ContextSelector(tmp_path, CodeIntelligenceService(tmp_path))

    selected = selector.select("Revise o pacote", ["pkg"])

    assert {item.path for item in selected.files} == {"pkg/a.py", "pkg/b.py"}

    root_selected = selector.select("Revise", ["."])
    assert {item.path for item in root_selected.files} == {"pkg/a.py", "pkg/b.py"}


def test_change_policy_distinguishes_safe_edit_from_full_rewrite(tmp_path: Path):
    original = "value = 0\n"
    (tmp_path / "module.py").write_text(original, encoding="utf-8")
    risky = changeset_from_dict(
        {"changes": [{"path": "module.py", "kind": "modify", "content": "value = 1\n"}]}
    )
    safe = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "module.py",
                    "kind": "edit",
                    "base_hash": _hash(original),
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "expected_text": original,
                            "content": "value = 1\n",
                        }
                    ],
                }
            ]
        }
    )
    policy = ChangeApprovalPolicy()

    risky_result = policy.assess(tmp_path, risky, ["module.py"])
    safe_result = policy.assess(tmp_path, safe, ["module.py"])

    assert risky_result.requires_confirmation is True
    assert safe_result.requires_confirmation is False
    assert safe_result.confidence == 1.0

    root_target = policy.assess(tmp_path, risky, ["."])
    assert not any("targets" in reason for reason in root_target.reasons)


def test_failure_classifier_prevents_retry_after_permission_error():
    result = TaskResult(TaskStatus.BLOCKED, error="Capacidades ausentes para modify: write")

    classification = FailureClassifier().classify(result)

    assert classification.category == FailureCategory.PERMISSION
    assert classification.retryable is False

    unavailable = FailureClassifier().classify(
        TaskResult(TaskStatus.FAILED, error="Esta operação exige um ModelGateway configurado.")
    )
    assert unavailable.category == FailureCategory.TOOL_UNAVAILABLE
    assert unavailable.retryable is False


def test_change_policy_factory_fails_closed_for_unvalidated_config():
    policy = change_policy_from_config(
        {
            "code_policy": {
                "auto_apply_min_confidence": "alta",
                "max_auto_files": 0,
                "require_target_alignment": "não",
            }
        }
    )

    assert policy == ChangeApprovalPolicy()


def test_code_command_parser_builds_explicit_request_without_planner():
    parsed = parse_code_command(
        "/code repair src/api.py --tests --yes -- Corrija o parser sem mudar a API"
    )

    assert parsed.action == "repair"
    assert parsed.targets == ("src/api.py",)
    assert parsed.objective == "Corrija o parser sem mudar a API"
    assert parsed.include_tests is True
    assert parsed.assume_yes is True

    with pytest.raises(CodeCommandError, match="objetivo"):
        parse_code_command("/code modify src/api.py")
    with pytest.raises(CodeCommandError, match="target"):
        parse_code_command("/code repair -- Corrija o parser")


def test_task_template_has_deterministic_dependencies_and_resources():
    graph = build_code_task_template(
        "analyze_then_modify",
        ["a.py", "b.py", "a.py"],
        objective="Atualize os módulos",
        include_tests=True,
    )
    change_node = graph.by_id()["modify_after_analysis"]

    assert len(graph.nodes) == 3
    assert set(change_node.depends_on) == {
        node.node_id for node in graph.nodes if node.node_id != "modify_after_analysis"
    }
    assert {resource.name for resource in change_node.resources} == {
        "model",
        "a.py",
        "b.py",
    }
    assert all(resource.mode == ResourceMode.WRITE for resource in change_node.resources)
    assert change_node.metadata["include_tests"] is True

    with pytest.raises(ValueError, match="objective"):
        build_code_task_template("analyze_then_modify", ["a.py"])


def test_cli_code_command_bypasses_orchestrator(monkeypatch):
    from agent.code.application import CodingApplicationService
    from agent.interfaces.cli import commands as cli_commands

    captured = {}

    class Session:
        config = {"hardware_profile": "low_vram_8gb"}
        gateway = None

    class Orchestrator:
        def run(self, objective):
            raise AssertionError(f"planner não deveria receber: {objective}")

    def fake_execute(self, request, approver=None):
        del self, approver
        captured["request"] = request
        return TaskResult(TaskStatus.SUCCEEDED, summary="analisado")

    monkeypatch.setattr(CodingApplicationService, "execute", fake_execute)
    context = cli_commands.CommandContext(Session(), Orchestrator())

    handled, should_exit = cli_commands.handle_command(
        "/code analyze agent/code/workflows.py", context
    )

    assert (handled, should_exit) == (True, False)
    assert captured["request"].action == "analyze"
    assert captured["request"].targets == ("agent/code/workflows.py",)
