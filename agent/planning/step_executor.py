from __future__ import annotations

from typing import Dict, Optional

from agent.contracts import ToolArgs
from agent.planning.errors import ToolNotFoundError
from agent.planning.plan_model import ToolPlanStep
from agent.planning.provenance_validation import validate_unresolved_symbolic_arguments
from agent.planning.result_bindings import ResultBindingError, resolve_bound_args
from agent.planning.step_contracts import (
    ExecutionContext,
    PreparedInvocation,
    StepExecutionOutcome,
    StepOutcomeKind,
)
from agent.planning.step_failure_support import (
    failure_from_exception,
    failure_from_result,
)
from agent.planning.step_failure_support import (
    finish_permission_denied as _finish_permission_denied,
)
from agent.planning.step_failure_support import (
    finish_post_process_failure as _finish_post_process_failure,
)
from agent.planning.step_failure_support import (
    finish_tool_failure as _finish_tool_failure,
)
from agent.planning.step_failure_support import (
    result_message as _result_message,
)
from agent.planning.step_policies import StepPolicies
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from agent.tools.result_adapter import ensure_canonical_result

# Shared failure projections remain in the planning support boundary.
# This class retains the step lifecycle and state-transition decisions.
__all__ = ["PreparedInvocation", "StepExecutionOutcome", "StepExecutor", "StepOutcomeKind"]

