import hashlib
from typing import Any, Dict, Optional

from agent.parsers import stringify, validate_tool_args

DEFAULT_MAX_TASK_STEPS = 20
DEFAULT_MAX_TASK_TOKENS = 25000
DEFAULT_MAX_TASK_TOOL_CALLS = 40


class PlanExecutor:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def execute(self, objective: str, tool_usage_count: Dict[str, int]) -> Optional[str]:
        result = None
        self.orchestrator.workspace.create_restore_point(self.orchestrator.agent_state.plan)

        for i, step in enumerate(self.orchestrator.agent_state.plan):
            self.orchestrator.agent_state.plan_step = i + 1
            limit_answer = self._check_cost_limits(i + 1)
            if limit_answer:
                return limit_answer

            tool = step["tool"]
            args = step["args"] if isinstance(step["args"], dict) else {}
            file_path = args.get("target") or args.get("file_path") or ""

            if not self._validate_step(i + 1, tool, args):
                continue

            if self._is_hard_blocked(i + 1, tool, args, file_path, tool_usage_count):
                continue

            if self._is_impossible_chunk(tool, args, file_path):
                continue

            if tool == "file_writer" and not args.get("content"):
                if not self._fill_generated_content(i + 1, tool, args, objective):
                    continue

            cache_hit, result = self._try_cache(tool, args, file_path)

            if tool == "file_writer" and args.get("content") and file_path:
                self.orchestrator.workspace.show_diff(file_path, args["content"])

            if not cache_hit:
                self.orchestrator._emit("tool_start", {"tool": tool, "args": args})
                result = self.orchestrator._run_tool(tool, args)
                self.orchestrator._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
                self.orchestrator._maybe_summarize_and_store(tool, args, result)

            if not self._post_process_tool(i + 1, tool, args, result, file_path, objective, tool_usage_count):
                break

            edit_answer = self._maybe_finish_edit(objective)
            if edit_answer:
                return edit_answer

        return None

    def _check_cost_limits(self, step_number: int) -> Optional[str]:
        max_steps = self.orchestrator.session.config.get("max_task_steps", DEFAULT_MAX_TASK_STEPS)
        max_tokens = self.orchestrator.session.config.get("max_task_tokens", DEFAULT_MAX_TASK_TOKENS)
        max_tool_calls = self.orchestrator.session.config.get("max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS)
        estimated_tokens = self.orchestrator.context_manager.estimate_conversation_tokens()

        if (
            step_number <= max_steps
            and estimated_tokens <= max_tokens
            and len(self.orchestrator.agent_state.tool_history) <= max_tool_calls
        ):
            return None

        self.orchestrator._emit("cost_limit", {
            "reason": "Limite de custo da tarefa atingido",
            "steps": step_number,
            "max_steps": max_steps,
            "estimated_tokens": estimated_tokens,
            "max_tokens": max_tokens,
            "tool_calls": len(self.orchestrator.agent_state.tool_history),
            "max_tool_calls": max_tool_calls,
        })
        summary_parts = []
        if self.orchestrator.agent_state.tool_history:
            tools_used = set(h["tool"] for h in self.orchestrator.agent_state.tool_history)
            summary_parts.append(f"Ferramentas usadas: {', '.join(tools_used)}")
            summary_parts.append(f"Último resultado: {stringify(self.orchestrator.agent_state.last_result)[:500]}")
        answer = "A tarefa foi interrompida porque atingiu o limite de custo definido. Resumo do que foi feito:\n" + "\n".join(summary_parts)
        self.orchestrator.agent_state.conversation_history.append({"user": self.orchestrator.agent_state.objective, "agent": answer})
        self.orchestrator._task_failed = True
        return answer

    def _validate_step(self, step_number: int, tool: str, args: Dict[str, Any]) -> bool:
        valid, error_msg = validate_tool_args(tool, args, self.orchestrator.skills)
        if not valid:
            action = self.orchestrator._handle_step_failure(step_number, f"Schema: {error_msg}", tool, args)
            if action == "continue":
                self.orchestrator._purge_stale_context()
                return False
            self.orchestrator._task_failed = True
            return False

        if tool not in self.orchestrator.skills or (self.orchestrator.active_skills and tool not in self.orchestrator.active_skills):
            action = self.orchestrator._handle_step_failure(step_number, f"Tool '{tool}' não permitida", tool, args)
            if action == "continue":
                self.orchestrator._purge_stale_context()
                return False
            self.orchestrator._task_failed = True
            return False
        return True

    def _is_hard_blocked(self, step_number: int, tool: str, args: Dict[str, Any],
                         file_path: str, tool_usage_count: Dict[str, int]) -> bool:
        hard_block_reason = None
        if tool == "code_analyzer" and file_path:
            key = f"code_analyzer_{file_path}"
            tool_usage_count[key] = tool_usage_count.get(key, 0) + 1
            if tool_usage_count[key] > 1:
                hard_block_reason = "code_analyzer repetido"

        if tool == "file_reader" and file_path:
            if "start_line" in args and "end_line" in args:
                chunk_key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
                tool_usage_count[chunk_key] = tool_usage_count.get(chunk_key, 0) + 1
                if tool_usage_count[chunk_key] > 1:
                    hard_block_reason = "chunk repetido"
            fully_read_key = f"fully_read_{file_path}"
            if tool_usage_count.get(fully_read_key, 0) > 0:
                hard_block_reason = "arquivo já totalmente lido"

        if not hard_block_reason:
            return False

        self.orchestrator._emit("hard_block", {"file": file_path, "reason": hard_block_reason})
        action = self.orchestrator._handle_step_failure(step_number, f"Hard block: {hard_block_reason}", tool, args)
        if action == "continue":
            self.orchestrator._purge_stale_context()
            return True
        self.orchestrator._task_failed = True
        return True

    def _is_impossible_chunk(self, tool: str, args: Dict[str, Any], file_path: str) -> bool:
        if tool != "file_reader" or "start_line" not in args or "end_line" not in args or not file_path:
            return False
        known_total = None
        for h in self.orchestrator.agent_state.tool_history:
            if h["tool"] == "file_reader" and h.get("result", {}).get("total_lines"):
                h_file = h.get("args", {}).get("file_path") or h.get("args", {}).get("target")
                if h_file == file_path:
                    known_total = h["result"]["total_lines"]
                    break
        if known_total and args["start_line"] > known_total:
            if self.orchestrator.verbose:
                print(f"[DEBUG] Pulando passo: start_line ({args['start_line']}) > total_lines ({known_total}) para '{file_path}'.")
            return True
        return False

    def _fill_generated_content(self, step_number: int, tool: str, args: Dict[str, Any], objective: str) -> bool:
        generated = None
        for _ in range(3):
            generated = self.orchestrator._generate_content(tool, args, objective)
            if generated:
                break
        if not generated:
            for msg in reversed(self.orchestrator.session.messages):
                if msg["role"] == "assistant" and len(msg.get("content", "")) > 20:
                    generated = self.orchestrator._sanitize_error(msg["content"])
                    if len(generated) > 10:
                        break
        if generated:
            args["content"] = generated
            return True

        action = self.orchestrator._handle_step_failure(step_number, "Conteúdo não gerado para file_writer", tool, args)
        if action == "continue":
            self.orchestrator._purge_stale_context()
            return False
        self.orchestrator._task_failed = True
        return False

    def _try_cache(self, tool: str, args: Dict[str, Any], file_path: str):
        if tool not in ("code_analyzer", "file_reader") or not file_path:
            return False, None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                current_hash = hashlib.sha256(f.read().encode("utf-8")).hexdigest()
        except Exception:
            current_hash = None

        stored_hash = self.orchestrator.agent_state.memory.state.get("file_hashes", {}).get(file_path)
        if not current_hash or not stored_hash or current_hash != stored_hash:
            return False, None

        summary = self.orchestrator.agent_state.memory.state.get("file_summaries", {}).get(file_path, "")
        if not summary:
            return False, None

        result = {"ok": True, "done": True, "data": summary, "message": f"Usando cache de {file_path}."}
        self.orchestrator._emit("cache_hit", {"file": file_path, "hash": current_hash[:8]})
        self.orchestrator._emit("tool_end", {"tool": tool, "ok": True})
        self.orchestrator.agent_state.last_tool = tool
        self.orchestrator.agent_state.last_args = args
        self.orchestrator.agent_state.last_result = result
        self.orchestrator.agent_state.tool_history.append({"tool": tool, "args": args, "result": result})
        return True, result

    def _post_process_tool(self, step_number: int, tool: str, args: Dict[str, Any], result: Dict[str, Any],
                           file_path: str, objective: str, tool_usage_count: Dict[str, int]) -> bool:
        if tool == "file_writer" and result.get("ok") and file_path.endswith(".py"):
            if self.orchestrator.verbose:
                print(f"🧪 [TEST] Iniciando ciclo teste-correção para '{file_path}'...")
            if not self.orchestrator._test_and_correct(file_path, objective):
                self.orchestrator._task_failed = True
                self.orchestrator._emit("error", {"step": step_number, "error": "Ciclo teste-correção falhou"})
                return False

            lint_error = self.orchestrator.workspace.lint_check(file_path)
            if lint_error:
                self.orchestrator._emit("warning", {"step": step_number, "warning": f"Problemas de lint em '{file_path}':\n{lint_error}"})
                if self.orchestrator.verbose:
                    print(f"⚠️ [LINT] Problemas encontrados em '{file_path}':\n{lint_error}")

        if tool == "file_reader" and result.get("ok") and "total_lines" in result:
            total_lines = result["total_lines"]
            end_line = args.get("end_line", total_lines)
            if end_line == total_lines:
                tool_usage_count[f"fully_read_{file_path}"] = 1
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Arquivo '{file_path}' completamente lido ({total_lines} linhas).")

        self.orchestrator.context_manager.maybe_compress_context()

        if result is not None and not result.get("ok"):
            action = self.orchestrator._handle_step_failure(step_number, f"Tool '{tool}' falhou: {result.get('error')}", tool, args)
            if action == "continue":
                self.orchestrator._purge_stale_context()
                return True
            self.orchestrator._task_failed = True
            return False
        return True

    def _maybe_finish_edit(self, objective: str) -> Optional[str]:
        edit_terms = ["mudar", "mude", "alterar", "altere", "corrigir", "corrija", "substituir", "substitua", "editar", "edite", "ajustar", "ajuste"]
        if not any(kw in objective.lower() for kw in edit_terms):
            return None
        if any(h["tool"] == "file_writer" and h.get("result", {}).get("ok") for h in self.orchestrator.agent_state.tool_history):
            answer = "Arquivo alterado com sucesso."
            self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
            return answer
        return None
