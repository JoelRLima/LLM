from __future__ import annotations

from pathlib import Path

from scripts import check_wave6_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


POLICY_SOURCE = """
class TaskPolicyDecision: pass
class TaskPolicyResult: pass
class TaskPolicyState:
    def __init__(self):
        self._logical_work_units_consumed = 0
        self._active_elapsed_seconds = 0.0
class TaskRuntimePolicy:
    def check_current(self): pass
    def admit_work_units(self): pass
    def authorize_recovery(self): pass
"""


def test_s1_rejects_duplicate_policy_type_but_allows_a_reference(tmp_path: Path) -> None:
    _write(tmp_path, "agent/runtime/task_policy.py", POLICY_SOURCE)
    _write(tmp_path, "agent/consumer.py", "from agent.runtime.task_policy import TaskRuntimePolicy\n")
    assert checker._check_s1(tmp_path) == []

    _write(tmp_path, "agent/rogue.py", "class TaskRuntimePolicy: pass\n")
    assert "W6-S1" in _rules(checker._check_s1(tmp_path))


def test_s2_flags_only_duplicate_task_scoped_field_assignments(tmp_path: Path) -> None:
    _write(tmp_path, "agent/runtime/task_policy.py", POLICY_SOURCE)
    _write(
        tmp_path,
        "agent/rogue.py",
        "class Rogue:\n"
        "    def reset(self):\n"
        "        self._logical_work_units_consumed = 0\n"
        "        self.local_counter = 0\n",
    )
    violations = checker._check_s2(tmp_path)
    assert "W6-S2" in _rules(violations)
    assert any("_logical_work_units_consumed" in item.detail for item in violations)


def test_s3_requires_admission_before_dispatch_and_uses_prefix(tmp_path: Path) -> None:
    _write(tmp_path, "agent/runtime/task_policy.py", POLICY_SOURCE)
    _write(
        tmp_path,
        "agent/planning/plan_executor.py",
        "def _execute_parallel_read_batch(self, batch_indices):\n"
        "    admission = self.task_policy.admit_work_units(len(batch_indices))\n"
        "    dispatch_indices = batch_indices[:admission.admitted_units]\n"
        "    return self._run_parallel_tools(dispatch_indices)\n",
    )
    _write(
        tmp_path,
        "agent/planning/task_scheduler.py",
        "def _run_batch(self, batch):\n"
        "    admission = self.task_policy.admit_work_units(len(batch))\n"
        "    dispatch_batch = batch[:admission.admitted_units]\n"
        "    return pool.submit(self.executor.execute, dispatch_batch)\n",
    )
    assert checker._check_s3(tmp_path) == []

    _write(
        tmp_path,
        "agent/planning/plan_executor.py",
        "def _execute_parallel_read_batch(self, batch_indices):\n"
        "    self._run_parallel_tools(batch_indices)\n"
        "    admission = self.task_policy.admit_work_units(len(batch_indices))\n"
        "    return batch_indices[:admission.admitted_units]\n",
    )
    assert "W6-S3" in _rules(checker._check_s3(tmp_path))


def test_s6_allows_local_immutable_projection_but_rejects_source_mutation(tmp_path: Path) -> None:
    projection = (
        "from dataclasses import dataclass\n"
        "from types import MappingProxyType\n"
        "@dataclass(frozen=True)\n"
        "class TaskProgressProjection:\n"
        "    counts: object\n"
        "def build_task_progress_projection(state=None):\n"
        "    return TaskProgressProjection(MappingProxyType({}))\n"
    )
    _write(tmp_path, "agent/planning/task_progress_projection.py", projection)
    assert checker._check_s6(tmp_path) == []

    _write(
        tmp_path,
        "agent/planning/task_progress_projection.py",
        projection.replace(
            "return TaskProgressProjection(MappingProxyType({}))",
            "state.plan.append('rogue')\n    return TaskProgressProjection(MappingProxyType({}))",
        ),
    )
    assert "W6-S6" in _rules(checker._check_s6(tmp_path))


def test_s7_rejects_report_success_derived_from_percentage(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/reporting/task_tracker.py",
        "def _recompute_progress(self):\n"
        "    return build_task_progress_projection()\n",
    )
    _write(tmp_path, "agent/reporting/task_tracker_rendering.py", "terminal_coverage_percent\n")
    _write(
        tmp_path,
        "agent/reporting/run_projection_facts.py",
        "def facts():\n"
        "    return build_task_progress_projection()\n",
    )
    _write(
        tmp_path,
        "agent/reporting/task_report.py",
        "def build_report(status, progress):\n"
        "    return {'success': progress['successful_completion_percent'] == 100, 'status': status}\n",
    )
    violations = checker._check_s7(tmp_path)
    assert "W6-S7" in _rules(violations)
    assert any("percentage" in item.detail for item in violations)


def test_s8_rejects_policy_import_inside_task_definition_authority(tmp_path: Path) -> None:
    _write(tmp_path, "agent/task_definition/models.py", "class TaskDefinitionRef: pass\n")
    _write(tmp_path, "agent/orchestration/task_definition_gate.py", "def ensure_task_definition(): pass\n")
    _write(tmp_path, "agent/orchestration/task_runner.py", "def _ensure_task_definition(): pass\n")
    _write(tmp_path, "scripts/check_wave55_architecture.py", "\n")
    _write(tmp_path, "agent/task_definition/policy.py", "from agent.runtime.task_policy import TaskRuntimePolicy\n")
    assert "W6-S8" in _rules(checker._check_s8(tmp_path))


def test_s9_reports_missing_prior_gates_without_running_agent_runtime(tmp_path: Path) -> None:
    violations = checker._check_s9(tmp_path)
    assert _rules(violations) == {"W6-S9"}
    assert len(violations) == 7
