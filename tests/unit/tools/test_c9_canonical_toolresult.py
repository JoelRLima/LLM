"""C9 regressions for canonical ToolResult ownership and legacy edges."""

import ast
from pathlib import Path

import pytest

from agent.state import AgentState
from agent.tools.contracts import ToolResult, ToolStatus

# These are the only production modules allowed to mention the historical
# result shape.  ``contracts.py`` owns the compatibility type name,
# ``result_adapter.py`` is the explicit conversion edge, and
# ``state_checkpointing.py`` projects the supported on-disk checkpoint schema.
LEGACY_BOUNDARY_ALLOWLIST = frozenset(
    {
        "agent/contracts.py",
        "agent/state_checkpointing.py",
        "agent/tools/result_adapter.py",
    }
)
FORBIDDEN_LEGACY_NAMES = ("LegacyToolResult", "to_legacy_result", "from_legacy_result")


def _legacy_boundary_violations(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    production = root / "agent"
    assert production.is_dir(), f"production root does not exist: {production}"
    scanned: list[str] = []
    violations: list[str] = []
    for path in sorted(production.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        scanned.append(relative)
        if relative in LEGACY_BOUNDARY_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        violations.extend(_legacy_text_violations(relative, text))
        tree = ast.parse(text, filename=relative)
        aliases = _contracts_aliases(tree)
        violations.extend(_legacy_ast_violations(relative, tree, aliases))
    return tuple(scanned), tuple(violations)


def _legacy_text_violations(relative: str, text: str) -> tuple[str, ...]:
    return tuple(
        f"{relative}: {name}"
        for name in FORBIDDEN_LEGACY_NAMES
        if name in text
    )


def _contracts_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname or "agent.contracts"
                for alias in node.names
                if alias.name == "agent.contracts"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "agent":
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "contracts"
            )
    return aliases


def _legacy_ast_violations(
    relative: str, tree: ast.AST, contracts_aliases: set[str]
) -> tuple[str, ...]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agent.contracts":
            if any(alias.name == "ToolResult" for alias in node.names):
                violations.append(f"{relative}: compatibility ToolResult import")
        if isinstance(node, ast.Attribute) and node.attr == "ToolResult":
            qualified = _attribute_name(node.value)
            if qualified == "agent.contracts" or qualified in contracts_aliases:
                violations.append(f"{relative}: qualified compatibility ToolResult")
    return tuple(violations)


def _attribute_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attribute_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def test_legacy_result_names_are_confined_to_documented_edges() -> None:
    root = Path(__file__).resolve().parents[3]
    scanned, violations = _legacy_boundary_violations(root)

    assert scanned, "C9 guard scanned zero production files"
    assert "agent/tool_executor.py" in scanned
    assert "agent/planning/step_executor.py" in scanned
    assert violations == (), "legacy result conversion leaked into core: " + "; ".join(violations)


def test_static_guard_rejects_compatibility_import_in_core_sentinel(tmp_path: Path) -> None:
    sentinel = tmp_path / "agent" / "tool_executor.py"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("from agent.contracts import ToolResult\n", encoding="utf-8")

    scanned, violations = _legacy_boundary_violations(tmp_path)

    assert scanned == ("agent/tool_executor.py",)
    assert violations == ("agent/tool_executor.py: compatibility ToolResult import",)


@pytest.mark.parametrize(
    "source",
    (
        "import agent\nagent.contracts.ToolResult\n",
        "import agent.contracts as contracts\ncontracts.ToolResult\n",
        "from agent import contracts\ncontracts.ToolResult\n",
        "from agent import contracts as legacy_contracts\nlegacy_contracts.ToolResult\n",
    ),
)
def test_static_guard_rejects_qualified_and_alias_access_sentinels(
    tmp_path: Path,
    source: str,
) -> None:
    sentinel = tmp_path / "agent" / "tool_executor.py"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text(source, encoding="utf-8")

    scanned, violations = _legacy_boundary_violations(tmp_path)

    assert scanned == ("agent/tool_executor.py",)
    assert violations == (
        "agent/tool_executor.py: qualified compatibility ToolResult",
    )


def test_live_history_is_canonical_and_checkpoint_projection_is_explicit() -> None:
    state = AgentState()
    result = ToolResult(
        invocation_id="c9-success",
        status=ToolStatus.SUCCEEDED,
        data={"value": "ok"},
        executed=True,
    )
    state.record_tool_result("file_reader", {"file_path": "foo.txt"}, result)

    assert state.last_result is result
    assert isinstance(state.tool_history[0]["result"], ToolResult)
    assert state.last_result["status"] == ToolStatus.SUCCEEDED.value

    checkpoint = state.to_checkpoint_dict()
    assert isinstance(checkpoint["last_result"], dict)
    assert isinstance(checkpoint["tool_history"][0]["result"], dict)

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    assert isinstance(restored.last_result, ToolResult)
    assert isinstance(restored.tool_history[0]["result"], ToolResult)
    assert restored.last_result.status is ToolStatus.SUCCEEDED
