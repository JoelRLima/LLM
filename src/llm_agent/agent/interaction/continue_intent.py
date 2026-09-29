"""Exact full-utterance natural resume guard (W12 P23)."""

from __future__ import annotations

from enum import Enum

from .evidence import SpanKind, scan_spans
from .lexicon import DEICTIC_TARGETS
from .profile import CAUTIOUS_SIGNALS, ECONOMY_SIGNALS, SMART_SIGNALS


class ResumeClassification(str, Enum):
    DIRECT_RESUME = "DIRECT_RESUME"
    NEGATED = "NEGATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    META = "META"
    CONTEXTUAL = "CONTEXTUAL"
    OVERRIDE = "OVERRIDE"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


PT_DIRECT_CORES = (
    "continue a tarefa",
    "continue a tarefa anterior",
    "retome a tarefa",
    "retome a tarefa anterior",
    "retome a execução da tarefa",
    "retome a execucao da tarefa",
)
PT_MODAL_CORES = (
    "continuar a tarefa",
    "continuar a tarefa anterior",
    "retomar a tarefa",
    "retomar a tarefa anterior",
    "retomar a execução da tarefa",
    "retomar a execucao da tarefa",
)
EN_CORES = (
    "resume the task",
    "resume the previous task",
    "continue the task",
    "continue the previous task",
    "resume task execution",
)

AMBIGUOUS_RESUMES = frozenset(
    {
        "continue", "continua", "resume", "pode continuar", "pode seguir", "continue de onde parou",
        "retome de onde parou", "continue where you left off", "go on", "keep going", "continue explicando",
    }
)

RESUME_NEGATIONS = ("não", "nao", "nunca", "não quero", "nao quero", "do not", "don't", "dont", "never")
OVERRIDE_CUES = (
    "/cautious", "/economy", "/normal", "/smart", "/read", "/plan", "/do",
    "apenas leia", "somente leia", "não altere", "nao altere", "não modifique", "nao modifique",
    "não execute", "nao execute", "não faça nada", "nao faca nada", "com cuidado", "de forma cautelosa",
    "se", "caso", "em", "no", "na", "para", "read only", "only read", "do not change", "do not modify",
    "do not execute", "do nothing", "carefully", "cautiously", "if", "provided", "on", "in", "for", "to",
)


def _normalize_resume(text: str) -> str:
    value = text.casefold().strip()
    if value and value[-1] in ".!?":
        value = value[:-1].strip()
    return value


def _accepted_bases() -> tuple[str, ...]:
    values = list(PT_DIRECT_CORES) + list(EN_CORES)
    values.extend(f"{prefix} {core}" for prefix in ("por favor",) for core in PT_DIRECT_CORES)
    values.extend(f"{prefix} {core}" for prefix in ("pode", "poderia") for core in PT_MODAL_CORES)
    values.extend(f"{prefix} {core}" for prefix in ("please", "can you", "could you") for core in EN_CORES)
    return tuple(sorted(values, key=len, reverse=True))


def _base_match(value: str) -> tuple[str, str] | None:
    for base in _accepted_bases():
        if value == base:
            return base, ""
        for delimiter in (" ", ",", ";", ".", "!", "?"):
            if value.startswith(base + delimiter):
                return base, value[len(base) :]
    return None


def _quoted_resume(value: str) -> bool:
    for span in scan_spans(value):
        if span.kind is not SpanKind.PLAIN and any(
            token in span.text for token in ("continue", "retome", "retomar", "resume", "continuar")
        ):
            return True
    return False


def _starts_exact(value: str, cues: tuple[str, ...]) -> bool:
    for cue in sorted(cues, key=len, reverse=True):
        if value == cue or value.startswith(cue + " ") or value.startswith(cue + ","):
            return True
    return False


def _override_remainder(value: str) -> str:
    remainder = value.strip()
    if remainder.startswith((",", ";")):
        remainder = remainder[1:].strip()
    for connector in ("mas", "e", "but", "and"):
        prefix = connector + " "
        if remainder.startswith(prefix):
            remainder = remainder[len(prefix) :].strip()
            break
    return remainder


def classify_resume_request(text: str) -> ResumeClassification:
    if type(text) is not str:
        return ResumeClassification.UNKNOWN
    value = _normalize_resume(text)
    if not value:
        return ResumeClassification.UNKNOWN
    if value in AMBIGUOUS_RESUMES:
        return ResumeClassification.CONTEXTUAL
    if _quoted_resume(value):
        return ResumeClassification.META
    if _starts_exact(value, tuple(RESUME_NEGATIONS)):
        return ResumeClassification.NEGATED
    if value.startswith(("if ", "what if ", "se eu dissesse ", "if i said ")):
        return ResumeClassification.HYPOTHETICAL
    matched = _base_match(value)
    if matched is not None:
        _, remainder = matched
        remainder = remainder.strip()
        if not remainder:
            return ResumeClassification.DIRECT_RESUME
        if remainder in {"agora", "por favor", "agora por favor", "now", "please", "now please"}:
            return ResumeClassification.DIRECT_RESUME
        override = _override_remainder(remainder)
        if override.startswith((".", "!", "?")) and override[1:].strip():
            return ResumeClassification.OVERRIDE
        effort_signals = CAUTIOUS_SIGNALS + SMART_SIGNALS + ECONOMY_SIGNALS
        if _starts_exact(override, OVERRIDE_CUES) or any(signal in override for signal in effort_signals):
            return ResumeClassification.OVERRIDE
        return ResumeClassification.UNKNOWN
    if value in DEICTIC_TARGETS or value.startswith(("continue ", "retome ", "resume ")):
        return ResumeClassification.CONTEXTUAL
    if _starts_exact(value, ("explique", "what does", "if i said", "se eu dissesse")):
        return ResumeClassification.META
    return ResumeClassification.UNKNOWN


class DirectTaskResumeGuard:
    @staticmethod
    def classify(text: str) -> ResumeClassification:
        return classify_resume_request(text)


DirectResumeResult = ResumeClassification


__all__ = [
    "AMBIGUOUS_RESUMES",
    "DirectResumeResult",
    "DirectTaskResumeGuard",
    "EN_CORES",
    "PT_DIRECT_CORES",
    "PT_MODAL_CORES",
    "ResumeClassification",
    "classify_resume_request",
]
