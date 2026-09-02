"""Classificação determinística de falhas antes de qualquer tentativa por LLM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.runtime.context import Artifact, TaskResult, TaskStatus
from agent.runtime.failures import FailureFact


class FailureCategory(str, Enum):
    SYNTAX = "syntax"
    TEST = "test"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    TOOL_UNAVAILABLE = "tool_unavailable"
    CONFLICT = "conflict"
    STRUCTURED_OUTPUT = "structured_output"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureClassification:
    category: FailureCategory
    retryable: bool
    guidance: str


def _failure_result(
    *, code: str | None, error: str | None = None,
    status: TaskStatus = TaskStatus.FAILED, summary: str = "",
    artifacts: tuple[Artifact, ...] = (),
    diagnostics: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
) -> TaskResult:
    fact = FailureFact.from_code(code, status=status, message=error)
    return TaskResult(status, summary, artifacts, diagnostics, error, metadata or {}, fact.code)


def _failure_fact(result: TaskResult) -> FailureFact | None:
    if result.status == TaskStatus.SUCCEEDED:
        return None
    return FailureFact.from_code(
        result.failure_code,
        status=result.status,
        message=result.error,
    )


def _diagnostic_codes(result: TaskResult) -> set[str]:
    return {
        str(item["code"]).strip().upper()
        for item in result.diagnostics
        if isinstance(item, dict) and item.get("code")
    }


class FailureClassifier:
    def classify(self, result: TaskResult) -> FailureClassification:
        fact = _failure_fact(result)
        if fact is None:
            return FailureClassification(
                FailureCategory.UNKNOWN,
                False,
                "Nenhuma falha estruturada foi observada.",
            )
        if fact.status == TaskStatus.CANCELLED.value or fact.code == "CANCELLED":
            return FailureClassification(
                FailureCategory.CANCELLED,
                False,
                "A operação foi cancelada; não tente novamente automaticamente.",
            )

        diagnostic_codes = _diagnostic_codes(result)
        if fact.status in {TaskStatus.BLOCKED.value, TaskStatus.PERMISSION_DENIED.value} or fact.code in {
            "AUTHORITY_DENIED",
            "PERMISSION_DENIED",
            "TOOL_BLOCKED",
        }:
            return FailureClassification(
                FailureCategory.PERMISSION,
                False,
                "A política de segurança bloqueou a operação; não contorne a restrição.",
            )
        if fact.status == TaskStatus.TIMED_OUT.value or fact.code in {"TIMEOUT", "WATCHDOG_TIMEOUT"}:
            return FailureClassification(
                FailureCategory.TIMEOUT,
                fact.retryable,
                "Reduza o escopo da mudança; não aumente timeouts nem instale ferramentas.",
            )
        if fact.status == TaskStatus.UNAVAILABLE.value or fact.code == "TOOL_UNAVAILABLE":
            return FailureClassification(
                FailureCategory.TOOL_UNAVAILABLE,
                fact.retryable,
                "O validator não está disponível; não alegue que os testes passaram.",
            )
        if diagnostic_codes & {"PYTHON_SYNTAX", "SYNTAX", "SYNTAX_ERROR", "VALIDATION_SYNTAX"}:
            return FailureClassification(
                FailureCategory.SYNTAX,
                fact.retryable,
                "Corrija somente a sintaxe indicada e preserve o restante do arquivo.",
            )
        if diagnostic_codes & {"PYTEST", "TEST", "TEST_FAILED", "VALIDATION_FAILED"}:
            return FailureClassification(
                FailureCategory.TEST,
                fact.retryable,
                "Corrija a causa do teste falho sem remover ou enfraquecer o teste.",
            )
        if diagnostic_codes & {"CHANGE_CONFLICT", "CHANGESET_CONFLICT", "HASH_DIVERGED"}:
            return FailureClassification(
                FailureCategory.CONFLICT,
                fact.retryable,
                "Releia o arquivo e gere base_hash/expected_text a partir do estado atual.",
            )
        if fact.code == "INVALID_RESPONSE" or diagnostic_codes & {
            "STRUCTURED_OUTPUT",
            "STRUCTURED_OUTPUT_FAILURE",
        }:
            return FailureClassification(
                FailureCategory.STRUCTURED_OUTPUT,
                fact.retryable,
                "Retorne apenas um ChangeSet válido conforme o schema, sem texto adicional.",
            )
        return FailureClassification(
            FailureCategory.UNKNOWN,
            fact.retryable,
            "Faça a menor alteração possível e não repita uma proposta idêntica.",
        )
