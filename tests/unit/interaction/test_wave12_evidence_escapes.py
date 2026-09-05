from __future__ import annotations

from agent.interaction.evidence import SpanKind, scan_spans


def test_escaped_quote_stays_inside_quoted_evidence_span() -> None:
    spans = scan_spans('Explain "the phrase \\"delete parser.py\\""')
    assert any(span.kind is SpanKind.DOUBLE_QUOTE and "delete parser.py" in span.text for span in spans)
