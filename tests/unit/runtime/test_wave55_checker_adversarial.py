from __future__ import annotations

from pathlib import Path

from scripts import check_wave55_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {violation.rule_id for violation in violations}


def test_s1_rejects_capability_authority_collision_and_alias(tmp_path: Path) -> None:
    _write(tmp_path, "agent/other.py", "class TaskAuthoritySnapshot:\n    pass\n")
    assert "W55-S1" in _rules(checker._check_s1(tmp_path))

    _write(
        tmp_path,
        "agent/task_definition/alias.py",
        "from agent.tools.authority import TaskAuthoritySnapshot\n",
    )
    assert "W55-S1" in _rules(checker._check_s1(tmp_path))


def test_s2_rejects_a_repository_outside_the_application_data_seam(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/task_definition/repository.py",
        "from pathlib import Path\n"
        "from agent.runtime.paths import WorkspacePaths\n"
        "from agent.memory.json_persistence import write_json_atomic\n"
        "from agent.memory.path_safety import reject_link_like\n"
        "def path(paths: WorkspacePaths):\n"
        "    return paths.memory_db_file\n",
    )
    violations = checker._check_s2(tmp_path)
    assert "W55-S2" in _rules(violations)
    assert any("memory_db_file" in item.detail for item in violations)


def test_s3_rejects_full_authority_body_in_checkpoint_projection(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/state_checkpointing.py",
        "from agent.task_definition.models import TaskDefinitionRef\n"
        "def to_checkpoint_dict(self):\n"
        "    return {'task_definition_ref': TaskDefinitionRef, 'contract': self.contract}\n",
    )
    violations = checker._check_s3(tmp_path)
    assert "W55-S3" in _rules(violations)
    assert any("contract" in item.detail for item in violations)


def test_s5_rejects_compaction_that_copies_the_wrong_message(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/llm/context_views.py",
        "def build_compact_view(messages):\n"
        "    return [dict(messages[1])]\n",
    )
    assert "W55-S5" in _rules(checker._check_s5(tmp_path))

    _write(
        tmp_path,
        "agent/llm/context_views.py",
        "def build_compact_view(messages):\n"
        "    return [dict(messages[0])]\n",
    )
    assert checker._check_s5(tmp_path) == []


def test_s6_rejects_resume_execution_without_authority_resolution(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/orchestration/task_runner.py",
        "def run(self):\n"
        "    self._execute()\n"
        "def _ensure_task_definition(self):\n"
        "    return None\n"
        "def _execute(self):\n"
        "    return None\n"
        "task_definition_compiler = None\n",
    )
    assert "W55-S6" in _rules(checker._check_s6(tmp_path))


def test_s6_rejects_missing_compiler_none_bypass(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/orchestration/task_runner.py",
        "def run(self):\n"
        "    answer = self._ensure_task_definition()\n"
        "    if answer is not None:\n"
        "        return answer\n"
        "    return self._execute()\n"
        "def _ensure_task_definition(self):\n"
        "    return ensure_task_definition(self)\n"
        "def _execute(self):\n"
        "    return None\n",
    )
    _write(
        tmp_path,
        "agent/orchestration/task_definition_gate.py",
        "def ensure_task_definition(runner):\n"
        "    compiler = getattr(runner.orchestrator, 'task_definition_compiler', None)\n"
        "    if compiler is None:\n"
        "        return None\n"
        "    return compiler.compile()\n",
    )
    assert "W55-S6" in _rules(checker._check_s6(tmp_path))


def test_s6_rejects_discarded_gate_result_even_when_gate_is_before_execute(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent/orchestration/task_runner.py",
        "def run(self):\n"
        "    self._ensure_task_definition()\n"
        "    return self._execute()\n"
        "def _ensure_task_definition(self):\n"
        "    return ensure_task_definition(self)\n"
        "def _execute(self):\n"
        "    return None\n",
    )
    _write(
        tmp_path,
        "agent/orchestration/task_definition_gate.py",
        "def ensure_task_definition(runner, inputs):\n"
        "    compiler = getattr(runner.orchestrator, 'task_definition_compiler', None)\n"
        "    if compiler is None:\n"
        "        raise RuntimeError('blocked')\n"
        "    if inputs.resumed:\n"
        "        return compiler.resume()\n"
        "    return compiler.compile()\n",
    )
    assert "W55-S6" in _rules(checker._check_s6(tmp_path))


def test_s7_rejects_model_or_mutation_owner_in_external_context_command(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interfaces/cli/app.py",
        "def _run_task_context(args):\n"
        "    from agent.application import AgentApplication\n"
        "    return AgentApplication(args)\n",
    )
    violations = checker._check_s7(tmp_path)
    assert "W55-S7" in _rules(violations)
    assert any("AgentApplication" in item.detail for item in violations)


def test_s8_is_scoped_but_rejects_explicit_future_phase_policy(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/task_definition/policy.py",
        "def decide():\n"
        "    return advance_phase_from_llm()\n",
    )
    _write(
        tmp_path,
        "agent/unrelated.py",
        "def progress(phase):\n"
        "    return phase\n",
    )
    _write(
        tmp_path,
        "agent/task_definition/policy.py",
        "def decide():\n"
        "    return 'phase'\n",
    )
    assert checker._check_s8(tmp_path) == []
