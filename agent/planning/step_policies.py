from __future__ import annotations

import hashlib
from typing import Dict, Optional

from agent.contracts import ToolArgs, ToolResult
from agent.parsers import validate_tool_args
from agent.planning.errors import ToolNotFoundError
from agent.planning.step_contracts import ExecutionContext


class StepPolicies:
    """Validation, deduplication, cache and post-processing policies for a step."""

    def __init__(self, context: ExecutionContext) -> None:
        self.context = context

    def validate(self, step_number: int, tool: str, args: ToolArgs) -> bool:
        valid, error = validate_tool_args(tool, args, self.context.skills)
        if not valid:
            return self._reject(step_number, f"Schema: {error}", tool, args)
        if tool not in self.context.skills:
            raise ToolNotFoundError(f"Tool '{tool}' não foi registrada no Orchestrator.")
        if self.context.active_skills and tool not in self.context.active_skills:
            return self._reject(step_number, f"Tool '{tool}' não permitida", tool, args)
        return True

    def _reject(self, step_number: int, reason: str, tool: str, args: ToolArgs) -> bool:
        action = self.context._handle_step_failure(step_number, reason, tool, args)
        if action == "continue":
            self.context._purge_stale_context()
        else:
            self.context.fail_task()
        return False

    def is_hard_blocked(
        self, tool: str, args: ToolArgs, file_path: str, usage: Dict[str, int]
    ) -> bool:
        reason = self._analyzer_repetition(tool, file_path, usage)
        reason = reason or self._reader_repetition(tool, args, file_path, usage)
        if reason and self.context.verbose:
            print(f"[DEBUG] Hard block silencioso: {reason} em '{file_path}'")
        return bool(reason)

    @staticmethod
    def _analyzer_repetition(tool: str, file_path: str, usage: Dict[str, int]) -> str | None:
        if tool != "code_analyzer" or not file_path:
            return None
        key = f"code_analyzer_{file_path}"
        usage[key] = usage.get(key, 0) + 1
        if usage[key] <= 1:
            return None
        usage[f"fully_read_{file_path}"] = 1
        usage[f"fully_analyzed_{file_path}"] = 1
        return "code_analyzer repetido"

    @staticmethod
    def _reader_repetition(tool: str, args: ToolArgs, file_path: str, usage: Dict[str, int]) -> str | None:
        if tool != "file_reader" or not file_path:
            return None
        if "start_line" in args and "end_line" in args:
            key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
            usage[key] = usage.get(key, 0) + 1
            if usage[key] > 1:
                return "chunk repetido"
        return "arquivo já totalmente lido" if usage.get(f"fully_read_{file_path}", 0) else None

    def is_impossible_chunk(self, tool: str, args: ToolArgs, file_path: str) -> bool:
        if tool != "file_reader" or "start_line" not in args or "end_line" not in args or not file_path:
            return False
        known_total = self._known_total_lines(file_path)
        return bool(known_total and args["start_line"] > known_total)

    def _known_total_lines(self, file_path: str) -> int | None:
        for history in self.context.agent_state.tool_history:
            result = history.get("result", {})
            history_args = history.get("args", {})
            history_file = history_args.get("file_path") or history_args.get("target")
            if history["tool"] == "file_reader" and result.get("total_lines") and history_file == file_path:
                return int(result["total_lines"])
        return None

    def try_cache(
        self, tool: str, args: ToolArgs, file_path: str, step_id: Optional[str] = None
    ) -> tuple[bool, Optional[ToolResult]]:
        if tool not in ("code_analyzer", "file_reader") or not file_path or "start_line" in args or "end_line" in args:
            return False, None
        current_hash = self._file_hash(file_path)
        memory = self.context.agent_state.memory.state
        if not current_hash or current_hash != memory.get("file_hashes", {}).get(file_path):
            return False, None
        summary = memory.get("file_summaries", {}).get(file_path, "")
        if not summary:
            return False, None
        result: ToolResult = {"ok": True, "done": True, "data": summary, "message": f"Usando cache de {file_path}."}
        self.context._emit("cache_hit", {"file": file_path, "hash": current_hash[:8]})
        self.context._emit("tool_end", {"tool": tool, "ok": True})
        self.context.agent_state.record_tool_result(tool, args, result, step_id=step_id)
        return True, result

    @staticmethod
    def _file_hash(file_path: str) -> str | None:
        try:
            with open(file_path, "r", encoding="utf-8") as source:
                return hashlib.sha256(source.read().encode("utf-8")).hexdigest()
        except OSError:
            return None

    def post_process(
        self, step_number: int, tool: str, args: ToolArgs, result: ToolResult,
        file_path: str, objective: str, usage: Dict[str, int],
    ) -> bool:
        if tool == "file_writer" and result.get("ok") and file_path.endswith(".py"):
            if not self.context._test_and_correct(file_path, objective):
                self.context.fail_task()
                self.context._emit("error", {"step": step_number, "error": "Ciclo teste-correção falhou"})
                return False
            lint_error = self.context.workspace.lint_check(file_path)
            if lint_error:
                self.context._emit("warning", {"step": step_number, "warning": f"Problemas de lint em '{file_path}':\n{lint_error}"})
        if tool == "file_reader" and result.get("ok") and "total_lines" in result:
            total = result["total_lines"]
            if args.get("end_line", total) == total:
                usage[f"fully_read_{file_path}"] = 1
        self.context.context_manager.maybe_compress_context()
        return True
