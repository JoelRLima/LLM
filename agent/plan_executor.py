import hashlib
from typing import Any, Dict, Optional

from agent.cost_guard import CostGuard
from agent.watchdog import Watchdog
from agent.parsers import stringify, validate_tool_args
from agent.replan import replan, ReplanContext


class PlanExecutor:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def execute(self, objective: str, tool_usage_count: Dict[str, int]) -> Optional[str]:
        result = None
        self.orchestrator.workspace.create_restore_point(self.orchestrator.agent_state.plan)

        # Detecção automática de dependências entre passos, baseada em arquivos
        # (ex.: um file_reader que lê um arquivo produzido por um file_writer
        # anterior). Calculada aqui e recalculada (via _rebuild_dependency_map)
        # sempre que o plano for alterado pelo replanner.
        self._rebuild_dependency_map()

        i = 0
        while i < len(self.orchestrator.agent_state.plan):
            step = self.orchestrator.agent_state.plan[i]
            self.orchestrator.agent_state.plan_step = i + 1
            limit_answer = self._check_cost_limits(i + 1)
            watchdog_answer = self._check_watchdog()
            if watchdog_answer:
                return watchdog_answer
            if limit_answer:
                return limit_answer

            tool = step["tool"]
            args = step["args"] if isinstance(step["args"], dict) else {}
            file_path = args.get("target") or args.get("file_path") or ""

            # Verifica dependências explícitas entre passos (ex.: um file_reader
            # que depende de um file_writer anterior ter criado o arquivo com
            # sucesso). Se alguma dependência falhou, pulamos este passo para
            # evitar erros em cascata.
            if not self._check_dependencies_ok(i):
                i += 1
                continue

            if not self._validate_step(i + 1, tool, args):
                # Validação falhou – pode ter retornado "replan"
                action = self.orchestrator._handle_step_failure(
                    i + 1, f"Schema: validação falhou para '{tool}'", tool, args
                )
                if action == "replan":
                    new_steps = self._attempt_replan(step, tool, args, objective, tool_usage_count)
                    if new_steps:
                        self._replace_current_step(i, new_steps)
                        continue
                i += 1
                continue

            if self._is_hard_blocked(i + 1, tool, args, file_path, tool_usage_count):
                i += 1
                continue

            if self._is_impossible_chunk(tool, args, file_path):
                i += 1
                continue

            if tool == "file_writer" and not args.get("content"):
                if not self._fill_generated_content(i + 1, tool, args, objective):
                    action = self.orchestrator._handle_step_failure(
                        i + 1, "Conteúdo não gerado para file_writer", tool, args
                    )
                    if action == "replan":
                        new_steps = self._attempt_replan(step, tool, args, objective, tool_usage_count)
                        if new_steps:
                            self._replace_current_step(i, new_steps)   # ✅ novo
                            continue
                    i += 1
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
                # _post_process_tool já chama _handle_step_failure internamente.
                # Se for "replan", tratamos aqui.
                action = self.orchestrator._handle_step_failure(
                    i + 1, f"Tool '{tool}' falhou: {result.get('error')}", tool, args
                )
                if action == "replan":
                    new_steps = self._attempt_replan(step, tool, args, objective, tool_usage_count)
                    if new_steps:
                        self._replace_current_step(i, new_steps)   # ✅ novo
                        continue
                i += 1
                continue

            edit_answer = self._maybe_finish_edit(objective)
            if edit_answer:
                return edit_answer
            i += 1

        if result is not None and not result.get("ok"):
            error_msg = result.get("error", "Erro desconhecido")
            return f"A tarefa não pôde ser concluída. Último erro: {error_msg}"
        return None

    def _build_dependency_map(self, plan: list) -> Dict[int, list]:
        """Detecta dependências implícitas entre passos, baseadas em arquivos.

        Um passo B depende de um passo A se A produz (file_writer, campo
        `file_path`) um arquivo que B posteriormente consome (file_reader ou
        code_analyzer, campo `file_path` ou `target`). O tool `grep` usa
        `path` para um diretório e é ignorado aqui, pois é um caso menos
        crítico (não aponta para um arquivo específico produzido no plano).

        Retorna um dicionário no formato:
            {índice_do_passo_consumidor: [índices_dos_passos_produtores]}

        Também popula `self._dependency_files`, um mapa auxiliar
        {(consumidor, produtor): file_path} usado em `_check_dependencies_ok`
        para localizar a entrada correspondente em `tool_history` mesmo que
        os índices do plano tenham sido alterados por injeção/substituição
        de passos feita pelo replanner.
        """
        dependencies: Dict[int, list] = {}
        self._dependency_files: Dict[tuple, str] = {}

        producers: Dict[str, int] = {}  # file_path -> índice do passo que o produz

        for idx, step in enumerate(plan):
            tool = step.get("tool")
            args = step.get("args") if isinstance(step.get("args"), dict) else {}

            if tool == "file_writer":
                fp = args.get("file_path")
                if fp:
                    # Passo mais recente que escreve nesse arquivo "vence"
                    # como produtor de referência para dependências futuras.
                    producers[fp] = idx
                continue

            if tool in ("file_reader", "code_analyzer"):
                fp = args.get("file_path") or args.get("target")
                if fp and fp in producers:
                    producer_idx = producers[fp]
                    if producer_idx != idx:
                        dependencies.setdefault(idx, []).append(producer_idx)
                        self._dependency_files[(idx, producer_idx)] = fp

            # tool == "grep": usa `path` (diretório), não tratado como
            # dependência crítica de arquivo único — ignorado por design.

        return dependencies

    def _check_dependencies_ok(self, i: int) -> bool:
        """Verifica se as dependências (baseadas em arquivo) do passo `i` já
        foram concluídas com sucesso antes de executá-lo.

        Para cada passo produtor do qual `i` depende, procura em
        `tool_history` a entrada correspondente (por tool `file_writer` +
        `file_path`, e não apenas por índice posicional, já que o plano pode
        ter sido reordenado/modificado pelo replanner) e confere se o
        resultado foi `ok: true`.

        Se alguma dependência não foi satisfeita (falhou ou nunca chegou a
        ser executada com sucesso), registra uma falha em `tool_history` e
        retorna False, sinalizando ao chamador para pular o passo atual sem
        executá-lo — evitando erros em cascata (ex.: ler um arquivo que o
        file_writer correspondente falhou em criar).

        # TODO (futuro): em vez de apenas pular o passo, poderíamos acionar
        # o replanner aqui (via self._attempt_replan) para tentar recuperar
        # a dependência falha antes de desistir do passo dependente. Por
        # ora, mantemos o comportamento simples de pular e seguir adiante.
        """
        deps = getattr(self, "_step_dependencies", {}).get(i)
        if not deps:
            return True

        plan = self.orchestrator.agent_state.plan
        tool_history = self.orchestrator.agent_state.tool_history

        for dep_idx in deps:
            file_path = self._dependency_files.get((i, dep_idx))
            dep_ok = False
            for h in tool_history:
                h_args = h.get("args") or {}
                if h.get("tool") == "file_writer" and h_args.get("file_path") == file_path:
                    # Considera a execução mais recente encontrada (caso o
                    # replanner tenha reexecutado o passo produtor).
                    dep_ok = bool(h.get("result", {}).get("ok"))

            if not dep_ok:
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Passo {i+1} depende do passo {dep_idx+1} que falhou. Pulando.")

                step = plan[i] if i < len(plan) else {}
                step_tool = step.get("tool", "unknown") if isinstance(step, dict) else "unknown"
                step_args = step.get("args", {}) if isinstance(step, dict) else {}
                error_result = {"ok": False, "error": f"Dependência falhou: passo {dep_idx+1}"}
                self.orchestrator.agent_state.record_tool_result(step_tool, step_args, error_result)
                return False

        return True

    def _attempt_replan(self, step: Dict[str, Any], tool: str, args: Dict[str, Any],
                        objective: str, tool_usage_count: Dict[str, int]) -> Optional[list]:
        """Chama o replanner e retorna uma lista de novos passos, ou None."""
        ctx = ReplanContext(
            task=objective,
            current_step=step,
            tool_history=self.orchestrator.agent_state.tool_history,
            last_exception=self.orchestrator.agent_state.last_result.get("error") if self.orchestrator.agent_state.last_result else None,
            last_tool_result=self.orchestrator.agent_state.last_result,
        )
        error_msg = self.orchestrator.agent_state.last_result.get("error", "") if self.orchestrator.agent_state.last_result else ""
        action = replan(ctx, error_msg, self.orchestrator)
        return action.steps if action else None

    def _inject_steps(self, position: int, new_steps: list) -> None:
        """Insere novos passos no plano a partir de position."""
        for j, new_step in enumerate(new_steps):
            self.orchestrator.agent_state.plan.insert(position + j, new_step)
        if self.orchestrator.verbose:
            print(f"[DEBUG] {len(new_steps)} passo(s) injetado(s) pelo replanner na posição {position}.")
        # O plano mudou de tamanho/ordem: os índices usados no mapa de
        # dependências ficam inválidos. Recalculamos o mapa a partir do
        # plano atualizado para manter a detecção de dependências correta.
        self._rebuild_dependency_map()

    def _replace_current_step(self, i: int, new_steps: list) -> None:
        """Remove o passo na posição i e insere os novos passos no lugar."""
        del self.orchestrator.agent_state.plan[i]
        for j, step in enumerate(new_steps):
            self.orchestrator.agent_state.plan.insert(i + j, step)
        if self.orchestrator.verbose:
            print(f"[DEBUG] Passo {i+1} substituído por {len(new_steps)} passo(s) do replanner.")
        # Mesma justificativa de _inject_steps: substituir um passo desloca
        # os índices de todos os passos seguintes, então o mapa de
        # dependências (construído antes do loop) precisa ser refeito.
        self._rebuild_dependency_map()

    def _rebuild_dependency_map(self) -> None:
        """Recalcula `self._step_dependencies` a partir do plano atual.

        Deve ser chamado sempre que o plano for alterado em tamanho ou
        ordem (injeção/substituição de passos pelo replanner), pois os
        índices usados como chave/valor no mapa de dependências deixam de
        corresponder aos passos originais após a mutação. A detecção em si
        (`_build_dependency_map`) é barata — é uma simples varredura linear
        do plano — então recalcular a cada mutação é seguro.

        Como a detecção é baseada em `file_path` (e não em identidade de
        objeto), passos já executados antes da mutação continuam sendo
        corretamente localizados em `tool_history` por `_check_dependencies_ok`,
        independentemente de seus novos índices.
        """
        self._step_dependencies = self._build_dependency_map(self.orchestrator.agent_state.plan)

    def _check_cost_limits(self, step_number: int) -> Optional[str]:
        estimated_tokens = self.orchestrator.context_manager.estimate_conversation_tokens()
        tool_history = self.orchestrator.agent_state.tool_history
        config = self.orchestrator.session.config

        if not CostGuard.check_limits(step_number, tool_history, estimated_tokens, config):
            return None

        event_data = CostGuard.build_limit_reached_event(step_number, tool_history, estimated_tokens, config)
        self.orchestrator._emit("cost_limit", event_data)

        answer = CostGuard.build_limit_summary(
            self.orchestrator.agent_state.objective,
            tool_history,
            self.orchestrator.agent_state.last_result
        )
        self.orchestrator.agent_state.conversation_history.append({"user": self.orchestrator.agent_state.objective, "agent": answer})
        self.orchestrator.fail_task()
        return answer

    def _validate_step(self, step_number: int, tool: str, args: Dict[str, Any]) -> bool:
        valid, error_msg = validate_tool_args(tool, args, self.orchestrator.skills)
        if not valid:
            action = self.orchestrator._handle_step_failure(step_number, f"Schema: {error_msg}", tool, args)
            if action == "continue":
                self.orchestrator._purge_stale_context()
                return False
            self.orchestrator.fail_task()
            return False

        if tool not in self.orchestrator.skills or (self.orchestrator.active_skills and tool not in self.orchestrator.active_skills):
            action = self.orchestrator._handle_step_failure(step_number, f"Tool '{tool}' não permitida", tool, args)
            if action == "continue":
                self.orchestrator._purge_stale_context()
                return False
            self.orchestrator.fail_task()
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
                # Marca o arquivo como totalmente lido para evitar que o LLM sugira novamente
                tool_usage_count[f"fully_read_{file_path}"] = 1
                tool_usage_count[f"fully_analyzed_{file_path}"] = 1

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

        if self.orchestrator.verbose:
            print(f"[DEBUG] Hard block silencioso: {hard_block_reason} em '{file_path}'")
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
        if generated:
            args["content"] = generated
            return True

        action = self.orchestrator._handle_step_failure(
            step_number, "Conteúdo não gerado para file_writer após 3 tentativas", tool, args
        )
        if action == "continue":
            self.orchestrator._purge_stale_context()
            return False
        self.orchestrator.fail_task()
        return False

    def _try_cache(self, tool: str, args: Dict[str, Any], file_path: str):
        if tool not in ("code_analyzer", "file_reader") or not file_path:
            return False, None

        if "start_line" in args or "end_line" in args:
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
        self.orchestrator.agent_state.record_tool_result(tool, args, result)
        return True, result

    def _post_process_tool(self, step_number: int, tool: str, args: Dict[str, Any], result: Dict[str, Any],
                           file_path: str, objective: str, tool_usage_count: Dict[str, int]) -> bool:
        if tool == "file_writer" and result.get("ok") and file_path.endswith(".py"):
            if self.orchestrator.verbose:
                print(f"🧪 [TEST] Iniciando ciclo teste-correção para '{file_path}'...")
            if not self.orchestrator._test_and_correct(file_path, objective):
                self.orchestrator.fail_task()
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
            self.orchestrator.fail_task()
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

    def _check_watchdog(self) -> Optional[str]:
        reason = Watchdog.check_all(
            self.orchestrator._task_start_time,
            self.orchestrator.agent_state.tool_history,
            self.orchestrator.session.config,
        )
        if not reason:
            return None

        event_data = Watchdog.build_watchdog_event(reason, self.orchestrator._task_start_time)
        self.orchestrator._emit("watchdog", event_data)

        answer = Watchdog.build_watchdog_summary(
            self.orchestrator.agent_state.tool_history, reason
        )
        self.orchestrator.agent_state.conversation_history.append(
            {"user": self.orchestrator.agent_state.objective, "agent": answer}
        )
        self.orchestrator.fail_task()
        return answer