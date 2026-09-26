"""Focused tests for the bounded Wave 21 structural bridge core."""

from __future__ import annotations

import ast

import pytest

from scripts.w21_architecture.structural import (
    StructuralStatus,
    analyze_symbol_bridge,
    collect_binding_events,
)

TARGET_MODULE = "agent.operation.spec"
TARGET_SYMBOL = "SkillSpec"
SOURCE_SYMBOL = "SkillSpec"


def analyze(source: str):
    return analyze_symbol_bridge(
        ast.parse(source),
        SOURCE_SYMBOL,
        target_module=TARGET_MODULE,
        target_symbol=TARGET_SYMBOL,
    )


@pytest.mark.parametrize(
    "source",
    (
        "from agent.operation.spec import SkillSpec\n",
        "from agent.operation.spec import SkillSpec as SkillSpec\n",
    ),
)
def test_exact_absolute_importfrom_is_the_only_exact_form(source: str) -> None:
    result = analyze(source)
    assert result.status is StructuralStatus.EXACT
    assert result.is_exact_target(TARGET_MODULE, TARGET_SYMBOL)
    assert len(result.events) == 1
    assert result.events[0].authorized


@pytest.mark.parametrize(
    "source",
    (
        "from agent.operation.catalog import SkillSpec\n",
        "from agent.operation.spec import Other as SkillSpec\n",
        "from agent.skills.facade import SkillSpec\n",
        "from .operation.spec import SkillSpec\n",
        "from ..operation.spec import SkillSpec\n",
        "from agent.operation.spec import *\n",
    ),
)
def test_wrong_owner_symbol_relative_and_wildcard_forms_fail_closed(source: str) -> None:
    assert analyze(source).status is StructuralStatus.INVALID


@pytest.mark.parametrize(
    "source",
    (
        "import agent.operation.spec as SkillSpec\n",
        "from agent.operation import spec\nSkillSpec = spec.SkillSpec\n",
        "import agent.operation.spec as spec\nSkillSpec = spec.SkillSpec\n",
        "from agent.operation.spec import SkillSpec as exact\nSkillSpec = exact\n",
        "SkillSpec = object()\n",
        "SkillSpec: object\n",
        "SkillSpec += value\n",
        "del SkillSpec\n",
        "def SkillSpec():\n    pass\n",
        "async def SkillSpec():\n    pass\n",
        "class SkillSpec:\n    pass\n",
        "for SkillSpec in values:\n    pass\n",
        "with context() as SkillSpec:\n    pass\n",
        "try:\n    pass\nexcept Exception as SkillSpec:\n    pass\n",
        "match value:\n    case SkillSpec:\n        pass\n",
        "value = (SkillSpec := object())\n",
    ),
)
def test_non_importfrom_direct_binders_are_not_authorized(source: str) -> None:
    result = analyze(source)
    assert result.status is StructuralStatus.INVALID
    assert result.events
    assert not any(event.authorized for event in result.events)


@pytest.mark.parametrize(
    "source",
    (
        "from agent.operation.spec import SkillSpec\nSkillSpec = replacement\n",
        "SkillSpec = replacement\nfrom agent.operation.spec import SkillSpec\n",
        "from agent.operation.spec import SkillSpec\n"
        "if enabled:\n"
        "    from agent.operation.spec import SkillSpec\n",
        "from agent.operation.spec import SkillSpec\n"
        "from agent.operation.catalog import SkillSpec\n",
        "from agent.operation.spec import SkillSpec\n"
        "def wrapper():\n"
        "    pass\n"
        "class SkillSpec:\n"
        "    pass\n",
    ),
)
def test_authorized_import_with_competing_module_binding_is_ambiguous(source: str) -> None:
    result = analyze(source)
    assert result.status is StructuralStatus.AMBIGUOUS
    assert result.is_exact_target(TARGET_MODULE, TARGET_SYMBOL) is False
    assert sum(event.authorized for event in result.events) >= 1
    assert len(result.events) > 1


def test_module_control_flow_is_scanned_without_reachability_evaluation() -> None:
    source = (
        "if FLAG:\n"
        "    from agent.operation.spec import SkillSpec\n"
        "else:\n"
        "    from agent.operation.catalog import SkillSpec\n"
    )
    result = analyze(source)
    assert result.status is StructuralStatus.AMBIGUOUS
    assert [event.kind for event in result.events] == ["import_from", "import_from"]


def test_function_and_class_local_bodies_are_not_module_bindings() -> None:
    source = (
        "from agent.operation.spec import SkillSpec\n"
        "def function():\n"
        "    SkillSpec = replacement\n"
        "class Container:\n"
        "    SkillSpec = replacement\n"
    )
    result = analyze(source)
    assert result.status is StructuralStatus.EXACT
    assert len(result.events) == 1


def test_comprehension_targets_are_local_but_walrus_is_a_direct_event() -> None:
    exact = analyze(
        "from agent.operation.spec import SkillSpec\n"
        "values = [SkillSpec for SkillSpec in items]\n"
    )
    assert exact.status is StructuralStatus.EXACT

    competing = analyze(
        "from agent.operation.spec import SkillSpec\n"
        "values = [(SkillSpec := item) for item in items]\n"
    )
    assert competing.status is StructuralStatus.AMBIGUOUS


def test_event_collection_preserves_direct_binder_kinds_and_lines() -> None:
    source = (
        "if FLAG:\n"
        "    SkillSpec = replacement\n"
        "for SkillSpec in values:\n"
        "    pass\n"
    )
    events = collect_binding_events(
        ast.parse(source),
        SOURCE_SYMBOL,
        target_module=TARGET_MODULE,
        target_symbol=TARGET_SYMBOL,
    )
    assert [(event.kind, event.line) for event in events] == [
        ("assignment", 2),
        ("for_target", 3),
    ]
