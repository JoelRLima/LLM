from __future__ import annotations

from typing import Dict, Optional

from agent.contracts import ToolArgs, ToolResult
from agent.planning.errors import ToolNotFoundError
from agent.planning.result_bindings import ResultBindingError, resolve_bound_args
from agent.planning.step_contracts import (
    ExecutionContext,
    PreparedInvocation,
    StepExecutionOutcome,
    StepOutcomeKind,
)
from agent.planning.step_policies import StepPolicies

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
        generated = self._ensure_writer_content(index, tool, args, objective)
        if generated is not None:
            return generated
        result_or_outcome = self._obtain_result(index, tool, args, file_path)
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
        )

    def _prepare(self, index: int) -> tuple[str, ToolArgs, str] | StepExecutionOutcome:
        state = self.context.agent_state
        step = state.plan[index]
        raw_args = step.get("args")
        args: ToolArgs = raw_args if isinstance(raw_args, dict) else {}
        file_path = str(args.get("target") or args.get("file_path") or "")
        state.mark_step_running(index)
        try:
            args = resolve_bound_args(step, index, state.plan, state.tool_history)
        except ResultBindingError as exc:
            return self.finish_failed(index, f"binding inválido: {exc}", decisive=True)
        file_path = str(args.get("target") or args.get("file_path") or "")
        return str(step.get("tool", "")), args, file_path

    def _validate(self, index: int, tool: str, args: ToolArgs) -> StepExecutionOutcome | None:
        try:
            return None if self.policies.validate(index + 1, tool, args) else self.finish_failed(index, "passo inválido")
        except ToolNotFoundError as exc:
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
            self.finish_failed(index, str(exc))
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, error=str(exc))

    def _ensure_writer_content(
        self, index: int, tool: str, args: ToolArgs, objective: str
    ) -> StepExecutionOutcome | None:
        if tool != "file_writer" or args.get("content"):
            return None
        if self.fill_generated_content(index + 1, tool, args, objective):
            return None
        action = self.context._handle_step_failure(index + 1, "Conteúdo não gerado para file_writer", tool, args)
        if action == "replan":
            self.finish_failed(index, "conteúdo não gerado")
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, error="conteúdo não gerado")
        return self.finish_failed(index, "conteúdo não gerado")

    def _obtain_result(
        self, index: int, tool: str, args: ToolArgs, file_path: str
    ) -> ToolResult | StepExecutionOutcome:
        cache_hit, cached = self.try_cache(tool, args, file_path, self.context.agent_state.get_step_id(index))
        if tool == "file_writer" and args.get("content") and file_path:
            self.context.workspace.show_diff(file_path, str(args["content"]))
        if cache_hit:
            return cached or {}
        if not getattr(self.context, "tool_invocation_gateway", None):
            self.context._emit("tool_start", {"tool": tool, "args": args})
        try:
            result = self.context._run_tool(tool, args)
        except ToolNotFoundError as exc:
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
            self.finish_failed(index, str(exc))
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, error=str(exc))
        if not getattr(self.context, "tool_invocation_gateway", None):
            self.context._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
        self.context._maybe_summarize_and_store(tool, args, result)
        return result

    def finalize_result(
        self, index: int, tool: str, args: ToolArgs, result: ToolResult,
        file_path: str, objective: str, usage: Dict[str, int],
    ) -> StepExecutionOutcome:
        status = str(result.get("status") or "")
        if status == "blocked":
            return self.finish_blocked(index, result)
        if status == "unverified":
            return self.finish_unverified(index, result)
        if status == "cancelled":
            reason = str(result.get("error") or "operação cancelada")
            self.context.agent_state.mark_step_skipped(index, reason)
            self._emit_terminal("step_cancelled", index, reason)
            return StepExecutionOutcome(
                StepOutcomeKind.CANCELLED,
                result=result,
                error=reason,
                final_answer=str(result.get("message") or "Operação cancelada."),
            )
        if status == "permission_denied":
            return self.finish_permission_denied(index, result)
        if not result.get("ok"):
            return self._finish_tool_failure(index, tool, args, result)
        if not self.policies.post_process(index + 1, tool, args, result, file_path, objective, usage):
            return self._finish_post_process_failure(index, tool, args, result)
        self.context.agent_state.mark_step_completed(index)
        self._emit_terminal("step_completed", index)
        return StepExecutionOutcome(StepOutcomeKind.COMPLETED, result=result)

    def finish_permission_denied(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        reason = str(result.get("error") or result.get("message") or "permissão negada")
        self.context.agent_state.mark_step_failed(index, reason)
        self._emit_terminal("step_failed", index, reason)
        return StepExecutionOutcome(
            StepOutcomeKind.PERMISSION_DENIED,
            result=result,
            error=reason,
            final_answer=str(result.get("message") or reason),
            decisive=True,
        )

    def _finish_tool_failure(self, index: int, tool: str, args: ToolArgs, result: ToolResult) -> StepExecutionOutcome:
        error = str(result.get("error") or "falha da ferramenta")
        action = self.context._handle_step_failure(index + 1, f"Tool '{tool}' falhou: {error}", tool, args)
        if action == "replan":
            self.finish_failed(index, error, result)
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, result=result, error=error)
        if action == "continue":
            self.context._purge_stale_context()
        else:
            self.context.fail_task()
        return self.finish_failed(index, error, result, decisive=action != "continue")

    def _finish_post_process_failure(self, index: int, tool: str, args: ToolArgs, result: ToolResult) -> StepExecutionOutcome:
        error = str(result.get("error") or "falha no pós-processamento")
        action = self.context._handle_step_failure(index + 1, f"Tool '{tool}' falhou: {error}", tool, args)
        if action == "replan":
            self.finish_failed(index, error, result)
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, result=result, error=error)
        return self.finish_failed(index, error, result, decisive=action != "continue")

    def finish_failed(
        self, index: int, error: str, result: Optional[ToolResult] = None,
        *, decisive: bool = False,
    ) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_failed(index, error)
        self._emit_terminal("step_failed", index, error)
        return StepExecutionOutcome(
            StepOutcomeKind.FAILED, result=result, error=error, decisive=decisive
        )

    def finish_skipped(self, index: int, reason: str) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_skipped(index, reason)
        self._emit_terminal("step_skipped", index, reason)
        return StepExecutionOutcome(StepOutcomeKind.SKIPPED, error=reason)

    def finish_blocked(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        reason = str(result.get("error") or "confirmação necessária")
        self.context.agent_state.mark_step_blocked(index, reason)
        self._emit_terminal("step_blocked", index, reason)
        return StepExecutionOutcome(
            StepOutcomeKind.BLOCKED,
            result=result,
            error=reason,
            final_answer=str(result.get("message") or "A execução aguarda aprovação."),
        )

    def finish_unverified(self, index: int, result: ToolResult) -> StepExecutionOutcome:
        reason = str(result.get("error") or "resultado sem validação disponível")
        self.context.agent_state.mark_step_unverified(index, reason)
        self._emit_terminal("step_unverified", index, reason)
        return StepExecutionOutcome(
            StepOutcomeKind.UNVERIFIED,
            result=result,
            error=reason,
            final_answer=str(result.get("message") or "A execução não pôde ser verificada."),
        )

    def _emit_terminal(self, event_type: str, index: int, reason: str = "") -> None:
        data = {"step": index + 1, "step_id": self.context.agent_state.get_step_id(index)}
        if reason:
            data["reason"] = reason
        self.context._emit(event_type, data)

    def fill_generated_content(self, step_number: int, tool: str, args: ToolArgs, objective: str) -> bool:
        for _ in range(3):
            generated = self.context._generate_content(tool, args, objective)
            if generated:
                args["content"] = generated
                return True
        action = self.context._handle_step_failure(step_number, "Conteúdo não gerado após 3 tentativas", tool, args)
        if action == "continue":
            self.context._purge_stale_context()
        else:
            self.context.fail_task()
        return False

    def try_cache(
        self, tool: str, args: ToolArgs, file_path: str, step_id: Optional[str] = None,
        *, record_result: bool = True,
    ) -> tuple[bool, Optional[ToolResult]]:
        return self.policies.try_cache(
            tool, args, file_path, step_id, record_result=record_result
        )
