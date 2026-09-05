from __future__ import annotations

import json

import pytest

from agent.interaction.transcript import (
    MAX_PRIOR_CONTENT,
    MAX_PRIOR_MESSAGE_LENGTH,
    MAX_PRIOR_PAIRS,
    bounded_prior_pairs,
    provider_message_projection,
    snapshot_visible_messages,
    validate_transcript_messages,
)
from agent.llm.session import ChatSession

from ._helpers import FakeGateway


def test_transcript_invariant_requires_first_system_and_string_fields() -> None:
    valid = [{"role": "system", "content": "s", "metadata": {"x": 1}}]
    validate_transcript_messages(valid)
    for invalid in (
        [],
        [{"role": "assistant", "content": "x"}],
        [{"role": "system"}],
        [{"role": "system", "content": 1}],
        [{"role": "system", "content": "s"}, {"role": "user", "content": 2}],
    ):
        with pytest.raises((TypeError, ValueError)):
            validate_transcript_messages(invalid)


def test_prior_context_is_whole_pair_bounded_and_preserves_extra_roles_in_storage() -> None:
    messages = [{"role": "system", "content": "s"}]
    for index in range(7):
        messages.extend(
            [
                {"role": "user", "content": f"u{index}"},
                {"role": "assistant", "content": f"a{index}"},
            ]
        )
    messages.append({"role": "tool", "content": "ignored"})
    pairs = bounded_prior_pairs(messages)
    assert len(pairs) == MAX_PRIOR_PAIRS
    assert sum(len(item["content"]) for pair in pairs for item in pair) <= MAX_PRIOR_CONTENT
    assert all(len(item["content"]) <= MAX_PRIOR_MESSAGE_LENGTH for pair in pairs for item in pair)
    projected = provider_message_projection(messages, current_user="current")
    assert [item["role"] for item in projected] == [
        "system",
        "user", "assistant",
        "user", "assistant",
        "user", "assistant",
        "user", "assistant",
        "user",
    ]


def test_snapshot_is_deep_enough_and_loaded_history_validates_before_assignment(tmp_path) -> None:
    session = ChatSession("system", {}, gateway=FakeGateway([]))
    original = [dict(item) for item in session.messages]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps([{ "role": "assistant", "content": "bad" }]), encoding="utf-8")
    success, error = session.load_from_file(str(invalid))
    assert success is False
    assert error
    assert session.messages == original

    valid = tmp_path / "valid.json"
    stored = [
        {"role": "system", "content": "new", "extra": {"preserve": True}},
        {"role": "tool", "content": "tool output"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "function", "content": "fn"},
        {"role": "system", "content": "later"},
    ]
    valid.write_text(json.dumps(stored), encoding="utf-8")
    success, error = session.load_from_file(str(valid))
    assert success is True
    assert error == ""
    assert session.messages == stored
    snapshot = snapshot_visible_messages(session.messages)
    session.messages[0]["content"] = "changed"
    assert snapshot[0]["content"] == "new"
    projected = provider_message_projection(session.messages, current_user="new turn")
    assert projected == [
        {"role": "system", "content": "changed"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "new turn"},
    ]
