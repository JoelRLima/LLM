from __future__ import annotations

from agent.interaction.evidence import (
    SpanKind,
    dot_is_token_internal,
    evidence_is_plain_exact,
    plain_occurrences,
    scan_clause_spans,
    scan_spans,
)


def test_quotes_code_fences_and_blockquotes_are_not_plain_evidence() -> None:
    text = (
        'Explain "delete parser.py" and `delete parser.py`.\n'
        "> delete parser.py\n"
        "```\ndelete parser.py\n```"
    )
    spans = scan_spans(text)
    assert all(
        span.kind is not SpanKind.PLAIN
        for span in spans
        if "delete parser.py" in span.text
    )
    assert evidence_is_plain_exact(text, "delete parser.py") is False


def test_apostrophe_is_plain_and_does_not_start_a_quote() -> None:
    text = "don't delete parser.py"
    assert evidence_is_plain_exact(text, "don't delete parser.py") is True
    assert scan_spans(text)[0].kind is SpanKind.PLAIN


def test_clause_scanner_preserves_path_and_decimal_dots() -> None:
    text = "Delete parser.py. Then inspect config.json and 127.0.0.1."
    clauses = scan_clause_spans(text)
    assert [clause.text for clause in clauses] == [
        "Delete parser.py.",
        " Then inspect config.json and 127.0.0.1.",
    ]
    assert dot_is_token_internal("a/../parser.py", 2) is True
    assert dot_is_token_internal("foo.bar", 3) is True


def test_plain_occurrence_does_not_cross_non_plain_spans() -> None:
    text = 'delete "parser.py"'
    assert plain_occurrences(text, "delete parser.py") == []
