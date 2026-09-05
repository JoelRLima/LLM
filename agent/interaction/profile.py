"""Deterministic effort-profile signals for W12."""

from __future__ import annotations

from agent.runtime.task_directives import DeliberationProfile, TaskDirective

from .evidence import SpanKind, scan_spans

ECONOMY_SIGNALS = (
    "rápido", "rapidamente", "brevemente", "olhada rápida", "seja breve", "sem pensar muito",
    "quickly", "briefly", "quick look", "keep it short",
)
SMART_SIGNALS = (
    "análise profunda", "profundamente", "minucioso", "minuciosa", "minuciosamente", "análise robusta",
    "analise bem", "pense com cuidado", "deep analysis", "deeply", "thoroughly", "comprehensive",
    "robust analysis", "think carefully",
)
CAUTIOUS_SIGNALS = (
    "auditoria adversarial", "alta criticidade", "não deixe passar nada", "muito cauteloso",
    "seja extremamente cauteloso", "adversarial", "security-critical", "security critical", "fail closed",
    "extremely cautious", "do not miss anything",
)


def _plain_signal_present(subject: str, signal: str) -> bool:
    for span in scan_spans(subject.casefold()):
        if span.kind is not SpanKind.PLAIN:
            continue
        offset = span.text.find(signal)
        while offset >= 0:
            before = span.text[offset - 1] if offset else ""
            end = offset + len(signal)
            after = span.text[end] if end < len(span.text) else ""
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return True
            offset = span.text.find(signal, offset + 1)
    return False


def deterministic_effort_signal(subject: str) -> DeliberationProfile | None:
    """Return the highest exact closed signal occurring in PLAIN text."""

    if any(_plain_signal_present(subject, signal) for signal in CAUTIOUS_SIGNALS):
        return DeliberationProfile.CAUTIOUS
    if any(_plain_signal_present(subject, signal) for signal in SMART_SIGNALS):
        return DeliberationProfile.SMART
    if any(_plain_signal_present(subject, signal) for signal in ECONOMY_SIGNALS):
        return DeliberationProfile.ECONOMY
    return None


def select_fresh_profile(
    subject: str,
    *,
    directive: TaskDirective | None,
    profile_explicit: bool = False,
    explicit_profile: DeliberationProfile | None = None,
) -> DeliberationProfile:
    if profile_explicit and explicit_profile is not None:
        return explicit_profile
    profile = deterministic_effort_signal(subject) or DeliberationProfile.NORMAL
    if directive is TaskDirective.DO and profile is DeliberationProfile.ECONOMY:
        return DeliberationProfile.NORMAL
    return profile


def response_reasoning_budget(profile: DeliberationProfile, baseline: int) -> int:
    baseline = max(0, int(baseline))
    if profile is DeliberationProfile.ECONOMY:
        return 0
    if profile is DeliberationProfile.SMART:
        return max(baseline, 1024)
    if profile is DeliberationProfile.CAUTIOUS:
        return max(baseline, 2048)
    return baseline


__all__ = [
    "CAUTIOUS_SIGNALS",
    "ECONOMY_SIGNALS",
    "SMART_SIGNALS",
    "deterministic_effort_signal",
    "response_reasoning_budget",
    "select_fresh_profile",
]
