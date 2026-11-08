"""Preserve W11 /read requirements when the registry delegates to F1 core."""
import ast
from pathlib import Path

import pytest

from scripts import check_wave11_architecture as checker

REGISTRY = "agent/interfaces/cli/action_registry.py"
ADAPTER = '''from llm_agent.interactive import commands as _core
from llm_agent.interactive.commands import CliActionBinding
DEFAULT_CLI_ACTION_REGISTRY = CliActionRegistry(_core.DEFAULT_ACTION_CATALOG, _core.DEFAULT_CLI_ACTION_REGISTRY._bindings)
'''
OWNER = 'def _default_bindings(): return (binding("read", "/read", ()),)\n'


@pytest.mark.parametrize(
    "adapter,owner,handler,expected_violation",
    [
        (ADAPTER, OWNER, True, False),
        (OWNER, "", True, False),
        (ADAPTER.replace("_core", "shared"), OWNER, True, False),
        (ADAPTER, OWNER.replace("/read", "/other"), True, True),
        (ADAPTER, "", True, True),
        (ADAPTER.replace("import commands", "import unrelated"), OWNER, True, True),
        (ADAPTER.replace("from llm_agent.interactive.commands import CliActionBinding", ""), OWNER, True, True),
        (ADAPTER.replace("_core.DEFAULT_CLI_ACTION_REGISTRY._bindings", "()"), OWNER, True, True),
        (ADAPTER, OWNER, False, True),
        (ADAPTER, "# /read", True, True),
    ],
    ids=["shared", "legacy", "renamed-alias", "missing-path", "missing-owner", "wrong-import",
         "missing-direct-import", "missing-delegation", "missing-handler", "comments-are-not-registration"],
)
def test_read_registration_and_handler_remain_required(monkeypatch, adapter, owner, handler, expected_violation):
    sources = {
        REGISTRY: adapter,
        "agent/interactive/commands.py": owner,
        "agent/interfaces/cli/command_handlers.py": "def read_file(text, ctx): pass" if handler else "",
    }
    monkeypatch.setattr(checker, "_tree", lambda root, relative: ast.parse(sources.get(relative, "")))
    findings = checker._check_s5_interactive_read(Path.cwd())
    assert bool(findings) == expected_violation
    assert all(finding.rule_id == "W11-S5" for finding in findings)
