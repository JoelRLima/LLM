"""CostGuard: centraliza a política de limites de custo de execução do agente.

Anteriormente, a verificação de custo (max_steps, max_tokens, max_tool_calls) e a
montagem da mensagem de interrupção estavam duplicadas em PlanExecutor e ReactiveLoop,
com valores de fallback divergentes. Este módulo é a única fonte de verdade para essas regras.
"""
from typing import Any, Dict, List

from agent.runtime.config import DEFAULT_COST_WATCHDOG

DEFAULT_MAX_TASK_STEPS = DEFAULT_COST_WATCHDOG["max_task_steps"]
DEFAULT_MAX_TASK_TOKENS = DEFAULT_COST_WATCHDOG["max_task_tokens"]
DEFAULT_MAX_TASK_TOOL_CALLS = DEFAULT_COST_WATCHDOG["max_task_tool_calls"]


class CostGuard:
    """Verifica se os limites de custo de uma tarefa foram atingidos."""

    @staticmethod
    def check_limits(
        plan_step: int,
        tool_history: List[Dict[str, Any]],
        estimated_tokens: int,
        config: Dict[str, Any],
    ) -> bool:
        """Retorna True se algum limite de custo foi ultrapassado.

        Args:
            plan_step: Número do passo atual (1-indexed).
            tool_history: Lista de chamadas de ferramentas já executadas.
            estimated_tokens: Estimativa de tokens consumidos no contexto atual.
            config: Dicionário de configuração com as chaves max_task_*.
        """
        max_steps = config.get("max_task_steps", DEFAULT_MAX_TASK_STEPS)
        max_tokens = config.get("max_task_tokens", DEFAULT_MAX_TASK_TOKENS)
        max_tool_calls = config.get("max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS)

        return bool(
            plan_step > max_steps
            or estimated_tokens > max_tokens
            or len(tool_history) > max_tool_calls
        )

    @staticmethod
    def build_limit_reached_event(
        plan_step: int,
        tool_history: List[Dict[str, Any]],
        estimated_tokens: int,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Monta o payload do evento de telemetria 'cost_limit'."""
        max_steps = config.get("max_task_steps", DEFAULT_MAX_TASK_STEPS)
        max_tokens = config.get("max_task_tokens", DEFAULT_MAX_TASK_TOKENS)
        max_tool_calls = config.get("max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS)
        return {
            "reason": "Limite de custo da tarefa atingido",
            "steps": plan_step,
            "max_steps": max_steps,
            "estimated_tokens": estimated_tokens,
            "max_tokens": max_tokens,
            "tool_calls": len(tool_history),
            "max_tool_calls": max_tool_calls,
        }

    @staticmethod
    def build_limit_summary(
        objective: str,
        tool_history: List[Dict[str, Any]],
        last_result: Any,
    ) -> str:
        """Monta a mensagem de resposta ao usuário quando a tarefa é interrompida por custo.

        Args:
            objective: O objetivo da tarefa interrompida.
            tool_history: Histórico de chamadas de ferramentas executadas.
            last_result: Último resultado de ferramenta disponível.
        """
        from agent.parsers import stringify

        summary_parts = []
        if tool_history:
            tools_used = set(h["tool"] for h in tool_history)
            summary_parts.append(f"Ferramentas usadas: {', '.join(sorted(tools_used))}")
            summary_parts.append(f"Último resultado: {stringify(last_result)[:500]}")

        body = "\n".join(summary_parts) if summary_parts else "Nenhuma ferramenta foi executada."
        return (
            "A tarefa foi interrompida porque atingiu o limite de custo definido. "
            f"Resumo do que foi feito:\n{body}"
        )
