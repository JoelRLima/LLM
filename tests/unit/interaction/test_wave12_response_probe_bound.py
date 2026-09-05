from __future__ import annotations

from agent.interaction.response import MAX_RESPONSE_CONTEXT_EXACT_PROBES


def test_response_probe_cap_is_two() -> None:
    assert MAX_RESPONSE_CONTEXT_EXACT_PROBES == 2
