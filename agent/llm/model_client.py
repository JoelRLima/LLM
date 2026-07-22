"""Fachada de decisões estruturadas do planejador legado.

O transporte e o protocolo pertencem ao ``ModelGateway`` configurado na
``ChatSession``. Este módulo preserva parsing, retry e métricas exigidos pelo
executor antigo; workflows novos usam os contratos normalizados diretamente.
"""
import datetime as dt
import time
from collections.abc import Callable
from typing import Any, Dict, Optional, cast

from agent.parsers import extract_json, extract_json_from_end
from agent.runtime.logging import logger

FALLBACK_AGENT_MAX_TOKENS = 4096


class ModelClient:
    """Compatibilidade de parsing e retry para consumidores legados."""

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

    @classmethod
    def _send_with_grammar_fallback(
        cls,
        session: Any,
        request_payload: Dict[str, Any],
    ) -> Any:
        try:
            return session.send_non_streaming_request(request_payload)
        except Exception as exc:
            can_fallback = (
                "grammar" in request_payload
                and cls._backend_supports_grammar is None
                and cls._is_grammar_unsupported_error(exc)
            )
            if not can_fallback:
                logger.error(f"Erro na requisição ao modelo: {exc}")
                return f"Erro na requisição: {exc}"
        cls._backend_supports_grammar = False
        fallback_payload = dict(request_payload)
        fallback_payload.pop("grammar", None)
        try:
            return session.send_non_streaming_request(fallback_payload)
        except Exception as exc:
            logger.error(f"Erro na requisição ao modelo: {exc}")
            return f"Erro na requisição: {exc}"

    @staticmethod
    def _extract_decision(response: Any) -> Optional[Dict[str, Any]]:
        decision = extract_json(response) or extract_json_from_end(response)
        return cast(Optional[Dict[str, Any]], decision)

    @staticmethod
    def _record_metric(
        callback: Callable[[Dict[str, Any]], None] | None,
        response: Any,
        decision: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
        step_type: str,
        started_at: float,
    ) -> None:
        if callback is None:
            return
        usage = response.get("usage") or {} if isinstance(response, dict) else {}
        callback({
            "timestamp": dt.datetime.now().isoformat(),
            "step_type": step_type,
            "tool": decision.get("tool") if decision else None,
            "budget": payload.get("max_tokens"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "duration_ms": int((time.time() - started_at) * 1000),
            "success": bool(decision and "action" in decision),
        })

    @staticmethod
    def _retry(session: Any, verbose: bool) -> Optional[Dict[str, Any]]:
        retry_payload = session.build_payload()
        hardware_profile = getattr(session, "hardware_profile", None)
        retry_payload["max_tokens"] = session.config.get("agent_max_tokens") or min(
            FALLBACK_AGENT_MAX_TOKENS,
            hardware_profile.default_output_tokens if hardware_profile is not None else FALLBACK_AGENT_MAX_TOKENS,
        )
        retry_payload["stream"] = False
        try:
            response = session.send_non_streaming_request(retry_payload)
        except Exception as exc:
            logger.error(f"Erro no retry: {exc}")
            return None
        if verbose:
            print(" ✓")
        return ModelClient._extract_decision(response)

    @classmethod
    def request(
        cls,
        session: Any,
        payload: Dict[str, Any],
        step_type: str = "tool_decision",
        log_metric_callback: Callable[[Dict[str, Any]], None] | None = None,
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
        started_at = time.time()
        request_payload = dict(payload)
        if grammar is not None and cls._backend_supports_grammar is not False:
            request_payload["grammar"] = grammar
        if verbose:
            has_grammar = "grammar" in request_payload
            print(f"[DEBUG] GBNF na requisição: {'SIM' if has_grammar else 'NÃO'} (step_type={step_type})")
        response = cls._send_with_grammar_fallback(session, request_payload)
        if verbose:
            print(" ✓")
            print(f"[DEBUG] Resposta bruta: {str(response)[:300]}")
        decision = cls._extract_decision(response)
        cls._record_metric(log_metric_callback, response, decision, payload, step_type, started_at)
        if decision is not None:
            return decision
        if verbose:
            print(
                "[DEBUG] Resposta possivelmente truncada. Retentando com mais tokens...",
                end="",
                flush=True,
            )

        decision = cls._retry(session, verbose)
        if decision:
            return decision
        return {
            "action": "error",
            "message": "Falha ao extrair JSON da resposta.",
            "raw_response": str(response),
        }
