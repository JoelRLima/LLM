"""Integration-grade LH15-13..LH15-16 scenario routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.cancellation import CancellationToken
from agent.evaluation.long_horizon_production import (
    ProductionGateway,
    RecordingEventSink,
    RecordingMetricsSink,
    event_types,
    real_plan_run,
    result,
)
from agent.planning.observation_receipts import ObservationClassification
from agent.planning.plan_model import Plan
from agent.planning.plan_step_types import ToolPlanStep
from agent.planning.progress_receipt import build_progress_receipt, compare_progress_receipts
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.runtime.context import (
    RuntimeLimits,
    TaskExecutionContext,
    TaskResult,
    TaskStatus,
)
from agent.runtime.convergence import ConvergenceStateV1
from agent.runtime.convergence_runtime import (
    current_progress_receipt,
    observe_convergence_attempt,
)


def lh13() -> dict[str, Any]:
    run = real_plan_run(file_count=3, seed_lossy_rehydration=True, replay_completed=True)
    steps, history = run["plan"], run["history"]
    event_names = run["event_names"]
    logical_ids = tuple(step.step_id for step in steps)
    finalized_ids = tuple(
        entry.get("step_id")
        for entry in history
        if isinstance(entry, Mapping) and entry.get("step_id") is not None
    )
    cache_count = run["classifications"].count(ObservationClassification.CACHE_REUSE.value)
    rehydration_count = event_names.count("observation_rehydration")
    physical_count = sum(
        int(getattr(entry.get("result"), "executed", None) is True)
        for entry in history
        if isinstance(entry, Mapping)
    )
    progress_count = event_names.count("progress_receipt_advanced")
    return result(
        "LH15-13",
        "Parallel logical finalization",
        {
            "actual_plan_executor_route": len(steps) == 3,
            "logical_order_is_deterministic": finalized_ids == logical_ids,
            "each_slot_appears_once": len(finalized_ids) == len(set(finalized_ids)) == len(logical_ids),
            "cache_reuse_is_measured": cache_count == 1,
            "slot_classifications_match_frozen_order": run["classifications"] == ("CACHE_REUSE", "NEW_EVIDENCE", "CONTEXT_REHYDRATION"),
            "physical_slots_are_measured": physical_count == len(steps) - cache_count,
            "physical_rehydration_is_measured": rehydration_count == 1,
            "logical_slots_finalized_equals_three": len(finalized_ids) == 3,
            "cache_reuse_charged_cycles_equals_zero": event_names.count("progress_plateau_cycle") == physical_count,
            "physical_charged_cycles_equals_two": run["state"].convergence.cycles_since_progress == 2,
            "new_canonical_progress_from_physical_slots_equals_zero": progress_count == 0,
            "duplicate_worker_finalizer_accounting_equals_zero": event_names.count("progress_plateau_cycle") == 2,
            "convergence_owner_remains_live": isinstance(run["state"].convergence, ConvergenceStateV1),
        },
        logical_units=len(steps),
        model_calls=run["budget"].model_calls,
        tool_calls=run["budget"].tool_calls,
        progress_advances=progress_count,
        no_progress_cycles=run["state"].convergence.cycles_since_progress,
        cache_reuse=cache_count,
        rehydration=rehydration_count,
    )


def lh14() -> dict[str, Any]:
    events, metrics = RecordingEventSink(), RecordingMetricsSink()
    root = TaskExecutionContext(
        model_gateway=ProductionGateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_steps=6, max_model_calls=6, max_task_tool_calls=6, max_no_progress_plateau=6),
        event_sink=events,
        metrics_sink=metrics,
        permissions=frozenset(),
    )
    graph = TaskGraph(
        "Run the graph",
        (
            TaskNode("root-a", "first graph node"),
            TaskNode("root-b", "second graph node"),
            TaskNode("child-c", "third graph node"),
        ),
    )

    class Executor:
        def __init__(self) -> None:
            self.contexts: list[TaskExecutionContext] = []
            self.delegated_observations: list[Any] = []

        def execute(self, node: TaskNode, context: TaskExecutionContext) -> TaskResult:
            self.contexts.append(context)
            before = current_progress_receipt(context)
            self.delegated_observations.append(
                observe_convergence_attempt(
                    context, before, before, accounting=context.convergence_accounting
                )
            )
            return TaskResult(TaskStatus.UNVERIFIED, summary=node.objective)

    executor = Executor()
    assert root.convergence is not None
    cycles = []
    for _ in range(2):
        TaskGraphScheduler(executor, max_workers=1).execute(graph, root)
        cycles.append(root.convergence.cycles_since_progress)
    event_names = event_types(events.events)
    delegated_progress = tuple(
        event
        for event in events.events
        if getattr(getattr(event, "kind", None), "value", None)
        in {"progress_receipt_advanced", "progress_plateau_cycle"}
        and getattr(event, "task_id", None) in {context.task_id for context in executor.contexts}
    )
    return result(
        "LH15-14",
        "TaskGraph root convergence",
        {
            "root_no_progress_cycles_increase": cycles == [3, 6],
            "child_recreation_does_not_reset_root_plateau": len({context.task_id for context in executor.contexts}) == 6,
            "children_share_root_convergence_owner": all(context.convergence is root.convergence for context in executor.contexts),
            "children_are_delegated": all(
                context.convergence_accounting is not None and context.convergence_accounting.delegated
                for context in executor.contexts
            ),
            "child_route_does_not_emit_root_progress": not delegated_progress,
            "root_route_observed_no_progress": "progress_receipt_advanced" not in event_names,
            "terminal_plateau_reached_within_bound": root.convergence.terminal_reached(),
            "delegated_execution_does_not_double_charge": event_names.count("progress_plateau_cycle") == 6,
            "root_accounting_remains_non_delegated": root.convergence_accounting is not None and not root.convergence_accounting.delegated,
            "child_observation_does_not_charge_root": all(item is not None and item.cycle_charged is False for item in executor.delegated_observations),
        },
        logical_units=len(executor.contexts),
        model_calls=root.budget_snapshot().model_calls,
        tool_calls=root.budget_snapshot().tool_calls,
        progress_advances=event_names.count("progress_receipt_advanced"),
        no_progress_cycles=(root.convergence.cycles_since_progress if root.convergence is not None else 0),
    )


def lh15() -> dict[str, Any]:
    run = real_plan_run(file_count=32, seed_lossy_rehydration=False)
    steps, history, state = run["plan"], run["history"], run["state"]
    event_names, budget = run["event_names"], run["budget"]
    cache_count = run["classifications"].count(ObservationClassification.CACHE_REUSE.value)
    completed_ids = tuple(
        step_id
        for step_id, record in state.step_records.items()
        if getattr(record.status, "value", record.status) == "completed"
    )
    all_step_ids = tuple(step.step_id for step in steps)
    pressure_decisions = tuple(
        str(item.get("decision"))
        for item in run["metrics"]
        if item.get("metric_type") == "context_pressure_events" and item.get("decision") is not None
    )
    omitted_milestones = [
        (before, after, fact)
        for before, after in run["milestones"]
        for fact in compare_progress_receipts(before, after).raw_new_credit_fact_ids
        if fact not in after.credit_fact_projection
    ]
    # Continue the same root with two reversible canonical completion facts.
    # Only first B/C completion may reset; subsequent restorations must plateau.
    extra = tuple(ToolPlanStep(f"churn-{name}", "file_reader", {"file_path": f"{name}.txt"}) for name in ("B", "C"))
    state.set_plan(Plan((*steps, *extra)))
    before = build_progress_receipt(state)
    churn = []
    for index in (32, 33, 32, 33, 32, 33, 32, 33):
        state.mark_step_running(32)
        state.mark_step_running(33)
        state.mark_step_completed(index)
        after = build_progress_receipt(state)
        from types import SimpleNamespace
        observation = observe_convergence_attempt(SimpleNamespace(agent_state=state), before, after)
        assert observation is not None
        churn.append(observation)
        before = after
    return result(
        "LH15-15",
        "Long successful task efficiency",
        {
            "actual_long_plan_route_succeeded": len(history) == len(steps),
            "all_logical_units_complete": completed_ids == all_step_ids,
            "no_completed_unit_repeats": len(completed_ids) == len(set(completed_ids)),
            "frontier_consumed_canonical_plan": state.plan_identity is not None,
            "progress_is_measured_from_runtime_events": event_names.count("progress_receipt_advanced") > 0,
            "counters_are_from_live_budget_owner": budget.tool_calls + cache_count == len(history) and budget.model_calls == len(run["gateway"].requests),
            "multiple_context_pressure_events": len(pressure_decisions) >= 2,
            "external_credit_projection_truncated": before.to_dict()["credit_fact_projection"]["truncated"] is True,
            "chosen_internal_milestone_omitted_externally": bool(omitted_milestones),
            "omitted_milestone_advances_complete_internal_receipt": any(compare_progress_receipts(old, new).advanced and fact in new.credit_fact_ids and fact in state.convergence.credited_fact_ids_seen for old, new, fact in omitted_milestones),
            "B_first_seen_reset": churn[0].advanced is True,
            "C_first_seen_reset": churn[1].advanced is True,
            "restored_B_not_advanced": churn[2].advanced is False,
            "restored_C_not_advanced": churn[3].advanced is False,
            "eventual_plateau_terminal_without_third_fact": state.convergence.terminal_reached(),
        },
        logical_units=len(steps),
        model_calls=budget.model_calls,
        tool_calls=budget.tool_calls,
        context_pressure_decisions=pressure_decisions,
        compact_full_events=pressure_decisions,
        progress_advances=event_names.count("progress_receipt_advanced") + sum(item.advanced for item in churn),
        no_progress_cycles=state.convergence.cycles_since_progress,
        cache_reuse=cache_count,
    )


def lh16() -> dict[str, Any]:
    run = real_plan_run(file_count=24, seed_lossy_rehydration=False, no_summary_control=True)
    pressure = tuple(str(item["decision"]) for item in run["metrics"]
                    if item.get("metric_type") == "context_pressure_events" and item.get("decision"))
    history = run["history"]
    return result(
        "LH15-16", "Long no-summary production control",
        {
            "representative_multi_unit_application_route": len(history) == 24,
            "multiple_pressure_decisions": len(pressure) >= 24,
            "summary_hook_never_called": run["summary_calls"] == [],
            "durable_messages_and_history_preserved_at_every_request": len(run["durable_checks"]) == 24 and all(run["durable_checks"]),
            "every_model_unit_returns_correct_answer": len(run["responses"]) == 24 and all(item == {"answer": "deterministic response"} for item in run["responses"]),
            "task_preserves_correct_source_results": all(getattr(entry["result"], "data", None) == f"long-horizon-unit-{index}\n" for index, entry in enumerate(history)),
            "all_task_units_complete": len(run["state"].convergence.credited_fact_ids_seen) >= 24,
            "budget_counts_canonical_requests": run["budget"].model_calls == len(run["gateway"].requests),
        },
        logical_units=len(history), model_calls=run["budget"].model_calls,
        tool_calls=run["budget"].tool_calls, context_pressure_decisions=pressure,
        compact_full_events=pressure, progress_advances=run["event_names"].count("progress_receipt_advanced"),
    )


__all__ = ["lh13", "lh14", "lh15", "lh16"]
