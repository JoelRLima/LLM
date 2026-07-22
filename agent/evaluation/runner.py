"""Executor e verificador hermético de cenários de capacidade."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Protocol

from agent.evaluation.contracts import (
    CapabilityScenario,
    EvaluationFailure,
    ExecutionObservation,
    ScenarioExpectation,
    ScenarioReport,
)


class ScenarioExecutor(Protocol):
    """Adapter entre o evaluator e uma implementação do agente."""

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        ...


def _safe_relative_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho fora do workspace do cenário: {relative}") from exc
    return candidate


def _snapshot(root: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[relative] = digest
    return result


class CapabilityEvaluator:
    """Prepara um cenário, executa o agente e verifica efeitos reais."""

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
        )

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
    def _verify_outcome(expected: ScenarioExpectation, observation: ExecutionObservation) -> list[EvaluationFailure]:
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
    def _verify_answer(expected: ScenarioExpectation, observation: ExecutionObservation) -> list[EvaluationFailure]:
        failures: list[EvaluationFailure] = []
        answer_lower = observation.answer.casefold()
        for text in expected.answer_contains:
            if text.casefold() not in answer_lower:
                failures.append(
                    EvaluationFailure("answer_missing", f"Resposta não contém: {text!r}.")
                )
        for text in expected.answer_not_contains:
            if text.casefold() in answer_lower:
                failures.append(
                    EvaluationFailure("answer_forbidden", f"Resposta contém trecho proibido: {text!r}.")
                )
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
                failures.append(
                    EvaluationFailure(
                        "file_content",
                        f"Conteúdo exato divergente em '{file_expected.path}'.",
                    )
                )
            for text in file_expected.contains:
                if text not in content:
                    failures.append(
                        EvaluationFailure(
                            "file_missing_text",
                            f"'{file_expected.path}' não contém {text!r}.",
                        )
                    )
            for text in file_expected.not_contains:
                if text in content:
                    failures.append(
                        EvaluationFailure(
                            "file_forbidden_text",
                            f"'{file_expected.path}' contém trecho proibido {text!r}.",
                        )
                    )
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
                failures.append(
                    EvaluationFailure("file_changed", f"'{relative}' deveria permanecer inalterado.")
                )

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
