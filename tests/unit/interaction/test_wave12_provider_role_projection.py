from __future__ import annotations

from agent.interaction.transcript import provider_message_projection


def test_provider_projection_keeps_only_first_system_and_user_assistant_pairs() -> None:
    messages = [
        {"role": "system", "content": "first"},
        {"role": "tool", "content": "tool"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "system", "content": "later"},
        {"role": "function", "content": "fn"},
    ]
    assert provider_message_projection(messages) == [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
