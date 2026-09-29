"""Character-precise evidence and clause scanner used by every W12 guard."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SpanKind(str, Enum):
    PLAIN = "PLAIN"
    SINGLE_QUOTE = "SINGLE_QUOTE"
    DOUBLE_QUOTE = "DOUBLE_QUOTE"
    INLINE_CODE = "INLINE_CODE"
    FENCED_CODE = "FENCED_CODE"
    MARKDOWN_BLOCKQUOTE = "MARKDOWN_BLOCKQUOTE"


class EvidenceSpanKind(str, Enum):
    PLAIN = "PLAIN"
    SINGLE_QUOTE = "SINGLE_QUOTE"
    DOUBLE_QUOTE = "DOUBLE_QUOTE"
    INLINE_CODE = "INLINE_CODE"
    FENCED_CODE = "FENCED_CODE"
    MARKDOWN_BLOCKQUOTE = "MARKDOWN_BLOCKQUOTE"


@dataclass(frozen=True, slots=True)
class Span:
    kind: SpanKind
    start: int
    end: int
    text: str

    @property
    def content(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class ClauseSpan:
    start: int
    end: int
    text: str

    @property
    def content(self) -> str:
        return self.text


def _is_apostrophe(text: str, index: int) -> bool:
    return (
        index > 0
        and index + 1 < len(text)
        and text[index - 1].isalnum()
        and text[index + 1].isalnum()
    )


def _is_token_character(char: str) -> bool:
    return char.isalnum() or char in "_-/:\\"


def dot_is_token_internal(text: str, index: int) -> bool:
    if index < 0 or index + 1 >= len(text):
        return False
    previous = text[index - 1]
    following = text[index + 1]
    if following == "/" and (index == 0 or previous.isspace()):
        return True
    if following == "." and index + 2 < len(text) and _is_token_character(text[index + 2]):
        return True
    if previous == "." and index > 1 and _is_token_character(text[index - 2]) and _is_token_character(following):
        return True
    return _is_token_character(previous) and _is_token_character(following)


def scan_spans(text: str) -> list[Span]:
    if type(text) is not str:
        raise TypeError("evidence subject must be a string")
    if not text:
        return []
    kinds: list[SpanKind] = [SpanKind.PLAIN] * len(text)
    state = SpanKind.PLAIN
    index = 0
    line_start = True
    while index < len(text):
        char = text[index]
        if state is SpanKind.FENCED_CODE:
            kinds[index] = state
            if text.startswith("```", index):
                kinds[index : index + 3] = [state] * min(3, len(text) - index)
                index += 3
                state = SpanKind.PLAIN
                line_start = False
                continue
            line_start = char == "\n"
            index += 1
            continue
        if state is SpanKind.INLINE_CODE:
            kinds[index] = state
            if char == "`" and not text.startswith("```", index):
                state = SpanKind.PLAIN
            line_start = char == "\n"
            index += 1
            continue
        if state in (SpanKind.SINGLE_QUOTE, SpanKind.DOUBLE_QUOTE):
            kinds[index] = state
            delimiter = "'" if state is SpanKind.SINGLE_QUOTE else '"'
            if char == "\\" and index + 1 < len(text) and text[index + 1] in (delimiter, "\\"):
                kinds[index + 1] = state
                index += 2
                line_start = False
                continue
            if char == delimiter:
                state = SpanKind.PLAIN
            line_start = char == "\n"
            index += 1
            continue
        if state is SpanKind.MARKDOWN_BLOCKQUOTE:
            if char == "\n":
                state = SpanKind.PLAIN
                line_start = True
            else:
                kinds[index] = state
                line_start = False
            index += 1
            continue

        if line_start:
            probe = index
            while probe < len(text) and text[probe] in " \t":
                probe += 1
            if probe == index or probe < len(text):
                if probe < len(text) and text[probe] == ">":
                    for mark in range(probe, len(text)):
                        if text[mark] == "\n":
                            break
                        kinds[mark] = SpanKind.MARKDOWN_BLOCKQUOTE
                    state = SpanKind.MARKDOWN_BLOCKQUOTE
                    index = probe
                    line_start = False
                    continue
            line_start = False
        if text.startswith("```", index):
            kinds[index : index + 3] = [SpanKind.FENCED_CODE] * min(3, len(text) - index)
            state = SpanKind.FENCED_CODE
            index += 3
            line_start = False
            continue
        if char == "`":
            kinds[index] = SpanKind.INLINE_CODE
            state = SpanKind.INLINE_CODE
        elif char == '"':
            kinds[index] = SpanKind.DOUBLE_QUOTE
            state = SpanKind.DOUBLE_QUOTE
        elif char == "'" and not _is_apostrophe(text, index):
            kinds[index] = SpanKind.SINGLE_QUOTE
            state = SpanKind.SINGLE_QUOTE
        else:
            kinds[index] = SpanKind.PLAIN
        index += 1
        line_start = char == "\n"

    spans: list[Span] = []
    start = 0
    current = kinds[0]
    for index in range(1, len(kinds) + 1):
        if index == len(kinds) or kinds[index] is not current:
            spans.append(Span(current, start, index, text[start:index]))
            if index < len(kinds):
                start = index
                current = kinds[index]
    return spans


scan_evidence_spans = scan_spans
scan_evidence = scan_spans


def _plain_boundary(text: str, kinds: list[SpanKind], index: int) -> bool:
    if kinds[index] is not SpanKind.PLAIN:
        return False
    char = text[index]
    if char in ";!?":
        return True
    return char == "." and not dot_is_token_internal(text, index)


def scan_clause_spans(text: str) -> list[ClauseSpan]:
    spans = scan_spans(text)
    if not text:
        return []
    kinds: list[SpanKind] = [SpanKind.PLAIN] * len(text)
    for span in spans:
        kinds[span.start : span.end] = [span.kind] * (span.end - span.start)
    result: list[ClauseSpan] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n" and kinds[index] is SpanKind.PLAIN:
            if start < index:
                result.append(ClauseSpan(start, index, text[start:index]))
            start = index + 1
        elif _plain_boundary(text, kinds, index):
            if start < index + 1:
                result.append(ClauseSpan(start, index + 1, text[start : index + 1]))
            start = index + 1
    if start < len(text):
        result.append(ClauseSpan(start, len(text), text[start:]))
    return [span for span in result if span.text.strip()]


scan_clauses = scan_clause_spans


def normalize_clause_for_guard(exact_clause_text: str) -> str:
    value = exact_clause_text.casefold().strip()
    if value and value[-1] in ";.!?":
        value = value[:-1].strip()
    return value


GUARD_TARGET_PUNCTUATION = "()[]{}<>,:;.!?"


def strip_target_surrounding_punctuation(value: str) -> str:
    return value.strip(GUARD_TARGET_PUNCTUATION)


def plain_occurrences(text: str, evidence: str, *, limit: int = 32) -> list[tuple[int, int]]:
    if type(text) is not str or type(evidence) is not str or not evidence:
        return []
    spans = scan_spans(text)
    result: list[tuple[int, int]] = []
    for span in spans:
        if span.kind is not SpanKind.PLAIN:
            continue
        offset = span.text.find(evidence)
        while offset >= 0 and len(result) < limit:
            result.append((span.start + offset, span.start + offset + len(evidence)))
            offset = span.text.find(evidence, offset + 1)
        if len(result) >= limit:
            break
    return result


def evidence_is_plain_exact(text: str, evidence: str) -> bool:
    return bool(plain_occurrences(text, evidence, limit=1))


__all__ = [
    "ClauseSpan",
    "EvidenceSpanKind",
    "GUARD_TARGET_PUNCTUATION",
    "Span",
    "SpanKind",
    "dot_is_token_internal",
    "evidence_is_plain_exact",
    "normalize_clause_for_guard",
    "plain_occurrences",
    "scan_clause_spans",
    "scan_clauses",
    "scan_evidence",
    "scan_evidence_spans",
    "scan_spans",
    "strip_target_surrounding_punctuation",
]
