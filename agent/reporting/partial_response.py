"""Safe composition of bounded observations with a canonical non-success status."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from agent.reporting.observation_evidence import (
    MAX_OBSERVATION_EVIDENCE_CHARS,
    MAX_OBSERVATION_RECORD_CHARS,
    project_tool_observation,
    serialize_tool_observations,
)
from agent.reporting.operational_outcome import OperationalOutcome
from agent.reporting.public_safety import sanitize_public_text

MAX_TOOL_RESULTS_SUMMARY_CHARS = MAX_OBSERVATION_EVIDENCE_CHARS
MAX_TOOL_RESULT_SUMMARY_CHARS = MAX_OBSERVATION_RECORD_CHARS
_PARTIAL_EVIDENCE_STATUSES = frozenset(
    {"blocked", "failed", "timed_out", "unavailable", "unverified"}
)
_INTERNAL_REASON_MARKERS = (
    "approval", "authority", "budget", "cancel", "checkpoint", "execution_aborted",
    "missing_required", "permission", "protocol", "reasoning_boundary", "requested_effect",
    "runtime", "safety", "task_", "unavailable", "workspace_grant",
)
_SUCCESS_CLAIM_PATTERNS = (
    re.compile(
        r"\b(?:a tarefa|o objetivo|a solicitacao|the task|the objective)\s+"
        r"(?:foi\s+)?(?:concluida|completada|bem sucedida|successful|completed|succeeded)\b"
    ),
    re.compile(r"\b(?:concluida|completada|completed|succeeded)\s+(?:com\s+)?(?:sucesso|successfully|success)\b"),
    re.compile(r"\b(?:successfully completed|completed successfully)\b"),
    re.compile(r"^\s*(?:success|succeeded|completed)\s*[.!]?\s*$"),
)
_UNSUPPORTED_EFFECT_PATTERNS = (
    re.compile(
        r"\b(?:o\s+)?(?:arquivo|ficheiro|file|alteracao|mudanca|escrita|write)\s+"
        r"(?:foi\s+)?(?:alterad[oa]|modificad[oa]|aplicad[oa]|escrit[oa]|salv[oa])\b"
    ),
    re.compile(r"\b(?:alteracao|mudanca|escrita)\s+(?:foi\s+)?(?:aplicada|executada|salva)\b"),
)


def _fold_reason(value: Any) -> str:
    text = str(value or "")
    normalized = unicodedata.normalize("NFKD", text).casefold()
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _known_observation_reason(value: Any) -> str | None:
    """Return a bounded, non-instructional classification for common read errors."""

    folded = _fold_reason(value)
    if not folded:
        return None
    if any(marker in folded for marker in ("arquivo nao encontrado", "file not found", "does not exist", "nao existe")):
        return "arquivo não encontrado"
    if any(marker in folded for marker in ("nao e um arquivo", "not a regular file")):
        return "o caminho não é um arquivo"
    if any(marker in folded for marker in ("encoding invalido", "invalid encoding")):
        return "codificação inválida"
    return None


def public_outcome_reason(outcome: OperationalOutcome) -> str | None:
    raw = outcome.failure_reason if outcome.terminal_status == "failed" else outcome.blocked_reason
    reason = _known_observation_reason(raw)
    if reason is not None:
        return reason
    if not raw:
        return None
    folded = _fold_reason(raw)
    if any(marker in folded for marker in _INTERNAL_REASON_MARKERS):
        return None
    safe = sanitize_public_text(str(raw)).strip()
    return safe if safe and len(safe) <= 240 and "\n" not in safe and "\r" not in safe else None


def history_observation_reason(history: Any) -> str | None:
    for entry in reversed(history or ()):
        if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
            continue
        reason = _known_observation_reason(entry["result"].get("error"))
        if reason is not None:
            return reason
    return None


def _partial_evidence_allowed(outcome: OperationalOutcome) -> bool:
    if outcome.terminal_status not in _PARTIAL_EVIDENCE_STATUSES:
        return False
    if any((outcome.requested_effects, outcome.executed_effects, outcome.waived_effects,
            outcome.pending_effects, outcome.mutation_occurred, outcome.rollback_occurred)):
        return False
    if outcome.terminal_status == "blocked":
        blocked_reason = _fold_reason(outcome.blocked_reason)
        if any(marker in blocked_reason for marker in ("approval", "authority", "permission", "safety", "workspace_grant")):
            return False
    return True


def has_usable_partial_evidence(outcome: OperationalOutcome, history: Any) -> bool:
    """Allow synthesis only for complete read evidence without effect obligations."""

    if not _partial_evidence_allowed(outcome):
        return False
    successful_observations = []
    for entry in history or ():
        if not isinstance(entry, dict):
            continue
        evidence = project_tool_observation(entry)
        if evidence.status != "succeeded" or evidence.executed is not True or not evidence.present:
            continue
        successful_observations.append(evidence)
    if not successful_observations:
        return False
    # A model may summarize complete observations, but a source-truncated
    # observation must remain a bounded preview in the deterministic fallback.
    return all(evidence.complete for evidence in successful_observations)


def _model_claims_global_success(answer: str) -> bool:
    folded = _fold_reason(answer)
    return any(pattern.search(folded) for pattern in _SUCCESS_CLAIM_PATTERNS)


def _model_claims_unsupported_effect(answer: str) -> bool:
    folded = _fold_reason(answer)
    return any(pattern.search(folded) for pattern in _UNSUPPORTED_EFFECT_PATTERNS)


def render_non_success_status(status: str, outcome: OperationalOutcome) -> str:
    if status == "failed":
        reason = public_outcome_reason(outcome)
        return f"A tarefa não pôde ser concluída: {reason}." if reason else "A tarefa não pôde ser concluída."
    if status == "permission_denied":
        return "A tarefa foi negada (status operacional: permission_denied)."
    reason = public_outcome_reason(outcome)
    return f"A tarefa terminou com status operacional: {status}. Motivo observado: {reason}." if reason else f"A tarefa terminou com status operacional: {status}."


def _render_pending_effects(effects: tuple[str, ...]) -> str:
    if len(effects) == 1:
        return (
            "A tarefa não foi concluída: o efeito solicitado permanece pendente "
            f"(efeitos pendentes: {', '.join(effects)})."
        )
    return (
        "A tarefa não foi concluída: os efeitos solicitados permanecem "
        f"pendentes ({', '.join(effects)})."
    )


def _render_rollback(outcome: OperationalOutcome) -> str:
    answer = "A altera\u00e7\u00e3o tentada foi revertida; nenhuma escrita persistiu no estado final."
    if outcome.pending_effects:
        answer += f" Efeitos ainda pendentes: {', '.join(outcome.pending_effects)}."
    return answer


def render_operational_answer(outcome: OperationalOutcome) -> str | None:
    """Render canonical operational truth when the outcome contains effects."""

    terminal_status = str(outcome.terminal_status or "unverified")
    successful_statuses = {"complete", "succeeded"}
    if terminal_status not in successful_statuses and not outcome.pending_effects and not outcome.rollback_occurred:
        return render_non_success_status(terminal_status, outcome)
    if not any((outcome.requested_effects, outcome.executed_effects, outcome.waived_effects,
                outcome.pending_effects, outcome.mutation_occurred, outcome.rollback_occurred)):
        return None
    if outcome.rollback_occurred:
        return _render_rollback(outcome)
    if outcome.pending_effects:
        return _render_pending_effects(outcome.pending_effects)
    if outcome.rollback_occurred:
        return "A alteração tentada foi revertida; nenhuma escrita persistiu no estado final."
    if terminal_status not in successful_statuses:
        return f"A tarefa terminou com status operacional: {terminal_status}."
    if "write" in outcome.executed_effects and outcome.mutation_occurred:
        files = f" Arquivos afetados: {', '.join(outcome.files_affected)}." if outcome.files_affected else ""
        validation = (
            " A alteração foi aplicada, mas não havia validação aplicável disponível."
            if outcome.validation_status == "unavailable"
            else f" Validação: {outcome.validation_status}." if outcome.validation_status else ""
        )
        return f"Uma alteração foi aplicada.{files}{validation}".strip()
    if "write" in outcome.waived_effects:
        return "Nenhuma escrita foi executada. A obrigação condicional de escrita foi dispensada com base na observação registrada."
    return "A tarefa terminou sem mutação operacional comprovada."


def _partial_fallback(outcome: OperationalOutcome, history: Any, descriptor_lookup: Any) -> str:
    status = render_non_success_status(str(outcome.terminal_status), outcome)
    reason = history_observation_reason(history)
    if reason and reason not in status:
        status += f" Motivo observado: {reason}."
    evidence = serialize_tool_observations(
        history or (), max_chars=MAX_TOOL_RESULTS_SUMMARY_CHARS,
        max_record_chars=MAX_TOOL_RESULT_SUMMARY_CHARS, descriptor_lookup=descriptor_lookup,
    )
    return f"{status}\n\nEvidência canônica das ferramentas:\n{evidence}" if evidence else status


def compose_operational_answer(
    outcome: OperationalOutcome,
    answer: str | None,
    history: Any,
    descriptor_lookup: Any,
    operational_renderer: Callable[[OperationalOutcome], str | None],
) -> str:
    """Keep terminal truth while retaining safe partial observations in public text."""

    if outcome.terminal_status in {"succeeded", "complete"}:
        # A successful terminal status still carries canonical effect facts.
        # Keep the deterministic effect projection in that case; otherwise a
        # route-specific model answer could hide a waived, pending, or applied
        # operation.  A plain successful answer remains untouched.
        if any((
            outcome.requested_effects,
            outcome.executed_effects,
            outcome.waived_effects,
            outcome.pending_effects,
            outcome.mutation_occurred,
            outcome.rollback_occurred,
        )):
            return operational_renderer(outcome) or str(answer or "")
        return str(answer or "")
    if not has_usable_partial_evidence(outcome, history):
        if _partial_evidence_allowed(outcome) and history:
            return _partial_fallback(outcome, history, descriptor_lookup)
        return operational_renderer(outcome) or f"A tarefa terminou com status operacional: {outcome.terminal_status}."
    evidence = serialize_tool_observations(
        history or (), max_chars=MAX_TOOL_RESULTS_SUMMARY_CHARS,
        max_record_chars=MAX_TOOL_RESULT_SUMMARY_CHARS, descriptor_lookup=descriptor_lookup,
    )
    candidate = sanitize_public_text(str(answer or "")).strip()
    if not candidate or _model_claims_global_success(candidate) or _model_claims_unsupported_effect(candidate):
        return _partial_fallback(outcome, history, descriptor_lookup)
    status = render_non_success_status(str(outcome.terminal_status), outcome)
    if not candidate.startswith(status):
        candidate = f"{status}\n\n{candidate}"
    if evidence and "Evidência canônica das ferramentas:" not in candidate:
        candidate += f"\n\nEvidência canônica das ferramentas:\n{evidence}"
    return candidate


__all__ = [
    "compose_operational_answer", "has_usable_partial_evidence", "history_observation_reason",
    "public_outcome_reason", "render_non_success_status", "render_operational_answer",
]
