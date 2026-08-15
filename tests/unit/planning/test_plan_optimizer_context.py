import pytest

from agent.planning.plan_optimizer import PlanningOptimizationError, PlanOptimizer
from agent.planning.planning_context import PlanningContextSnapshot, PlanningTool
from agent.tools.contracts import ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


def test_optimizer_uses_context_metadata_instead_of_static_catalog() -> None:
    context = PlanningContextSnapshot(
        snapshot_id="ctx-1",
        registry_identity="registry-1",
        authority_identity="authority-1",
        tools=(
            PlanningTool(
                name="custom_read",
                description="custom",
                category="READ",
                cost=17,
                cacheable=True,
                required_capabilities=frozenset({"read"}),
                origin_kind=ToolOriginKind.BUILTIN,
            ),
        ),
        eligible_names=frozenset({"custom_read"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    report = PlanOptimizer(planning_context=context).optimize(
        [
            {"tool": "custom_read", "args": {}},
            {"tool": "custom_read", "args": {}},
        ]
    )
    assert report.removed_duplicates == 1
    assert report.cost_before == 34
    assert report.cost_after == 17


def test_optimizer_rejects_unknown_tool_in_canonical_context() -> None:
    context = PlanningContextSnapshot(
        snapshot_id="ctx-unknown",
        registry_identity="registry-unknown",
        authority_identity="authority-unknown",
        tools=(PlanningTool(name="known", description="known"),),
        eligible_names=frozenset({"known"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-unknown", "workspace"),
    )
    with pytest.raises(PlanningOptimizationError):
        PlanOptimizer(planning_context=context).optimize([{"tool": "invented", "args": {}}])


def test_optimizer_preserves_deferred_producer_until_dedup_is_dependency_aware() -> None:
    plan = [
        {
            "tool": "file_reader",
            "args": {"file_path": "controle.txt"},
            "_step_id": "read-a",
        },
        {
            "tool": "file_reader",
            "args": {"file_path": "controle.txt"},
            "_step_id": "read-b",
        },
        {
            "kind": "deferred_condition",
            "_step_id": "condition",
            "observation_ref": "read-b",
            "predicate": {"op": "equals", "value": "original"},
            "on_true": {"tool": "echo", "args": {}},
            "on_false": {"waive_effect": "write"},
        },
    ]

    report = PlanOptimizer().optimize(plan)

    assert report.optimized_steps == plan
    assert report.removed_duplicates == 0
    assert report.changed is False
