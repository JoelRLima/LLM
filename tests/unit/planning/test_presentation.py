import pytest

from agent.planning.planning_context import PlanningContextError, PlanningTool
from agent.planning.presentation import PlanningPresentationError, PlanningPresentationSnapshot
from agent.tools.contracts import ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


def _view(*, planner_kind: str = "reactive") -> PlanningPresentationSnapshot:
    tool = PlanningTool(
        name="safe_tool",
        description="descrição com <instrução> & conteúdo",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        required_capabilities=frozenset({"read"}),
        category="READ",
        cost=2,
    )
    return PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind=planner_kind,
        tools=(tool,),
        presented_names=frozenset({"safe_tool"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )


def test_compact_render_is_a_framed_escaped_level_zero_index() -> None:
    rendered = _view().render(compact=True)
    assert rendered.startswith("Os dados seguintes")
    assert "<untrusted_tool_catalog>" in rendered
    assert "\\u003c" in rendered and "\\u0026" in rendered
    assert '"name":"safe_tool"' in rendered
    assert '"purpose":"descri' in rendered
    assert '"schema"' not in rendered
    assert "<instrução>" not in rendered


def test_hierarchical_compact_view_may_omit_schema() -> None:
    rendered = _view(planner_kind="hierarchical").render(compact=True)
    assert '"name":"safe_tool"' in rendered
    assert '"schema"' not in rendered


def test_result_data_schema_is_rendered_as_bounded_structure() -> None:
    tool = PlanningTool(
        name="shaped",
        description="shaped",
        result_data_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
            },
        },
    )
    view = PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind="linear",
        tools=(tool,),
        presented_names=frozenset({"shaped"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )

    rendered = view.render_detailed()

    assert '"result_data_schema":{"items":{"properties":{"content":{"type":"string"}' in rendered


def test_catalog_overflow_fails_closed_without_omitting_tools() -> None:
    tool = PlanningTool(name="x", description="x" * 2_001)
    view = PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind="linear",
        tools=(tool,),
        presented_names=frozenset({"x"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    with pytest.raises(PlanningPresentationError):
        view.render()


def test_extension_payload_contains_only_canonical_identity_fields() -> None:
    tool = PlanningTool(
        name="external",
        description="external",
        origin_kind=ToolOriginKind.EXTENSION,
        extension_id="scanner.extension",
    )
    view = PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind="linear",
        tools=(tool,),
        presented_names=frozenset({"external"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    rendered = view.render()
    assert '"origin":"extension"' in rendered
    assert '"extension_id":"scanner.extension"' in rendered


def test_budget_measures_final_escaped_representation() -> None:
    tools = tuple(
        PlanningTool(name=f"hostile_{index}", description="<" * 2_000)
        for index in range(6)
    )
    view = PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind="linear",
        tools=tools,
        presented_names=frozenset(tool.name for tool in tools),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    with pytest.raises(PlanningPresentationError):
        view.render()


def test_total_budget_rejects_post_escape_overflow_after_tool_budget() -> None:
    tools = tuple(
        PlanningTool(name=f"catalog_{index}", description="<" * 1_000)
        for index in range(6)
    )
    view = PlanningPresentationSnapshot(
        planning_context_id="ctx-1",
        planner_kind="linear",
        tools=tools,
        presented_names=frozenset(tool.name for tool in tools),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    with pytest.raises(PlanningPresentationError, match="budget"):
        view.render()


def test_deep_schema_fails_with_typed_error() -> None:
    schema = {}
    current = schema
    for _ in range(1_200):
        current["nested"] = {}
        current = current["nested"]
    with pytest.raises(PlanningContextError):
        PlanningTool(name="deep", description="deep", input_schema=schema)


def test_cyclic_schema_fails_with_typed_error() -> None:
    schema = {}
    schema["self"] = schema
    with pytest.raises(PlanningContextError):
        PlanningTool(name="cycle", description="cycle", input_schema=schema)
