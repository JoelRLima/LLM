"""Canonical model-owned planning decisions and prompt boundary."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

from agent.planning.plan_prompts import (
    build_continuation_prompt,
    build_plan_prompt,
    build_reasoning_boundary_prompt,
)
from agent.planning.task_semantics import TaskSemanticsError


class PlanningDecisionKind(str, Enum):
    EXECUTE = "execute"
    REPLAN = "replan"
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


@dataclass(frozen=True)
class PlanBuildResult:
    plan: Optional[List[Dict[str, Any]]] = None
    obligations: Optional[List[Dict[str, Any]]] = None
    review_obligations: Optional[List[Dict[str, Any]]] = None
    blocked_answer: Optional[str] = None
    direct_answer: Optional[str] = None
    waiver_observation_index: Optional[int] = None
    continue_after_plan: bool = False
    kind: PlanningDecisionKind | None = None

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


def build_planner_tools_description(orchestrator: Any, *, planner_kind: str, compact: bool) -> str:
    builder = orchestrator._build_tools_description
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError) as exc:
        if getattr(orchestrator, "planning_context", None) is not None:
            raise TypeError("canonical planner catalog signature is unavailable") from exc
        return cast(str, builder(compact=compact))
    supports_kind = "planner_kind" in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if supports_kind:
        return cast(str, builder(compact=compact, planner_kind=planner_kind))
    if getattr(orchestrator, "planning_context", None) is not None:
        raise TypeError("canonical planner catalog requires planner_kind")
    return cast(str, builder(compact=compact))


class PlanBuilder:
    def __init__(self, orchestrator: Any, *, analysis_notes_file: str | Path = "analysis_notes.md"):
        self.orchestrator = orchestrator
        self.analysis_notes_file = Path(analysis_notes_file)

    def build_plan(self, objective: str) -> PlanBuildResult:
        self._clear_analysis_notes()
        decision = self.orchestrator.context_manager.ask_model(
            self._build_prompt(objective), step_type="plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        if self.orchestrator.verbose:
            print(f"[DEBUG] plan_decision bruto: {decision}")
        if not isinstance(decision, dict):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        obligations_ok, reviewed_obligations = self._review_obligations(decision)
        if not obligations_ok:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        direct_answer = self._direct_answer(decision)
        if direct_answer is not None:
            self.orchestrator._emit("direct_response", {})
            return PlanBuildResult(direct_answer=direct_answer)
        plan = self._normalize_decision(decision)
        if plan is None:
            return PlanBuildResult()
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano proposto com {len(plan)} passos: {plan}")
        return PlanBuildResult(
            plan=cast(List[Dict[str, Any]], plan),
            obligations=reviewed_obligations,
            continue_after_plan=decision.get("action") == "continue_after_plan",
        )

    def _review_obligations(
        self, decision: Dict[str, Any]
    ) -> tuple[bool, Optional[List[Dict[str, Any]]]]:
        if "obligations" not in decision:
            return True, None
        state = getattr(self.orchestrator, "agent_state", None)
        report_reviewer = getattr(state, "review_task_obligations_report", None)
        legacy_reviewer = getattr(state, "review_task_obligations", None)
        if not callable(report_reviewer) and not callable(legacy_reviewer):
            return False, None
        try:
            if callable(report_reviewer):
                reviewed = report_reviewer(decision["obligations"], source="initial_plan")
                accepted = reviewed.accepted
            else:
                reviewed = cast(Callable[..., Any], legacy_reviewer)(
                    decision["obligations"],
                    source="initial_plan",
                    collect_rejections=True,
                )
                accepted = tuple(reviewed)
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
        decision = self.orchestrator.context_manager.ask_model(
            self._build_continuation_prompt(objective, summary, effect_evidence, observation_references, self._plan_progress()),
            step_type="continuation_plan", base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        action = decision.get("action")
        if action == "complete_without_effect":
            index = decision.get("observation_index")
            if set(decision) != {"action", "observation_index"} or type(index) is not int or index < 1:
                return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
            return PlanBuildResult(waiver_observation_index=index, kind=PlanningDecisionKind.COMPLETE)
        if action == "blocked":
            reason = decision.get("reason")
            if set(decision) != {"action", "reason"}:
                return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
            return PlanBuildResult(
                blocked_answer=reason.strip() if isinstance(reason, str) and reason.strip() else "Efeito solicitado permanece pendente.",
                kind=PlanningDecisionKind.BLOCK,
            )
        if action not in {"execute", "continue_after_plan"} or set(decision) != {"action", "plan"}:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._normalize_decision(decision)
        return PlanBuildResult(plan=cast(List[Dict[str, Any]], plan), kind=PlanningDecisionKind.EXECUTE) if plan else PlanBuildResult(kind=PlanningDecisionKind.FAIL)

    def continue_after_reasoning_boundary(self, objective: str) -> PlanBuildResult:
        summary = ""
        responder = getattr(self.orchestrator, "final_responder", None)
        summarize = getattr(responder, "_tool_results_summary", None)
        if callable(summarize):
            summary = str(summarize())
        decision = self.orchestrator.context_manager.ask_model(
            self._build_reasoning_boundary_prompt(objective, summary, self._plan_progress()),
            step_type="continuation_plan", base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        if not isinstance(decision, dict):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        terminal = self._reasoning_boundary_terminal_result(decision)
        if terminal is not None:
            return terminal
        return self._reasoning_boundary_execute_result(decision)

    @staticmethod
    def _optional_boundary_obligations(
        decision: Dict[str, Any],
    ) -> tuple[bool, Optional[List[Dict[str, Any]]]]:
        if "obligations" not in decision:
            return True, None
        obligations = decision["obligations"]
        return (isinstance(obligations, list), obligations if isinstance(obligations, list) else None)

    def _reasoning_boundary_terminal_result(
        self, decision: Dict[str, Any]
    ) -> Optional[PlanBuildResult]:
        action = decision.get("action")
        if action not in {"complete", "blocked"}:
            return None
        allowed = {"action", "reason"}
        if action == "complete":
            allowed.add("obligations")
        if set(decision) not in ({"action", "reason"}, allowed):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        obligations_ok, review_obligations = self._optional_boundary_obligations(decision)
        reason = decision.get("reason")
        if not obligations_ok or not isinstance(reason, str) or not reason.strip():
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        if action == "complete":
            return PlanBuildResult(
                review_obligations=review_obligations,
                kind=PlanningDecisionKind.COMPLETE,
            )
        return PlanBuildResult(blocked_answer=reason.strip(), kind=PlanningDecisionKind.BLOCK)

    def _reasoning_boundary_execute_result(self, decision: Dict[str, Any]) -> PlanBuildResult:
        allowed = {"action", "plan", "obligations"}
        if decision.get("action") != "execute" or set(decision) not in ({"action", "plan"}, allowed):
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        obligations_ok, review_obligations = self._optional_boundary_obligations(decision)
        if not obligations_ok:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._normalize_decision(decision)
        if not plan:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        return PlanBuildResult(
            plan=cast(List[Dict[str, Any]], plan),
            review_obligations=review_obligations,
            kind=PlanningDecisionKind.EXECUTE,
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
        tools = build_planner_tools_description(self.orchestrator, planner_kind="linear", compact=True)
        return build_plan_prompt(objective, hints, tools)

    def _build_continuation_prompt(self, objective: str, observations: str, effect_evidence: str, observation_references: str, plan_progress: str) -> str:
        tools = build_planner_tools_description(self.orchestrator, planner_kind="linear", compact=True)
        return build_continuation_prompt(objective, observations, effect_evidence, observation_references, plan_progress, tools)

    def _build_reasoning_boundary_prompt(self, objective: str, observations: str, plan_progress: str) -> str:
        tools = build_planner_tools_description(self.orchestrator, planner_kind="linear", compact=True)
        return build_reasoning_boundary_prompt(objective, observations, plan_progress, tools)

    def _plan_progress(self) -> str:
        state = self.orchestrator.agent_state
        lines = [
            f"{index + 1}: status={state.get_step_status(index).value}, tool={json.dumps(str(step.get('tool', '')), ensure_ascii=True)}"
            for index, step in enumerate(state.plan)
        ]
        semantics = getattr(state, "task_semantics", None)
        snapshot = getattr(semantics, "snapshot", None)
        if callable(snapshot):
            lines.append("Obrigacoes canonicas e evidencias:")
            for item in snapshot():
                lines.append(
                    "- id={id}; kind={kind}; status={status}; evidence={evidence}; description={description}".format(
                        id=item.get("id", ""),
                        kind=item.get("kind", ""),
                        status=item.get("status", "pending"),
                        evidence=json.dumps(item.get("evidence_refs", []), ensure_ascii=True),
                        description=item.get("description", ""),
                    )
                )
        return "\n".join(lines)

    @staticmethod
    def _direct_answer(decision: Dict[str, Any]) -> Optional[str]:
        if decision.get("action") != "direct_response":
            return None
        answer = decision.get("answer")
        return answer.strip() if isinstance(answer, str) and answer.strip() else None

    def _normalize_decision(self, decision: Dict[str, Any]) -> Optional[List[Any]]:
        plan = decision.get("plan")
        if isinstance(plan, list):
            return plan or None
        single = self._single_step(decision)
        return [single] if single else None

    def _single_step(self, decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tool = decision.get("tool")
        args = decision.get("args", {})
        if not isinstance(args, dict):
            args = {}
        if not tool and "file_path" in decision:
            tool, args = "file_reader", decision
        elif not tool and "target" in decision:
            tool, args = "code_analyzer", decision
        if self.orchestrator.verbose and tool:
            print(f"[DEBUG] Plano extraído de campos soltos: {tool}")
        return {"tool": tool, "args": args} if tool else None
