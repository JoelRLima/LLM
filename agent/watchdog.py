"""Watchdog: monitoramento determinístico da execução do agente.

Componente isolado (mesmo padrão de `cost_guard.py`): fonte única de
verdade para detectar três condições de risco durante a execução de uma
tarefa, sem nenhuma chamada adicional ao LLM:

1. Timeout global da tarefa — soma do tempo de parede de todos os passos
   (complementa o timeout por-subprocesso já existente em `python_executor`
   e `shell`, que não protege contra uma tarefa "viva" porém lenta demais
   ao somar muitos passos).
2. Loop sem progresso — mesma ferramenta chamada repetidamente com os
   mesmos argumentos e resultado idêntico.
3. Falhas consecutivas com o mesmo erro — sinal de que o agente está preso
   tentando repetidamente a mesma abordagem inválida.

`PlanExecutor` e `ReactiveLoop` chamam `Watchdog.check_all(...)` a cada
passo, do mesmo jeito que já chamam `CostGuard.check_limits(...)`.
"""
import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from agent.parsers import stringify
from agent.runtime.limits import (
    default_runtime_limit,
    runtime_limit_values,
)
from agent.tools.result_completeness import legacy_result_successful

# Compatibility constants for historical imports; the typed runtime-limit
# owner supplies configured values at each boundary below.
DEFAULT_MAX_TASK_WALL_SECONDS = default_runtime_limit("max_task_wall_seconds")
DEFAULT_MAX_REPEATED_NO_PROGRESS = default_runtime_limit("max_repeated_no_progress")
DEFAULT_MAX_CONSECUTIVE_SAME_ERROR = default_runtime_limit("max_consecutive_same_error")


class Watchdog:
    """Monitora a execução de uma tarefa do agente e decide quando abortar."""

    # ------------------------------------------------------------------
    # Controle de tempo
    # ------------------------------------------------------------------

    @staticmethod
    def start_task() -> float:
        """Retorna o timestamp (monotonic) de início da tarefa."""
        return time.monotonic()

    @staticmethod
    def check_global_timeout(start_time: Optional[float], config: Dict[str, Any]) -> Optional[str]:
        if start_time is None:
            return None
        max_seconds = runtime_limit_values(config)["max_task_wall_seconds"]
        elapsed = time.monotonic() - start_time
        if elapsed > max_seconds:
            return f"Timeout global da tarefa atingido ({elapsed:.1f}s > {max_seconds}s)."
        return None

    # ------------------------------------------------------------------
    # Detecção de loop sem progresso
    # ------------------------------------------------------------------

    @staticmethod
    def _signature(tool: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
        """Assinatura estável de (ferramenta, args, resultado) para detectar repetição exata."""
        # Arguments are tool payload; only root result metadata is ephemeral.
        normalized_result = Watchdog._without_ephemeral_ids(result)
        raw = json.dumps(
            {"tool": tool, "args": args, "result": normalized_result},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _without_ephemeral_ids(value: Any) -> Any:
        """Remove only root framework IDs; nested tool data is semantic."""
        if isinstance(value, dict):
            return {
                key: item
                for key, item in value.items()
                if key not in {"invocation_id", "attempt_id"}
            }
        return value

    @staticmethod
    def check_no_progress_loop(tool_history: List[Dict[str, Any]], config: Dict[str, Any]) -> Optional[str]:
        """
        Detecta repetição exata de (tool, args, result) nos últimos passos —
        sinal de que o agente está "girando" sem avançar, mesmo que cada
        chamada individualmente pareça válida (não é coberto pelo hard
        block do PlanExecutor, que só olha tool+file_path, nem pelo
        CostGuard, que só conta volume).
        """
        max_repeats = runtime_limit_values(config)["max_repeated_no_progress"]
        if len(tool_history) < max_repeats:
            return None

        recent = tool_history[-max_repeats:]
        signatures = {
            Watchdog._signature(h.get("tool", ""), h.get("args", {}), h.get("result", {}))
            for h in recent
        }
        if len(signatures) == 1:
            tool_name = recent[-1].get("tool", "?")
            return (
                f"Loop sem progresso detectado: a ferramenta '{tool_name}' foi chamada "
                f"{max_repeats} vezes seguidas com os mesmos argumentos e resultado idêntico."
            )
        return None

    # ------------------------------------------------------------------
    # Detecção de falhas repetidas
    # ------------------------------------------------------------------

    @staticmethod
    def check_consecutive_same_error(tool_history: List[Dict[str, Any]], config: Dict[str, Any]) -> Optional[str]:
        """
        Detecta N falhas seguidas com a mesma mensagem de erro, mesmo que os
        argumentos variem entre tentativas — sinal de que o agente está
        preso tentando a mesma abordagem inválida repetidamente.
        """
        max_same_error = runtime_limit_values(config)["max_consecutive_same_error"]
        if len(tool_history) < max_same_error:
            return None

        recent = tool_history[-max_same_error:]
        errors = []
        for h in recent:
            result = h.get("result") or {}
            if legacy_result_successful(
                result,
                allow_bare_ok=True,
            ):
                return None  # houve sucesso recente, não é uma sequência de falhas
            errors.append((result.get("error") or "").strip())

        if errors and len(set(errors)) == 1 and errors[0]:
            return (
                f"{max_same_error} falhas consecutivas com o mesmo erro: '{errors[0][:200]}'. "
                "O agente parece preso na mesma abordagem inválida."
            )
        return None

    # ------------------------------------------------------------------
    # Ponto de entrada único
    # ------------------------------------------------------------------

    @classmethod
    def check_all(
        cls,
        start_time: Optional[float],
        tool_history: List[Dict[str, Any]],
        config: Dict[str, Any],
    ) -> Optional[str]:
        """
        Executa todas as checagens determinísticas, na ordem. Retorna o
        motivo da primeira violação encontrada, ou None se tudo estiver ok.
        """
        return (
            cls.check_global_timeout(start_time, config)
            or cls.check_no_progress_loop(tool_history, config)
            or cls.check_consecutive_same_error(tool_history, config)
        )

    # ------------------------------------------------------------------
    # Telemetria / mensagem ao usuário
    # ------------------------------------------------------------------

    @staticmethod
    def build_watchdog_event(reason: str, start_time: Optional[float]) -> Dict[str, Any]:
        elapsed = round(time.monotonic() - start_time, 2) if start_time is not None else None
        return {
            "reason": reason,
            "elapsed_seconds": elapsed,
        }

    @staticmethod
    def build_watchdog_summary(tool_history: List[Dict[str, Any]], reason: str) -> str:
        summary_parts = [f"Motivo: {reason}"]
        if tool_history:
            tools_used = sorted(set(h.get("tool", "?") for h in tool_history))
            summary_parts.append(f"Ferramentas usadas: {', '.join(tools_used)}")
            summary_parts.append(f"Último resultado: {stringify(tool_history[-1].get('result'))[:500]}")
        body = "\n".join(summary_parts)
        return (
            "A tarefa foi interrompida pelo watchdog de execução por segurança/robustez.\n"
            f"{body}"
        )
