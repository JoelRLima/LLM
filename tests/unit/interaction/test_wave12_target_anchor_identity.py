from __future__ import annotations

from agent.interaction.guards import normalize_target_anchor_identity


def test_lexical_target_aliases_share_canonical_identity() -> None:
    assert normalize_target_anchor_identity("parser.py").canonical == "parser.py"
    assert normalize_target_anchor_identity("./parser.py").canonical == "parser.py"
    assert normalize_target_anchor_identity("a/../parser.py").canonical == "parser.py"
    assert normalize_target_anchor_identity("src\\parser.py").canonical == "src/parser.py"
