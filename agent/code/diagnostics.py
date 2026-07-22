"""Classificação determinística de falhas antes de qualquer tentativa por LLM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.runtime.context import TaskResult, TaskStatus


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


class FailureClassifier:
    def classify(self, result: TaskResult) -> FailureClassification:
        text = " ".join(
            [
                result.error or "",
                result.summary,
                *(
                    str(item.get("code", "")) + " " + str(item.get("message", ""))
                    for item in result.diagnostics
                ),
            ]
        ).casefold()

        if result.status == TaskStatus.CANCELLED or "cancel" in text:
            return FailureClassification(
                FailureCategory.CANCELLED,
                False,
                "A operação foi cancelada; não tente novamente automaticamente.",
            )
        if "permission" in text or "capacidade" in text or "fora do projeto" in text:
            return FailureClassification(
                FailureCategory.PERMISSION,
                False,
                "A política de segurança bloqueou a operação; não contorne a restrição.",
            )
        if "timeout" in text or "timed_out" in text or "tempo limite" in text:
            return FailureClassification(
                FailureCategory.TIMEOUT,
                True,
                "Reduza o escopo da mudança; não aumente timeouts nem instale ferramentas.",
            )
        if (
            "unavailable" in text
            or "indispon" in text
            or "exige um modelgateway" in text
            or "não encontrado" in text and "comando" in text
        ):
            return FailureClassification(
                FailureCategory.TOOL_UNAVAILABLE,
                False,
                "O validator não está disponível; não alegue que os testes passaram.",
            )
        if "py_compile" in text or "syntax" in text or "syntaxerror" in text:
            return FailureClassification(
                FailureCategory.SYNTAX,
                True,
                "Corrija somente a sintaxe indicada e preserve o restante do arquivo.",
            )
        if "pytest" in text or "assert" in text or "test" in text and "failed" in text:
            return FailureClassification(
                FailureCategory.TEST,
                True,
                "Corrija a causa do teste falho sem remover ou enfraquecer o teste.",
            )
        if "hash" in text or "conflict" in text or "divergente" in text:
            return FailureClassification(
                FailureCategory.CONFLICT,
                True,
                "Releia o arquivo e gere base_hash/expected_text a partir do estado atual.",
            )
        if "json" in text or "changeset" in text or "estrutur" in text:
            return FailureClassification(
                FailureCategory.STRUCTURED_OUTPUT,
                True,
                "Retorne apenas um ChangeSet válido conforme o schema, sem texto adicional.",
            )
        return FailureClassification(
            FailureCategory.UNKNOWN,
            True,
            "Faça a menor alteração possível e não repita uma proposta idêntica.",
        )
