from __future__ import annotations

from typing import Dict, Optional

from agent.contracts import ToolArgs, ToolResult
from agent.planning.errors import ToolNotFoundError
from agent.planning.step_contracts import (
    ExecutionContext,
    StepExecutionOutcome,
    StepOutcomeKind,
)
from agent.planning.step_policies import StepPolicies

__all__ = ["StepExecutionOutcome", "StepExecutor", "StepOutcomeKind"]


class StepExecutor:
    """Executes and finalizes one already-selected plan step."""

    def __init__(self, context: ExecutionContext):
        self.context = context
        self.policies = StepPolicies(context)

    def execute(self, index: int, objective: str, usage: Dict[str, int]) -> StepExecutionOutcome:
        if self.context.cancellation_token.cancelled:
            return StepExecutionOutcome(StepOutcomeKind.CANCELLED, final_answer="Tarefa cancelada. O progresso concluído foi preservado.")
        tool, args, file_path = self._prepare(index)
        validation = self._validate(index, tool, args)
        if validation is not None:
            return validation
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

    def _prepare(self, index: int) -> tuple[str, ToolArgs, str]:
        state = self.context.agent_state
        step = state.plan[index]
        raw_args = step.get("args")
        args: ToolArgs = raw_args if isinstance(raw_args, dict) else {}
        file_path = str(args.get("target") or args.get("file_path") or "")
        state.mark_step_running(index)
        return str(step.get("tool", "")), args, file_path

    def _validate(self, index: int, tool: str, args: ToolArgs) -> StepExecutionOutcome | None:
        try:
            return None if self.policies.validate(index + 1, tool, args) else self.finish_failed(index, "passo inválido")
        except ToolNotFoundError as exc:
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
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
        self.context._emit("tool_start", {"tool": tool, "args": args})
        try:
            result = self.context._run_tool(tool, args)
        except ToolNotFoundError as exc:
            self.context._emit("error", {"step": index + 1, "error": str(exc)})
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, error=str(exc))
        self.context._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
        self.context._maybe_summarize_and_store(tool, args, result)
        return result

    def finalize_result(
        self, index: int, tool: str, args: ToolArgs, result: ToolResult,
        file_path: str, objective: str, usage: Dict[str, int],
    ) -> StepExecutionOutcome:
        if not result.get("ok"):
            return self._finish_tool_failure(index, tool, args, result)
        if not self.policies.post_process(index + 1, tool, args, result, file_path, objective, usage):
            return self._finish_post_process_failure(index, tool, args, result)
        self.context.agent_state.mark_step_completed(index)
        self._emit_terminal("step_completed", index)
        edit_answer = self.maybe_finish_edit(objective)
        if edit_answer:
            return StepExecutionOutcome(StepOutcomeKind.FINAL, result=result, final_answer=edit_answer)
        return StepExecutionOutcome(StepOutcomeKind.COMPLETED, result=result)

    def _finish_tool_failure(self, index: int, tool: str, args: ToolArgs, result: ToolResult) -> StepExecutionOutcome:
        error = str(result.get("error") or "falha da ferramenta")
        action = self.context._handle_step_failure(index + 1, f"Tool '{tool}' falhou: {error}", tool, args)
        if action == "replan":
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, result=result, error=error)
        if action == "continue":
            self.context._purge_stale_context()
        else:
            self.context.fail_task()
        return self.finish_failed(index, error, result)

    def _finish_post_process_failure(self, index: int, tool: str, args: ToolArgs, result: ToolResult) -> StepExecutionOutcome:
        error = str(result.get("error") or "falha no pós-processamento")
        action = self.context._handle_step_failure(index + 1, f"Tool '{tool}' falhou: {error}", tool, args)
        if action == "replan":
            return StepExecutionOutcome(StepOutcomeKind.REPLAN, result=result, error=error)
        return self.finish_failed(index, error, result)

    def finish_failed(self, index: int, error: str, result: Optional[ToolResult] = None) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_failed(index, error)
        self._emit_terminal("step_failed", index, error)
        return StepExecutionOutcome(StepOutcomeKind.FAILED, result=result, error=error)

    def finish_skipped(self, index: int, reason: str) -> StepExecutionOutcome:
        self.context.agent_state.mark_step_skipped(index, reason)
        self._emit_terminal("step_skipped", index, reason)
        return StepExecutionOutcome(StepOutcomeKind.SKIPPED, error=reason)

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

    def try_cache(self, tool: str, args: ToolArgs, file_path: str, step_id: Optional[str] = None) -> tuple[bool, Optional[ToolResult]]:
        return self.policies.try_cache(tool, args, file_path, step_id)

    def maybe_finish_edit(self, objective: str) -> Optional[str]:
        terms = ("mudar", "mude", "alterar", "altere", "corrigir", "corrija", "substituir", "substitua", "editar", "edite", "ajustar", "ajuste")
        if not any(term in objective.lower() for term in terms):
            return None
        changed = any(item["tool"] == "file_writer" and item.get("result", {}).get("ok") for item in self.context.agent_state.tool_history)
        if not changed:
            return None
        answer = "Arquivo alterado com sucesso."
        self.context.agent_state.add_conversation_turn(objective, answer)
        return answer
