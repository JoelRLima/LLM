"""Canonical model-owned planning decisions and prompt boundary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.llm.admitted_decisions import (
    DirectResponseDecision,
    EffectObservationBlockedDecision,
    EffectObservationCompleteWithoutEffectDecision,
    EffectObservationExecuteDecision,
    InitialPlanDecision,
    LegacyModelDecision,
    ReasoningBoundaryBlockedDecision,
    ReasoningBoundaryCompleteDecision,
    ReasoningBoundaryExecuteDecision,
    ask_model_decision_with_compatibility,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.planning.plan_builder_compat import (
    build_legacy_continuation,
    build_legacy_initial,
    continuation_step_id,
)
from agent.planning.plan_model import Plan, PlanDecodeError
from agent.planning.plan_progress import build_plan_progress
from agent.planning.plan_prompts import (
    build_continuation_prompt,
    build_plan_prompt,
    build_reasoning_boundary_prompt,
)
from agent.planning.planner_prompt_tools import (
    build_planner_tools_description,
    build_tool_guidance,
)
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.task_semantics import TaskSemanticsError

__all__ = ["PlanBuilder", "PlanBuildResult", "PlanningDecisionKind", "build_planner_tools_description"]


class PlanningDecisionKind(str, Enum):
    EXECUTE = "execute"
    REPLAN = "replan"
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


@dataclass(frozen=True)
class PlanBuildResult:
    # A live builder result carries the immutable typed plan.  The runtime
    # gateway still accepts a list-shaped value for explicit test/legacy
    # facades, but the production builder never emits that projection.
    plan: Optional[Plan] = None
    obligations: Optional[List[Dict[str, Any]]] = None
    review_obligations: Optional[List[Any]] = None
    blocked_answer: Optional[str] = None
    direct_answer: Optional[str] = None
    waiver_observation_index: Optional[int] = None
    continue_after_plan: bool = False
    kind: PlanningDecisionKind | None = None
    planning_view: PlanningPresentationSnapshot | None = None

    def __post_init__(self) -> None:
        if self.kind is not None:
            return
        if self.plan:
            selected = PlanningDecisionKind.EXECUTE
        elif self.blocked_answer:
            selected = PlanningDecisionKind.BLOCK
        elif self.direct_answer or self.waiver_observation_index is not None:
            selected = PlanningDecisionKind.COMPLETE
        else:
            selected = PlanningDecisionKind.REPLAN
        object.__setattr__(self, "kind", selected)


class PlanBuilder:
    def __init__(self, orchestrator: Any, *, analysis_notes_file: str | Path = "analysis_notes.md"):
        self.orchestrator = orchestrator
        self.analysis_notes_file = Path(analysis_notes_file)
        self._last_planning_view: PlanningPresentationSnapshot | None = None

    def build_plan(self, objective: str) -> PlanBuildResult:
        self._clear_analysis_notes()
        decision = ask_model_decision_with_compatibility(
            self.orchestrator.context_manager,
            self._build_prompt(objective), step_type="plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
            request_contract=ModelRequestContract.INITIAL_PLAN,
        )
        if self.orchestrator.verbose:
            print(f"[DEBUG] plan_decision admitido: {decision!r}")
        if isinstance(decision, DirectResponseDecision):
            self.orchestrator._emit("direct_response", {})
            return PlanBuildResult(direct_answer=decision.answer)
        if isinstance(decision, LegacyModelDecision):
            return build_legacy_initial(self, decision)
        if not isinstance(decision, InitialPlanDecision):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        obligations_ok, reviewed_obligations = self._review_obligations(
            decision.obligations, source="initial_plan"
        )
        if not obligations_ok:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._project_plan(decision)
        if plan is None:
            return PlanBuildResult()
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano proposto com {len(plan)} passos: {plan}")
        return PlanBuildResult(
            plan=plan,
            obligations=reviewed_obligations,
            planning_view=self._last_planning_view,
        )

    def _review_obligations(
        self,
        obligations: Sequence[Any] | None,
        *,
        source: str,
    ) -> tuple[bool, Optional[List[Dict[str, Any]]]]:
        if obligations is None:
            return True, None
        state = getattr(self.orchestrator, "agent_state", None)
        report_reviewer = getattr(state, "review_task_obligations_report", None)
        if not callable(report_reviewer):
            return False, None
        try:
            reviewed = report_reviewer(obligations, source=source)
            accepted = reviewed.accepted
        except (TaskSemanticsError, TypeError, ValueError):
            return False, None
        return (
            True,
            [item.to_dict() for item in accepted],
        )

    def continue_after_observation(self, objective: str, effect_evidence: str, observation_references: str) -> PlanBuildResult:
        summary = ""
        responder = getattr(self.orchestrator, "final_responder", None)
        summarize = getattr(responder, "_tool_results_summary", None)
        if callable(summarize):
            summary = str(summarize())
        decision = ask_model_decision_with_compatibility(
            self.orchestrator.context_manager,
            self._build_continuation_prompt(objective, summary, effect_evidence, observation_references, build_plan_progress(self.orchestrator.agent_state)),
            step_type="continuation_plan", base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
            request_contract=ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
        )
        if isinstance(decision, EffectObservationCompleteWithoutEffectDecision):
            return PlanBuildResult(
                waiver_observation_index=decision.observation_index,
                kind=PlanningDecisionKind.COMPLETE,
            )
        if isinstance(decision, EffectObservationBlockedDecision):
            return PlanBuildResult(
                blocked_answer=(
                    decision.reason.strip()
                    if decision.reason.strip()
                    else "Efeito solicitado permanece pendente."
                ),
                kind=PlanningDecisionKind.BLOCK,
            )
        if isinstance(decision, LegacyModelDecision):
            return build_legacy_continuation(self, decision)
        if not isinstance(decision, EffectObservationExecuteDecision):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._project_plan(decision)
        return (
            PlanBuildResult(
                plan=plan,
                kind=PlanningDecisionKind.EXECUTE,
                planning_view=self._last_planning_view,
            )
            if plan
            else PlanBuildResult(
                kind=PlanningDecisionKind.FAIL,
                planning_view=self._last_planning_view,
            )
        )

    def continue_after_reasoning_boundary(self, objective: str) -> PlanBuildResult:
        summary = ""
        responder = getattr(self.orchestrator, "final_responder", None)
        summarize = getattr(responder, "_tool_results_summary", None)
        if callable(summarize):
            summary = str(summarize())
        decision = ask_model_decision_with_compatibility(
            self.orchestrator.context_manager,
            self._build_reasoning_boundary_prompt(objective, summary, build_plan_progress(self.orchestrator.agent_state)),
            step_type="continuation_plan", base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
            request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
        )
        if isinstance(decision, ReasoningBoundaryCompleteDecision):
            return PlanBuildResult(
                review_obligations=(
                    list(decision.obligations)
                    if decision.obligations is not None
                    else None
                ),
                kind=PlanningDecisionKind.COMPLETE,
            )
        if isinstance(decision, ReasoningBoundaryBlockedDecision):
            return PlanBuildResult(
                blocked_answer=decision.reason.strip(), kind=PlanningDecisionKind.BLOCK
            )
        if not isinstance(decision, ReasoningBoundaryExecuteDecision):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._project_plan(decision)
        if not plan:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        return PlanBuildResult(
            plan=plan,
            review_obligations=(
                list(decision.obligations)
                if decision.obligations is not None
                else None
            ),
            kind=PlanningDecisionKind.EXECUTE,
            planning_view=self._last_planning_view,
        )

    def _clear_analysis_notes(self) -> None:
        if not self.analysis_notes_file.exists():
            return
        try:
            self.analysis_notes_file.write_text("", encoding="utf-8")
        except OSError:
            pass

    def _build_prompt(self, objective: str) -> str:
        hints = self.orchestrator.context_manager.get_file_hints(objective)
        tools = self._tool_guidance(objective, force_refresh=False)
        return build_plan_prompt(objective, hints, tools)

    def _build_continuation_prompt(self, objective: str, observations: str, effect_evidence: str, observation_references: str, plan_progress: str) -> str:
        tools = self._tool_guidance(objective, force_refresh=True)
        return build_continuation_prompt(objective, observations, effect_evidence, observation_references, plan_progress, tools)

    def _build_reasoning_boundary_prompt(self, objective: str, observations: str, plan_progress: str) -> str:
        tools = self._tool_guidance(objective, force_refresh=True)
        return build_reasoning_boundary_prompt(objective, observations, plan_progress, tools)

    def _tool_guidance(self, objective: str, *, force_refresh: bool) -> str:
        disclosure, guidance = build_tool_guidance(
            self.orchestrator,
            planner_kind="linear",
            objective=objective,
            force_refresh=force_refresh,
        )
        self._last_planning_view = disclosure.selected_view if disclosure is not None else None
        return guidance

    def _plan_progress(self) -> str:
        return build_plan_progress(self.orchestrator.agent_state)

    def _project_plan(self, decision: Any) -> Optional[Plan]:
        """Decode an admitted decision into the live typed plan value."""

        try:
            plan = Plan.from_decision(
                decision, new_step_id=continuation_step_id(self)
            )
        except PlanDecodeError:
            return None
        return plan if plan else None
