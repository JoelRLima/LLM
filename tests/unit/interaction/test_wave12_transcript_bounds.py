from __future__ import annotations

from agent.interaction.transcript import MAX_PRIOR_CONTENT, bounded_prior_pairs


def test_total_prior_content_is_bounded() -> None:
    messages = [{"role": "system", "content": "s"}]
    for _index in range(4):
        messages.extend((
            {"role": "user", "content": "u" * 1024},
            {"role": "assistant", "content": "a" * 1024},
        ))
    pairs = bounded_prior_pairs(messages)
    assert sum(len(item["content"]) for pair in pairs for item in pair) <= MAX_PRIOR_CONTENT
