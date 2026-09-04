from __future__ import annotations

from pathlib import Path

from scripts import check_wave11_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


def test_real_repository_passes_w11_checker() -> None:
    root = Path(__file__).parents[3]

    assert checker.check_architecture(root) == []


def test_checker_catches_execution_provider_resume_and_persisted_authority_fixtures(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent/planning/plan_preview.py",
        "from agent.tools.invocation_gateway import ToolInvocationGateway\n"
        "def preview(gateway):\n"
        "    gateway.execute_validated_plan([])\n"
        "    return gateway.validate_and_optimize_plan([])\n",
    )
    _write(
        tmp_path,
        "agent/orchestration/task_directive_runtime.py",
        "from agent.llm.providers.fake import ProviderFactory\n"
        "def apply(orchestrator):\n"
        "    orchestrator.set_operational_mode('full')\n",
    )
    _write(
        tmp_path,
        "agent/interfaces/cli/app.py",
        "from agent.checkpoint_manager import CheckpointManager\n"
        "def run():\n"
        "    CheckpointManager().save_checkpoint()\n",
    )
    _write(
        tmp_path,
        "agent/runtime/task_directives.py",
        "class TaskRunDirective:\n"
        "    def to_checkpoint_dict(self):\n"
        "        return {'schema_version': 1, 'directive': 'read',\n"
        "                'deliberation_profile': 'smart', 'subject': 'x',\n"
        "                'allowed_capabilities': ['write']}\n",
    )

    violations = checker.check_architecture(tmp_path)
    rules = _rules(violations)

    assert {"W11-S1", "W11-S2", "W11-S3", "W11-S4", "W11-S6"} <= rules


def test_checker_preserves_bare_read_registration_rule(tmp_path: Path) -> None:
    _write(tmp_path, "agent/interfaces/cli/command_handlers.py", "def read_file(text, ctx): pass\n")
    _write(tmp_path, "agent/interfaces/cli/commands.py", "PREFIX_HANDLERS = ()\n")

    assert "W11-S5" in _rules(checker.check_architecture(tmp_path))
