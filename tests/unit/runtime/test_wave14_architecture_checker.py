from __future__ import annotations

import shutil
from pathlib import Path

from scripts import check_wave14_architecture as checker
from scripts.check_wave14_architecture import REQUIRED_MUTATION_ARMS, check_architecture

ROOT = Path(__file__).parents[3]


def _copy(root: Path, destination: Path, relative: str) -> None:
    source = root / relative
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _rules(findings: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in findings}


def test_wave14_architecture_checker_covers_all_mutation_arms_and_passes() -> None:
    assert REQUIRED_MUTATION_ARMS == tuple(f"W14-M{index:02d}" for index in range(1, 23))
    assert check_architecture() == []


def test_wave14_c6_checker_covers_resume_graph_constraints_and_observability(tmp_path: Path) -> None:
    relative = "agent/orchestration/task_execution.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace("_restore_w14_runtime_intent", "_legacy_resume"), encoding="utf-8")
    assert "W14-C6-S1" in _rules(checker._check_c6_resume(tmp_path))

    relative = "agent/planning/intent_admission_logic.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace("INTENT_CONSTRAINT_UNSUPPORTED", "INTENT_CONSTRAINT_RETIRED"), encoding="utf-8")
    assert "W14-C6-S3" in _rules(checker._check_c6_constraints(tmp_path))

    relative = "agent/planning/graph_authority.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace("strict_w14", "compatibility_mode"), encoding="utf-8")
    assert "W14-C6-S2" in _rules(checker._check_c6_graph(tmp_path))

    relative = "agent/planning/target_grounding_revalidation.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace('normalize_resource_id(target.resource) == "memory"', 'normalize_resource_id(target.resource) == "legacy"'), encoding="utf-8")
    assert "W14-C6-S5" in _rules(checker._check_c6_memory(tmp_path))

    relative = "agent/runtime/task_execution_context.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace('"w14_semantic_task"', '"legacy_task"'), encoding="utf-8")
    assert "W14-C6-S6" in _rules(checker._check_c6_metadata(tmp_path))

    relative = "agent/runtime/event_kinds.py"
    _copy(ROOT, tmp_path, relative)
    source = (tmp_path / relative).read_text(encoding="utf-8")
    (tmp_path / relative).write_text(source.replace("MUTATION_SUBSET_CHECKED", "MUTATION_SUBSET_RETIRED"), encoding="utf-8")
    assert "W14-C6-S7" in _rules(checker._check_c6_observability(tmp_path))
