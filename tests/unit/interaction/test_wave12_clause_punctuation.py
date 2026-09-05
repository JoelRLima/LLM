from __future__ import annotations

from agent.interaction.evidence import scan_clause_spans


def test_terminal_dot_splits_but_token_internal_dot_does_not() -> None:
    clauses = scan_clause_spans("Delete parser.py. Then inspect foo.bar")
    assert len(clauses) == 2
    assert "parser.py." in clauses[0].text
    assert "foo.bar" in clauses[1].text
