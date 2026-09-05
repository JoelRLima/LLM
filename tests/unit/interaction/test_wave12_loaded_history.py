from __future__ import annotations

import json

from agent.llm.session import ChatSession

from ._helpers import FakeGateway


def test_invalid_loaded_history_does_not_replace_previous_value(tmp_path) -> None:
    session = ChatSession("system", {}, gateway=FakeGateway([]))
    before = [dict(item) for item in session.messages]
    path = tmp_path / "history.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    success, _error = session.load_from_file(str(path))
    assert success is False
    assert session.messages == before