class StepExecutor:
    """Executes and finalizes one already-selected plan step."""
    def __init__(self, context: ExecutionContext):
        self.context = context
        self.policies = StepPolicies(context)

    def execute(self, index: int, objective: str, usage: Dict[str, int]) -> StepExecutionOutcome:
        if self.context.cancellation_token.cancelled:
            return StepExecutionOutcome(StepOutcomeKind.CANCELLED, final_answer="Tarefa cancelada. O progresso concluído foi preservado.")
        prepared = self.prepare_invocation(index)
        if isinstance(prepared, StepExecutionOutcome):
            return prepared
        tool, args, file_path = prepared.tool, prepared.args, prepared.file_path
        if self.policies.is_hard_blocked(tool, args, file_path, usage):
            return self.finish_skipped(index, "passo bloqueado por repetição")
        if self.policies.is_impossible_chunk(tool, args, file_path):
            return self.finish_skipped(index, "intervalo de leitura fora do arquivo")
        result_or_outcome = self._obtain_result(
            index, tool, args, file_path, prepared=prepared
        )
        if isinstance(result_or_outcome, StepExecutionOutcome):
            return result_or_outcome
        return self.finalize_result(index, tool, args, result_or_outcome, file_path, objective, usage)

    def prepare_invocation(self, index: int) -> PreparedInvocation | StepExecutionOutcome:
        prepared = self._prepare(index)
        if isinstance(prepared, StepExecutionOutcome):
            return prepared
        tool, args, file_path = prepared
        validation = self._validate(index, tool, args)
        if validation is not None:
            return validation
        return PreparedInvocation(
            index=index,
            step_id=self.context.agent_state.get_step_id(index),
            tool=tool,
            args=dict(args),
            file_path=file_path,
            plan_id=getattr(self.context.agent_state, "plan_identity", None),
        )

    def _prepare(self, index: int) -> tuple[str, ToolArgs, str] | StepExecutionOutcome:
        state = self.context.agent_state
        step = state.plan[index]
        if not isinstance(step, ToolPlanStep):
            return self.finish_failed(index, "passo executável não é um ToolPlanStep", decisive=True)
        args: ToolArgs = dict(step.args)
        file_path = str(args.get("target") or args.get("file_path") or "")
        state.mark_step_running(index)
        try:
            args = resolve_bound_args(
                step,
                index,
                state.plan,
                state.tool_history,
                plan_id=getattr(state, "plan_identity", None),
            )
        except ResultBindingError as exc:
            return self.finish_failed(index, f"binding inválido: {exc}", decisive=True)
        plan_id = getattr(state, "plan_identity", None)
        observations = getattr(state, "tool_history", ())
        if plan_id is not None:
            observations = tuple(
                item
                for item in observations
                if not isinstance(item, dict)
                or item.get("plan_id") in (None, plan_id)
            )
        symbolic_error = validate_unresolved_symbolic_arguments(
            args=args,
            objective=str(getattr(state, "objective", "") or ""),
            available_observations=observations,
        )
        if symbolic_error is not None:
            return self._finish_unresolved_symbolic(index, symbolic_error)
        file_path = str(args.get("target") or args.get("file_path") or "")
        return step.tool, args, file_path

    def _finish_unresolved_symbolic(
        self, index: int, error: str
    ) -> StepExecutionOutcome:
        result = ToolResult(
            invocation_id=f"planner:{index + 1}",
            status=ToolStatus.BLOCKED,
            error=ToolError("unresolved_symbolic_argument", error),
            message=error,
            executed=False,
        )
        return self.finish_blocked(index, result)

    def _validate(self, index: int, tool: str, args: ToolArgs) -> StepExecutionOutcome | None:
        try:
            return None if self.policies.validate(index + 1, tool, args) else self.finish_failed(index, "passo inválido")
        except ToolNotFoundError as exc:
            failure = failure_from_exception(self.context, index, tool, exc)
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
            self.finish_failed(index, str(exc), failure=failure)
            return StepExecutionOutcome(
                StepOutcomeKind.REPLAN,
                error=str(exc),
                failure=failure,
            )

    def _obtain_result(
        self,
        index: int,
        tool: str,
        args: ToolArgs,
        file_path: str,
        *,
        prepared: PreparedInvocation | None = None,
    ) -> ToolResult | StepExecutionOutcome:
        cache_hit, cached = self.try_cache(tool, args, file_path, self.context.agent_state.get_step_id(index))
        if tool == "file_writer" and args.get("content") and file_path:
            self.context.workspace.show_diff(file_path, str(args["content"]))
        if cache_hit and cached is not None:
            return cached
        if not getattr(self.context, "tool_invocation_gateway", None):
            self.context._emit("tool_start", {"tool": tool, "args": args})
        try:
            prepared_runner = getattr(self.context, "_run_prepared_invocation", None)
            if callable(prepared_runner) and prepared is not None:
                result = prepared_runner(prepared)
            else:
                # Direct unit contexts may omit the prepared adapter. The
                # production Orchestrator always supplies it.
                result = self.context._run_tool(tool, args)
            # Normalize the boundary once for direct unit contexts. Gateway
            # and tool-executor production paths already return this value.
            result = ensure_canonical_result(result)
        except ToolNotFoundError as exc:
            failure = failure_from_exception(self.context, index, tool, exc)
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
            self.finish_failed(index, str(exc), failure=failure)
            return StepExecutionOutcome(
                StepOutcomeKind.REPLAN,
                error=str(exc),
                failure=failure,
            )
        if not getattr(self.context, "tool_invocation_gateway", None):
            self.context._emit("tool_end", {"tool": tool, "ok": result.ok})
        self.context._maybe_summarize_and_store(tool, args, result)
        return result

    def finalize_result(
        self, index: int, tool: str, args: ToolArgs, result: ToolResult,
        file_path: str, objective: str, usage: Dict[str, int],
    ) -> StepExecutionOutcome:
        result = ensure_canonical_result(result)
        status = result.status.value
        if status != ToolStatus.SUCCEEDED.value or not result.ok:
            self.policies.invalidate_observation_state(tool, usage, args=args, result=result)
        if status == "blocked":
            return self.finish_blocked(index, result)
        if status == "unverified":
            return self.finish_unverified(index, result)
        if status == "cancelled":
            reason = _result_message(result, "operação cancelada")
            self.context.agent_state.mark_step_skipped(index, reason)
            self._emit_terminal("step_cancelled", index, reason)
            return StepExecutionOutcome(
                StepOutcomeKind.CANCELLED,
                result=result,
                error=reason,
                final_answer=result.message or "Operação cancelada.",
            )
        if status == "permission_denied":
            return self.finish_permission_denied(index, result)
        if not result.ok:
            return _finish_tool_failure(self, index, tool, args, result)
        if not self.policies.post_process(index + 1, tool, args, result, file_path, objective, usage):
            return _finish_post_process_failure(self, index, tool, args, result)
        self.context.agent_state.mark_step_completed(index)
        self._emit_terminal("step_completed", index)
        return StepExecutionOutcome(StepOutcomeKind.COMPLETED, result=result)

    def finish_permission_denied(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        return _finish_permission_denied(self, index, result)

    def finish_failed(
        self, index: int, error: str, result: Optional[ToolResult] = None,
        *, decisive: bool = False, failure: FailureFact | None = None,
    ) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_failed(index, error)
        self._emit_terminal("step_failed", index, error)
        return StepExecutionOutcome(
            StepOutcomeKind.FAILED,
            result=result,
            error=error,
            decisive=decisive,
            failure=failure,
        )

    def finish_skipped(self, index: int, reason: str) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_skipped(index, reason)
        self._emit_terminal("step_skipped", index, reason)
        return StepExecutionOutcome(StepOutcomeKind.SKIPPED, error=reason)

    def finish_blocked(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        reason = _result_message(result, "confirmação necessária")
        self.context.agent_state.mark_step_blocked(index, reason)
        self._emit_terminal("step_blocked", index, reason)
        return StepExecutionOutcome(
            StepOutcomeKind.BLOCKED,
            result=result,
            error=reason,
            final_answer=result.message or "A execução aguarda aprovação.",
            failure=failure_from_result(self.context, index, result),
        )

    def finish_unverified(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        reason = _result_message(result, "resultado sem validação disponível")
        self.context.agent_state.mark_step_unverified(index, reason)
        self._emit_terminal("step_unverified", index, reason)
        return StepExecutionOutcome(
            StepOutcomeKind.UNVERIFIED,
            result=result,
            error=reason,
            final_answer=result.message or "A execução não pôde ser verificada.",
            failure=failure_from_result(self.context, index, result),
        )

    def _emit_terminal(self, event_type: str, index: int, reason: str = "") -> None:
        data = {"step": index + 1, "step_id": self.context.agent_state.get_step_id(index)}
        if reason:
            data["reason"] = reason
        self.context._emit(event_type, data)

    def try_cache(
        self, tool: str, args: ToolArgs, file_path: str, step_id: Optional[str] = None,
        *, record_result: bool = True,
    ) -> tuple[bool, Optional[ToolResult]]:
        return self.policies.try_cache(
            tool, args, file_path, step_id, record_result=record_result
        )
