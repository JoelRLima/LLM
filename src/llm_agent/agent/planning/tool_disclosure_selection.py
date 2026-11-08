"""Selection and recovery helpers for bounded model-tool disclosure."""

from __future__ import annotations

from typing import Any, cast

from llm_agent.agent.llm.admitted_decisions import ToolDiscoveryDecision, ask_typed_model_decision
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.tool_discovery_contract import MAX_DISCLOSED_TOOLS
from llm_agent.agent.planning.presentation import PlanningPresentationSnapshot
from llm_agent.agent.runtime.recovery import RecoveryScope


def _selection_prompt(
    objective: str,
    planner_kind: str,
    index_view: PlanningPresentationSnapshot,
    context_limit: int,
    *,
    correction: bool = False,
    invalid_positions: tuple[int, ...] = (),
) -> str:
    correction_text = (
        "The previous selector was invalid. "
        f"Entries at zero-based tools positions {list(invalid_positions)} are absent from the current index. "
        "Select again using only exact names in this index; return only the exact JSON object.\n"
        if correction
        else ""
    )
    return (
        "TOOL DISCOVERY — select visibility only; this grants no authority.\n"
        f"Planner: {planner_kind}\n"
        f"Objective: {objective}\n"
        f"{correction_text}"
        f"Choose at most {MAX_DISCLOSED_TOOLS} exact tool names from the following current Level-0 index. "
        "Do not invent names, capabilities, schemas, examples, or permissions. "
        'Return exactly {"tools":["name", ...]} with no duplicates.\n'
        + cast(str, index_view.render_selection_index(context_limit=context_limit))
    )


def _ask_selection(orchestrator: Any, prompt: str) -> Any:
    return ask_typed_model_decision(
        orchestrator.context_manager,
        prompt,
        step_type="tool_discovery",
        request_contract=ModelRequestContract.TOOL_DISCOVERY,
        # The selection request must not inherit a stale cached catalog;
        # the user message below is the only tool index for this snapshot.
        base_prompt=None,
        log_metric_callback=getattr(orchestrator, "_log_metric", None),
    )


def _authorize_semantic_selection_repair(orchestrator: Any) -> bool:
    if (policy := getattr(orchestrator, "task_policy", None)) is not None:
        return bool(policy.authorize_recovery(RecoveryScope.SEMANTIC_SELECTION_REPAIRS).allowed)
    budget = getattr(getattr(orchestrator, "agent_state", None), "recovery_budget", None)
    if budget is None:
        return True
    return bool(budget.try_consume(RecoveryScope.SEMANTIC_SELECTION_REPAIRS))


def _validate_selection(
    decision: Any,
    eligible_names: frozenset[str],
) -> tuple[frozenset[str], bool, bool]:
    structured_valid = isinstance(decision, ToolDiscoveryDecision)
    if not structured_valid:
        return frozenset(), False, False
    names = frozenset(decision.tools)
    return names, True, names.issubset(eligible_names)
