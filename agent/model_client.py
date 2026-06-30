"""
ModelClient: cliente HTTP para comunicação com o modelo LLM.

Extraído do ContextManager durante a refatoração de modularidade (Fix 5).
Responsável por enviar requisições, processar retries e coletar métricas.
"""
import datetime as dt
import time
from typing import Any, Dict, Optional

from agent.parsers import extract_json, extract_json_from_end
from logger import logger

FALLBACK_AGENT_MAX_TOKENS = 4096


class ModelClient:
    """Cliente HTTP para comunicação com o modelo LLM."""

    @staticmethod
    def request(
        session,
        payload: Dict[str, Any],
        step_type: str = "tool_decision",
        log_metric_callback=None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """
        Envia uma requisição ao modelo e retorna a decisão parseada.

        Args:
            session: instância de ChatSession (fornece send_non_streaming_request e config).
            payload: dicionário completo do payload da requisição.
            step_type: tipo do passo (plan, final, tool_decision).
            log_metric_callback: função para registrar métricas.
            verbose: se True, imprime logs de depuração.

        Returns:
            Dicionário com a decisão (action, tool, args, etc.) ou erro.
        """
        start_time = time.time()

        # Primeira tentativa
        try:
            response = session.send_non_streaming_request(payload)
        except Exception as e:
            logger.error(f"Erro na requisição ao modelo: {e}")
            response = f"Erro na requisição: {e}"

        if verbose:
            print(" ✓")
            print(f"[DEBUG] Resposta bruta: {str(response)[:300]}")

        decision = extract_json(response)
        if decision is None:
            decision = extract_json_from_end(response)

        # Métricas da primeira tentativa
        duration_ms = int((time.time() - start_time) * 1000)
        prompt_tokens = None
        completion_tokens = None
        if isinstance(response, dict):
            usage = response.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

        metric = {
            "timestamp": dt.datetime.now().isoformat(),
            "step_type": step_type,
            "tool": decision.get("tool") if isinstance(decision, dict) else None,
            "budget": payload.get("max_tokens"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "duration_ms": duration_ms,
            "success": (
                decision is not None
                and isinstance(decision, dict)
                and "action" in decision
            ),
        }
        if log_metric_callback:
            log_metric_callback(metric)

        if decision is not None:
            return decision

        # Retry com mais tokens
        if verbose:
            print(
                "[DEBUG] Resposta possivelmente truncada. Retentando com mais tokens...",
                end="",
                flush=True,
            )

        retry_payload = session.build_payload()
        retry_payload["max_tokens"] = session.config.get(
            "agent_max_tokens", FALLBACK_AGENT_MAX_TOKENS
        )
        retry_payload["stream"] = False
        try:
            retry_response = session.send_non_streaming_request(retry_payload)
        except Exception as e:
            logger.error(f"Erro no retry: {e}")
            retry_response = f"Erro na requisição: {e}"

        if verbose:
            print(" ✓")

        decision = extract_json(retry_response)
        if decision is not None:
            return decision

        return {
            "action": "error",
            "message": "Falha ao extrair JSON da resposta.",
            "raw_response": str(response),
        }