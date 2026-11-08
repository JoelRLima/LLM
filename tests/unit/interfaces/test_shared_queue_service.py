"""Shared application authority and presentation-only terminal adapters."""
from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from llm_agent.application import test_queue_runtime as runtime
from llm_agent.application import workspace_preferences
from llm_agent.application.test_queue_service import (
    QueueExecutionRequest,
    TestQueueService,
    assess_queue,
)
from llm_agent.interfaces.cli import workspace_entry
from tests.unit.interfaces.test_test_queue import make_queue, successful

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("authorized", [False, None, 1, "yes"])
def test_shared_use_case_enforces_live_authorization_before_config_or_effects(tmp_path, monkeypatch, authorized):
    service = TestQueueService(tmp_path / "absent")
    monkeypatch.setattr(runtime, "prepare_config", lambda *a: pytest.fail("authorization bypassed"))
    with pytest.raises(PermissionError, match="authorization"):
        service.run(QueueExecutionRequest(tmp_path / "missing.json", live_authorized=authorized))
    assert not service.root.exists()


@pytest.mark.parametrize("field,value", [("automatic_approval", "yes"), ("skip_interrupted", 1),
                                         ("case_seconds", False), ("total_seconds", 0)])
def test_shared_service_validates_permissions_and_limits_without_ui(tmp_path, monkeypatch, field, value):
    service = TestQueueService(tmp_path / "absent")
    monkeypatch.setattr(runtime, "prepare_config", lambda *a: pytest.fail("invalid request reached configuration"))
    request = replace(QueueExecutionRequest(tmp_path / "unused.json", live_authorized=True), **{field: value})
    with pytest.raises(ValueError):
        service.run(request)
    assert not service.root.exists()


def test_headless_service_executes_and_grades_without_terminal_adapter(tmp_path, monkeypatch):
    queue = make_queue(tmp_path)
    monkeypatch.setattr(runtime, "prepare_config", lambda *a: {})
    monkeypatch.setattr(runtime, "runtime_identity", lambda *a, **k: "offline-service")
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: successful)
    service = TestQueueService(queue.root)
    events = []
    result = service.run(QueueExecutionRequest(tmp_path / "unused.json", live_authorized=True), on_progress=events.append)
    assert result.assessment == "verified"
    assert result.document["counts"] == {"verified": 3}
    assert len(events) == 6
    assert service.details("01-first")["verification"]["status"] == "passed"


@pytest.mark.parametrize("status,counts,expected", [
    ("completed", {"verified": 3}, "verified"),
    ("completed", {"verified": 2, "failed": 1}, "failed"),
    ("completed", {"verified": 2, "unverified": 1}, "incomplete"),
    ("completed", {"verified": 2, "interrupted": 1}, "incomplete"),
    ("paused", {"verified": 1, "pending": 2}, "incomplete"),
    ("completed", {"verified": 2, "timeout": 1}, "failed"),
])
def test_application_owns_outcome_independent_of_exit_codes(status, counts, expected):
    assert assess_queue({"status": status, "counts": counts, "cases": [{}, {}, {}]}) == expected


def test_profile_requirement_is_enforced_by_shared_service(tmp_path):
    service = TestQueueService(tmp_path / "unused")
    with pytest.raises(ValueError, match="requires --config"):
        service.check(profile="example")


def test_workspace_compatibility_exports_have_same_application_identity():
    assert workspace_entry.load_last_workspace is workspace_preferences.load_last_workspace
    assert workspace_entry.remember_workspace is workspace_preferences.remember_workspace


def _assert_interfaces_import_only_the_shared_queue_service() -> None:
    for path in (ROOT / "src/llm_agent/interfaces").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("llm_agent.application.test_queue"):
                if node.module == "llm_agent.application.test_queue":
                    assert {alias.name for alias in node.names} <= {"QueueProgress"}, path
                else:
                    assert node.module == "llm_agent.application.test_queue_service", path
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names if alias.name.startswith("llm_agent.application.test_queue")]
                assert all(name == "llm_agent.application.test_queue_service" for name in imported), path


def test_queue_cli_cannot_compose_runtime_or_classify_test_results():
    source = (ROOT / "src/llm_agent/interfaces/test_queue.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "llm_agent.application.test_queue_service" in imports
    assert not imports.intersection({"llm_agent.application.test_queue", "llm_agent.application.test_queue_runtime",
                                     "llm_agent.application.test_queue_inspection"})
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection({"TestQueue", "RuntimeExecutor", "prepare_config", "runtime_identity", "assess_queue"})
    classified_fields = {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"state", "document"}
        and isinstance(node.slice, ast.Constant)
    }
    assert not classified_fields.intersection({"counts", "results", "order", "status"})
    _assert_interfaces_import_only_the_shared_queue_service()


def test_shared_services_import_without_any_interface(tmp_path):
    code = """
import builtins,sys
sys.path.insert(0,sys.argv[1])
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('llm_agent.interfaces','rich','prompt_toolkit','webview','tkinter')):
        raise AssertionError('shared service imported UI: '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from llm_agent.application.test_queue_service import TestQueueService,QueueExecutionRequest
from llm_agent.application.workspace_preferences import load_last_workspace
assert not any(n.startswith('llm_agent.interfaces') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "src")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
