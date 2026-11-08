import ast
from pathlib import Path

import pytest

from scripts.check_current_architecture import _task_execution_dto_errors


def source():
    return (Path(__file__).resolve().parents[3] / "src/llm_agent/application/task_execution.py").read_text(encoding="utf-8")


def errors(text):
    tree = ast.parse(text)
    return _task_execution_dto_errors({node.name: node for node in tree.body if isinstance(node, ast.ClassDef)})


def test_start_plugin_scope_is_closed_immutable_and_keyword_only():
    assert errors(source()) == []


@pytest.mark.parametrize(("before", "after"), [
    ("startup_extension_ids: tuple[str, ...] | None", "startup_extension_ids: dict[str, Any] | None"),
    ("startup_extension_fingerprints: tuple[tuple[str, str], ...] | None", "startup_extension_fingerprints: Any"),
    ("startup_extension_ids: tuple[str, ...] | None = field(default=None, kw_only=True)", "startup_extension_ids: tuple[str, ...] | None = field(default=None, kw_only=False)"),
    ("startup_extension_ids: tuple[str, ...] | None = field(default=None, kw_only=True)", "startup_extension_ids: tuple[str, ...] | None = field(default=('unreviewed',), kw_only=True)"),
])
def test_start_plugin_scope_rejects_unbounded_or_implicitly_granted_inputs(before, after):
    assert before in source()
    assert any("plugin startup scope" in error for error in errors(source().replace(before, after)))
