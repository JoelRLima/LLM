from __future__ import annotations

from agent.interaction.guards import normalize_target_anchor_identity


def test_terminal_and_semicolon_punctuation_do_not_enter_target_identity() -> None:
    left = normalize_target_anchor_identity("parser.py;")
    right = normalize_target_anchor_identity("parser.py.")
    assert left.canonical == right.canonical == "parser.py"
