from __future__ import annotations

from agent.interaction.transcript import bounded_prior_pairs


def test_response_context_view_is_pair_bounded_before_fitting() -> None:
    messages = [{"role": "system", "content": "s"}]
    for index in range(10):
        messages.extend((
            {"role": "user", "content": f"u{index}"},
            {"role": "assistant", "content": f"a{index}"},
        ))
    assert len(bounded_prior_pairs(messages)) == 4
