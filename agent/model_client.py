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

    # None = suporte a GBNF ainda não testado neste processo/sessão.
    # True/False = resultado já conhecido (evita novas tentativas de fallback).
    _backend_supports_grammar: Optional[bool] = None

    @staticmethod
    def _is_grammar_unsupported_error(error: Exception) -> bool:
        """
        Verifica se a exceção capturada indica que o backend não suporta
        o parâmetro `grammar` (GBNF) — e não um erro genérico (500,
        timeout, falha de conexão, etc.).

        Args:
            error: exceção capturada ao enviar a requisição.

        Returns:
            True se a condição indicar claramente falta de suporte ao
            parâmetro `grammar`.
        """
        response = getattr(error, "response", None)
        if response is None:
            return False

        status_code = getattr(response, "status_code", None)
        if status_code != 400:
            return False

        try:
            body_text = response.text or ""
        except Exception:
            body_text = ""

        return "grammar" in body_text.lower()

    @staticmethod
    def request(
        session,
        payload: Dict[str, Any],
        step_type: str = "tool_decision",
        log_metric_callback=None,
        verbose: bool = False,
        grammar: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Envia uma requisição ao modelo e retorna a decisão parseada.

        Args:
            session: instância de ChatSession (fornece send_non_streaming_request e config).
            payload: dicionário completo do payload da requisição.
            step_type: tipo do passo (plan, final, tool_decision).
            log_metric_callback: função para registrar métricas.
            verbose: se True, imprime logs de depuração.
            grammar: gramática GBNF a ser enviada (campo "grammar" do payload),
                ou None para não usar gramática. Ignorada se o backend já
                tiver sinalizado que não suporta o parâmetro.

        Returns:
            Dicionário com a decisão (action, tool, args, etc.) ou erro.
        """
        start_time = time.time()

        # Monta o payload da primeira tentativa, incluindo a gramática
        # apenas se ela foi fornecida e o backend ainda não sinalizou
        # falta de suporte.
        request_payload = dict(payload)
        if grammar is not None and ModelClient._backend_supports_grammar is not False:
            request_payload["grammar"] = grammar

        # Log de diagnóstico
        if verbose:
            has_grammar = "grammar" in request_payload
            print(f"[DEBUG] GBNF na requisição: {'SIM' if has_grammar else 'NÃO'} (step_type={step_type})")

        # Primeira tentativa
        try:
            response = session.send_non_streaming_request(request_payload)
        except Exception as e:
            if (
                "grammar" in request_payload
                and ModelClient._backend_supports_grammar is None
                and ModelClient._is_grammar_unsupported_error(e)
            ):
                logger.warning(
                    "[GRAMMAR] Backend does not support GBNF. Disabling for this session."
                )
                ModelClient._backend_supports_grammar = False

                fallback_payload = dict(request_payload)
                fallback_payload.pop("grammar", None)
                try:
                    response = session.send_non_streaming_request(fallback_payload)
                    request_payload = fallback_payload
                except Exception as e2:
                    logger.error(f"Erro na requisição ao modelo: {e2}")
                    response = f"Erro na requisição: {e2}"
            else:
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