"""Retain W19 rejection cases when declarations live in the shared F1 owner."""
from pathlib import Path

import pytest

from scripts import check_wave19_architecture as checker

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = "agent/interfaces/cli/action_registry.py"
COMMANDS = "agent/interactive/commands.py"
ADAPTER = '''from llm_agent.interactive import commands as _core
from llm_agent.interactive.commands import CliActionBinding
class CliActionRegistry(_core.CliActionRegistry): pass
DEFAULT_CLI_ACTION_REGISTRY = CliActionRegistry(_core.DEFAULT_ACTION_CATALOG, _core.DEFAULT_CLI_ACTION_REGISTRY._bindings)
'''
OWNER = '''class CliActionRegistry: pass
def _default_bindings():
    return (c("memory.show", "/memory show", ("/memory", "/memoria")), c("inspection.open", "/open", ()))
'''


@pytest.mark.parametrize(
    "adapter,owner,expected",
    [
        (ADAPTER, OWNER, []),
        (OWNER, "", []),
        (ADAPTER.replace("_core", "shared"), OWNER, []),
        (ADAPTER, "", ["W19-S03-003", "W19-S03-004", "W19-S03-005", "W19-S08-005"]),
        (ADAPTER.replace("_core.DEFAULT_CLI_ACTION_REGISTRY._bindings", "()"), OWNER,
         ["W19-S03-003", "W19-S03-004", "W19-S08-005"]),
        (ADAPTER.replace("import commands", "import other"), OWNER,
         ["W19-S03-003", "W19-S03-004", "W19-S08-005"]),
        (ADAPTER, OWNER.replace("/memory show", "/other"), ["W19-S03-003"]),
        (ADAPTER, OWNER.replace('"/memoria"', '"/other"'), ["W19-S03-004"]),
        (ADAPTER, OWNER + "\nclass CliActionRegistry: pass\n", ["W19-S03-005"]),
        (ADAPTER, OWNER.replace("inspection.open", "other.action"), ["W19-S08-005"]),
        (ADAPTER, OWNER + '\nretired="/output"\n', ["W19-S08-005", "W19-S03-007"]),
        (ADAPTER + '\nretired="/output"\n', OWNER, ["W19-S08-005", "W19-S03-007"]),
        (ADAPTER, OWNER + '\nretired="/output show"\n', ["W19-S03-007"]),
        (ADAPTER, "# memory.show /memory show /memory /memoria inspection.open",
         ["W19-S03-003", "W19-S03-004", "W19-S03-005", "W19-S08-005"]),
    ],
    ids=["shared", "legacy", "renamed-alias", "absent-owner", "absent-delegation", "wrong-import",
         "wrong-path", "missing-alias", "duplicate-owner", "missing-inspection", "retired-in-owner",
         "retired-in-adapter", "retired-family", "comments-are-not-declarations"],
)
def test_registry_rules_follow_delegation_without_waiving_rejections(monkeypatch, adapter, owner, expected):
    monkeypatch.setattr(checker, "_read", lambda _root, path: owner if path == COMMANDS else adapter)
    findings = (checker._check_action_registry_contract(ROOT)
                + checker._check_output_action_unity(ROOT)
                + checker._check_interaction_output_namespace(ROOT))
    assert [item.rule_id for item in findings] == expected


@pytest.mark.parametrize("canonical", [True, False])
def test_parser_must_still_consume_registry_in_actual_owner(monkeypatch, canonical):
    adapter = ("from llm_agent.interactive import action_parser as _core\n"
               "from llm_agent.interactive.action_parser import parse_action\n")
    owner = "from llm_agent.interactive.commands import CliActionRegistry\n" if canonical else ""
    sources = {"agent/interfaces/cli/action_parser.py": adapter,
               "agent/interactive/action_parser.py": owner,
               "agent/interfaces/cli/action_completion.py": "from registry import CliActionRegistry\n"}
    monkeypatch.setattr(checker, "_read", lambda _root, path: sources.get(path, ""))
    assert [item.rule_id for item in checker._check_cli_action_projections(ROOT)] == (
        [] if canonical else ["W19-S03-006"]
    )


@pytest.mark.parametrize("canonical", [True, False])
def test_query_owner_must_still_import_application_query_contracts(monkeypatch, canonical):
    relative = "agent/interfaces/cli/query_executor.py"
    adapter = ("from llm_agent.interactive import query_executor as _core\n"
               "from llm_agent.interactive.query_executor import CliQuerySubmission\n")
    owner = "from llm_agent.application.services.queries import WorkspaceQueryRequest\n" if canonical else ""
    sources = {relative: adapter, "agent/interactive/query_executor.py": owner}
    monkeypatch.setattr(checker, "_read", lambda _root, path: sources.get(path, ""))
    findings = checker._check_application_surface_file(ROOT, ROOT / "src/llm_agent/interfaces/cli/query_executor.py")
    assert [item.rule_id for item in findings] == ([] if canonical else ["W19-S06-005"])
