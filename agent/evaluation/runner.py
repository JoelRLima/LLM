"""Executor, graders and minimal aggregation for capability scenarios."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, Protocol

from agent.evaluation.contracts import (
    CapabilityScenario,
    EvaluationFailure,
    ExecutionObservation,
    ScenarioExpectation,
    ScenarioReport,
)
from agent.evaluation.measurement_projection import (
    _bounded,
)
from agent.evaluation.measurement_projection import (
    project_measurement_summary as _measurement_summary,
)
from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path


class ScenarioExecutor(Protocol):
    """Adapter between the evaluator and an agent implementation."""

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        ...


def _safe_relative_path(root: Path, relative: str) -> Path:
    try:
        return resolve_workspace_path(root, relative)
    except (OSError, RuntimeError, WorkspacePathError) as exc:
        raise ValueError(f"Caminho fora do workspace do cenário: {relative}") from exc


def _snapshot(root: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    ignored = {".git", ".pytest_cache", "__pycache__", ".temp_analysis"}
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and not ignored.intersection(item.relative_to(root).parts)
    ):
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class CapabilityEvaluator:
    """Prepares a scenario, executes it and verifies observable effects."""

    def __init__(self, executor: ScenarioExecutor):
        self.executor = executor

    def evaluate(self, scenario: CapabilityScenario, workspace: Path) -> ScenarioReport:
        workspace.mkdir(parents=True, exist_ok=True)
        if any(workspace.iterdir()):
            raise ValueError("O workspace de avaliação deve estar vazio.")

        for relative, content in scenario.initial_files.items():
            target = _safe_relative_path(workspace, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        before = _snapshot(workspace)
        observation = self.executor.execute(scenario.objective, workspace)
        after = _snapshot(workspace)
        changed_files = tuple(
            sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        )
        failures = self._verify(scenario, workspace, observation, before, after, changed_files)
        return ScenarioReport(
            scenario_id=scenario.scenario_id,
            capability=scenario.capability,
            passed=not failures,
            observation=observation,
            failures=tuple(failures),
            changed_files=changed_files,
            expected=scenario.expectation,
        )

    def evaluate_set(
        self,
        scenarios: Iterable[CapabilityScenario],
        workspace_root: Path,
    ) -> "EvaluationSetReport":
        """Runs scenarios in isolated subdirectories below an empty root."""

        workspace_root.mkdir(parents=True, exist_ok=True)
        if any(workspace_root.iterdir()):
            raise ValueError("A raiz do conjunto de avaliação deve estar vazia.")
        reports = []
        for scenario in scenarios:
            scenario_root = _safe_relative_path(workspace_root, scenario.scenario_id)
            reports.append(self.evaluate(scenario, scenario_root))
        return EvaluationSetReport(tuple(reports))

    @staticmethod
    def _verify(
        scenario: CapabilityScenario,
        workspace: Path,
        observation: ExecutionObservation,
        before: Dict[str, str],
        after: Dict[str, str],
        changed_files: tuple[str, ...],
    ) -> list[EvaluationFailure]:
        expected = scenario.expectation
        failures: list[EvaluationFailure] = []
        failures.extend(CapabilityEvaluator._verify_outcome(expected, observation))
        failures.extend(CapabilityEvaluator._verify_answer(expected, observation))
        failures.extend(CapabilityEvaluator._verify_files(expected, workspace))
        failures.extend(CapabilityEvaluator._verify_changes(expected, before, after, changed_files))
        return failures

    @staticmethod
    def _verify_outcome(
        expected: ScenarioExpectation, observation: ExecutionObservation
    ) -> list[EvaluationFailure]:
        failures: list[EvaluationFailure] = []
        if observation.success != expected.success:
            failures.append(
                EvaluationFailure(
                    "unexpected_success",
                    f"Esperado success={expected.success}, recebido {observation.success}.",
                )
            )
        if expected.max_steps is not None and observation.steps > expected.max_steps:
            failures.append(
                EvaluationFailure(
                    "step_limit",
                    f"Foram usados {observation.steps} passos; limite: {expected.max_steps}.",
                )
            )
        return failures

    @staticmethod
    def _verify_answer(
        expected: ScenarioExpectation, observation: ExecutionObservation
    ) -> list[EvaluationFailure]:
        failures: list[EvaluationFailure] = []
        answer_lower = observation.answer.casefold()
        for text in expected.answer_contains:
            if text.casefold() not in answer_lower:
                failures.append(EvaluationFailure("answer_missing", f"Resposta não contém: {text!r}."))
        for text in expected.answer_not_contains:
            if text.casefold() in answer_lower:
                failures.append(EvaluationFailure("answer_forbidden", f"Resposta contém trecho proibido: {text!r}."))
        return failures

    @staticmethod
    def _verify_files(expected: ScenarioExpectation, workspace: Path) -> list[EvaluationFailure]:
        failures: list[EvaluationFailure] = []
        for file_expected in expected.files:
            target = _safe_relative_path(workspace, file_expected.path)
            if target.exists() != file_expected.exists:
                failures.append(
                    EvaluationFailure(
                        "file_existence",
                        f"Estado inesperado para '{file_expected.path}': exists={target.exists()}.",
                    )
                )
                continue
            if not file_expected.exists:
                continue
            content = target.read_text(encoding="utf-8")
            if file_expected.exact_content is not None and content != file_expected.exact_content:
                failures.append(EvaluationFailure("file_content", f"Conteúdo exato divergente em '{file_expected.path}'."))
            for text in file_expected.contains:
                if text not in content:
                    failures.append(EvaluationFailure("file_missing_text", f"'{file_expected.path}' não contém {text!r}."))
            for text in file_expected.not_contains:
                if text in content:
                    failures.append(EvaluationFailure("file_forbidden_text", f"'{file_expected.path}' contém trecho proibido {text!r}."))
        return failures

    @staticmethod
    def _verify_changes(
        expected: ScenarioExpectation,
        before: Dict[str, str],
        after: Dict[str, str],
        changed_files: tuple[str, ...],
    ) -> list[EvaluationFailure]:
        failures: list[EvaluationFailure] = []
        for relative in expected.unchanged_files:
            if before.get(relative) != after.get(relative):
                failures.append(EvaluationFailure("file_changed", f"'{relative}' deveria permanecer inalterado."))
        if expected.allowed_changed_files:
            allowed = set(expected.allowed_changed_files)
            unexpected = sorted(set(changed_files) - allowed)
            if unexpected:
                failures.append(
                    EvaluationFailure(
                        "unexpected_changes",
                        f"Arquivos alterados fora da allowlist: {', '.join(unexpected)}.",
                    )
                )
        return failures


class EvaluationSetReport:
    """Minimal aggregation and machine-readable export of scenario results."""

    def __init__(self, reports: tuple[ScenarioReport, ...]):
        self.reports = reports

    @property
    def total(self) -> int:
        return len(self.reports)

    @property
    def passed(self) -> int:
        return sum(report.passed for report in self.reports)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "pass_rate": self.pass_rate,
                "failures_by_category": self._failures_by_category(),
            },
            "tasks": [_report_to_dict(report) for report in self.reports],
        }

    def write_json(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _failures_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for report in self.reports:
            if not report.passed:
                counts[report.capability] = counts.get(report.capability, 0) + 1
        return dict(sorted(counts.items()))


def _report_to_dict(report: ScenarioReport) -> dict[str, object]:
    failures = [{"code": failure.code, "message": _bounded(failure.message)} for failure in report.failures]
    return {
        "task_id": report.scenario_id,
        "category": report.capability,
        "passed": report.passed,
        "reason": "passed" if report.passed else "; ".join(item["message"] for item in failures),
        "expected": {
            "success": report.expected.success,
            "files": [{"path": item.path, "exists": item.exists} for item in report.expected.files],
            "answer_contains": list(report.expected.answer_contains),
        },
        "observed": {
            "success": report.observation.success,
            "steps": report.observation.steps,
            "answer_preview": _bounded(report.observation.answer),
            "changed_files": list(report.changed_files),
            "error": _bounded(report.observation.error),
        },
        "measurement": _measurement_summary(report.observation.measurement),
        "failures": failures,
    }
