import json
from typing import Any, Dict

from agent.cost_guard import CostGuard
from agent.watchdog import Watchdog
from agent.parsers import stringify, validate_tool_args


class ReactiveLoop:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def run_reactive(self, objective: str, tool_usage_count: Dict[str, int], original_msg_count: int) -> str:
        """
        Fallback reativo (modo antigo) para quando o plano não é gerado
        """
        while True:
            # Verifica limites de custo da tarefa
            step_number = self.orchestrator.agent_state.plan_step + 1
            estimated_tokens = self.orchestrator.context_manager.estimate_conversation_tokens()
            tool_history = self.orchestrator.agent_state.tool_history
            config = self.orchestrator.session.config

            if CostGuard.check_limits(step_number, tool_history, estimated_tokens, config):
                event_data = CostGuard.build_limit_reached_event(step_number, tool_history, estimated_tokens, config)
                self.orchestrator._emit("cost_limit", event_data)

                answer = CostGuard.build_limit_summary(
                    objective,
                    tool_history,
                    self.orchestrator.agent_state.last_result
                )
                self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
                self.orchestrator.fail_task()
                return answer

            watchdog_reason = Watchdog.check_all(
                self.orchestrator._task_start_time,
                tool_history,
                config,
            )
            if watchdog_reason:
                event_data = Watchdog.build_watchdog_event(watchdog_reason, self.orchestrator._task_start_time)
                self.orchestrator._emit("watchdog", event_data)
                answer = Watchdog.build_watchdog_summary(tool_history, watchdog_reason)
                self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
                self.orchestrator.fail_task()
                return answer


            self.orchestrator.agent_state.plan_step += 1

            prompt = (
                f"Objetivo: {objective}\n"
                f"Ferramentas disponíveis:\n{self.orchestrator._build_tools_description(compact=True)}\n\n"
            )

            if self.orchestrator.agent_state.tool_history:
                prompt += "Histórico Recente de Ferramentas:\n"
                recent_history = self.orchestrator.agent_state.tool_history[-3:]
                for action in recent_history:
                    res_str = stringify(action['result'])
                    if len(res_str) > 1000:
                        res_str = res_str[:1000] + "\n... (truncado)"
                    prompt += f"- Usei: {action['tool']}\n  Com: {json.dumps(action['args'], ensure_ascii=False)}\n  Resultado: {res_str}\n"

            prompt += (
                "\nEscolha o PRÓXIMO passo. Responda APENAS com um JSON válido.\n"
                "Para usar uma ferramenta, use 'action': 'tool'. "
                "Para finalizar, use 'action': 'final'.\n"
                "Exemplo 1:\n{\"action\": \"tool\", \"tool\": \"file_reader\", \"args\": {\"file_path\": \"arquivo.py\"}}\n"
                "Exemplo 2:\n{\"action\": \"final\", \"answer\": \"O arquivo contém a função X.\"}\n"
            )

            decision = self.orchestrator.context_manager.ask_model(prompt, step_type="tool_decision",
                base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
                log_metric_callback=self.orchestrator._log_metric)

            action = decision.get("action")
            if action == "final":
                answer = decision.get("answer") or decision.get("message") or "Tarefa concluída."
                self.orchestrator._emit("final", {"answer": answer[:100]})

                # Check unread files
                unread = set()
                houve_leitura = False
                for step in self.orchestrator.agent_state.plan:
                    if step.get("tool") == "file_reader":
                        houve_leitura = True
                    elif step.get("tool") in ["file_writer", "python_executor"]:
                        target = step.get("args", {}).get("file_path") or step.get("args", {}).get("target")
                        if target and target not in self.orchestrator.agent_state.memory.state.get("file_summaries", {}):
                            unread.add(target)

                if unread and houve_leitura:
                    answer += "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
                    answer += ", ".join(sorted(unread))
                    answer += ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"

                self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
                return answer

            elif action == "tool":
                tool = decision.get("tool")
                args = decision.get("args", {})

                if not tool:
                    self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, "Ação 'tool' requer o campo 'tool'.")
                    continue

                valid, error_msg = validate_tool_args(tool, args, self.orchestrator.skills)
                if not valid:
                    self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, f"Argumentos inválidos: {error_msg}", tool, args)
                    self.orchestrator.context_manager.purge_stale_context()
                    continue

                if tool == "code_analyzer" and args.get("file_path"):
                    key = f"code_analyzer_{args['file_path']}"
                    tool_usage_count[key] = tool_usage_count.get(key, 0) + 1
                    if tool_usage_count[key] > 1:
                        self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, "Análise repetida bloqueada.", tool, args)
                        self.orchestrator.context_manager.purge_stale_context()
                        continue

                result = self.orchestrator._run_tool(tool, args)
                if result.get("ok"):
                    self.orchestrator._emit("tool_result", {"tool": tool, "success": True})
                    if tool in ["python_executor", "shell", "file_writer", "semantic_search"]:
                        self.orchestrator.context_manager.maybe_compress_context()

                    if tool == "file_writer":
                        self.orchestrator.workspace.show_diff(args.get("file_path", ""), args.get("content", ""))
                        lint_error = self.orchestrator.workspace.lint_check(args.get("file_path", ""))
                        if lint_error:
                            if self.orchestrator.verbose:
                                print(f"⚠️ Aviso de Linter em {args.get('file_path')}:\n{lint_error}")

                            if self.orchestrator.session.config.get("auto_test_and_correct", True):
                                if self.orchestrator.auto_coder.test_and_correct(args.get("file_path"), objective):
                                    continue
                                else:
                                    break
                else:
                    self.orchestrator._emit("tool_result", {"tool": tool, "success": False})
                    self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, result.get('error', 'Erro desconhecido'), tool, args)

            else:
                self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, f"Ação desconhecida: {action}")

        # Se quebrar o loop por falha:
        self.orchestrator.fail_task()
        return "A tarefa falhou e foi abortada."
