from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.llm import context_manager as context_manager_module
from agent.llm.context_manager import ContextManager
from agent.llm.context_views import get_file_hints


class _Memory:
    state = {}

    @staticmethod
    def stringify():
        return ""


def _context_state():
    return SimpleNamespace(
        memory=_Memory(),
        tool_history=[],
        conversation_history=[],
        max_history_turns=5,
    )


def _session():
    gateway = MagicMock()
    return SimpleNamespace(
        config={"hardware_profile": "low_vram_8gb"},
        gateway=gateway,
        messages=[{"role": "system", "content": "system"}],
    )


def test_context_manager_propagates_explicit_workspace_root(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        context_manager_module,
        "SemanticMemory",
        lambda memory: SimpleNamespace(find_similar_files=lambda *_args, **_kwargs: []),
    )

    def fake_discover(root):
        captured["root"] = root
        return "context"

    monkeypatch.setattr(context_manager_module, "discover_project_context", fake_discover)
    manager = ContextManager(_session(), _context_state(), workspace_root=tmp_path)

    assert manager.get_project_context() == "context"
    assert captured["root"] == tmp_path.resolve()


def test_file_hints_use_explicit_root_and_reject_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text("one\ntwo\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    semantic = SimpleNamespace(
        find_similar_files=lambda *_args, **_kwargs: ["../outside.py"]
    )

    hints = get_file_hints("analise sample.py", semantic, workspace)

    assert "sample.py (2 linhas)" in hints
    assert "outside.py" not in hints
