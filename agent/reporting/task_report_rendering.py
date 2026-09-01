from __future__ import annotations

import json
from typing import Any, Dict, List, cast

from agent.reporting.metrics import (
    DURATION_KEYS,
    MODEL_CALL_TYPES,
    TOKEN_KEYS,
    project_run_metrics,
)


def aggregate_metrics(
    entries: List[Dict[str, Any]],
    tools_called: int | None = None,
    *,
    tool_calls: int | None = None,
    history_records: int | None = None,
    budget_snapshot: Any = None,
    snapshot: Any = None,
) -> Dict[str, Any]:
    """Aggregate the canonical task-report snapshot into a mapping."""

    if snapshot is None:
        return project_run_metrics(
            entries,
            tools_called,
            tool_calls=tool_calls,
            history_records=history_records,
            budget_snapshot=budget_snapshot,
        ).to_dict()
    return cast(Dict[str, Any], snapshot.metrics.to_dict())


def render_markdown(report: Dict[str, Any]) -> str:
    raw_receipt = report.get("receipt")
    receipt = raw_receipt if isinstance(raw_receipt, dict) else {}
    raw_cause = receipt.get("error")
    cause = raw_cause if isinstance(raw_cause, dict) else {}
    reason_code = cause.get("code") or report.get("error")
    lines = [
        f"# Relatório da Tarefa {report.get('report_id', '')}",
        "",
        f"- **Objetivo:** {report.get('objective')}",
        f"- **Sucesso:** {'sim' if report.get('success') else 'não'}",
        f"- **Início:** {report.get('start_time')}",
        f"- **Fim:** {report.get('end_time')}",
        "",
    ]
    lines.insert(3, f"- **Status operacional:** {report.get('status', 'unverified')}")
    lines.insert(5, f"- **Código de resultado:** {reason_code or 'nenhum'}")
    _append_metrics(lines, report.get("metrics") or {})
    _append_steps(lines, report.get("steps") or [])
    _append_replans(lines, report.get("replan_events") or [])
    _append_errors(lines, report.get("errors") or [])
    lines.extend(["## Resposta Final (prévia)", str(report.get("final_answer_preview", "")), ""])
    return "\n".join(lines)


def _append_metrics(lines: list[str], metrics: Dict[str, Any]) -> None:
    total_tokens = metrics.get("total_tokens")
    duration = metrics.get("total_duration_ms") if metrics.get("duration_available", True) else None
    measurement = str(metrics.get("token_measurement", "unavailable"))
    measurement_labels = {
        "provider_reported": "exata, reportada pelo provedor",
        "derived": "derivada de partes completas reportadas pelo provedor",
        "estimated": "estimada; não é um total exato",
        "unavailable": "indisponível",
    }
    lines.extend([
        "## Métricas",
        f"- Total de tokens: {total_tokens if total_tokens is not None else 'desconhecido'}",
        f"- Medição de tokens: {measurement_labels.get(measurement, measurement)}",
        f"- Consumo total reportado pelo provedor: {metrics.get('reported_total_tokens') if metrics.get('token_measurement') == 'provider_reported' else 'não reportado'}",
        f"- Tokens reportados pelo provedor: {metrics.get('reported_tokens') if metrics.get('reported_tokens') is not None else 'desconhecidos'}",
        f"- Input medido antes do dispatch: {metrics.get('request_input_tokens') if metrics.get('request_input_measurement_available') else 'indisponível'}",
        f"- Proveniência do input pré-dispatch: {metrics.get('request_input_measurement_source', 'unavailable')}",
        f"- Input pré-dispatch exato: {'sim' if metrics.get('request_input_measurement_exact') is True else 'não' if metrics.get('request_input_measurement_available') else 'indisponível'}",
        f"- Delta input (provedor - pré-dispatch): {metrics.get('request_input_token_delta') if metrics.get('request_input_token_delta') is not None else 'não aplicável'}",
        f"- Input consistente: {'sim' if metrics.get('request_input_token_consistent') is True else 'não' if metrics.get('request_input_token_consistent') is False else 'não aplicável'}",
        f"- Tokens derivados: {metrics.get('derived_tokens') if metrics.get('derived_tokens') is not None else 'não aplicável'}",
        f"- Tokens reservados ativos: {metrics.get('reserved_tokens', 0)}",
        f"- Tokens de reserva observados: {metrics.get('reserved_allowance_tokens', metrics.get('reserved_tokens', 0))}",
        f"- Reserva/allowance observada (não é consumo): {metrics.get('reserved_allowance_tokens', metrics.get('reserved_tokens', 0))}",
        f"- Tokens estimados para contabilização (fallback): {metrics.get('estimated_tokens', 0)}",
        f"- Tokens contabilizados pelo ledger: {metrics.get('accounted_tokens', 0)}",
        f"- Uso de tokens completo: {'sim' if metrics.get('token_usage_complete') else 'não'}",
        f"- Duração total (ms): {duration if duration is not None else 'desconhecida'}",
        f"- Chamadas ao modelo: {metrics.get('model_calls', 0)}",
        f"- Chamadas reais de ferramentas: {metrics.get('tool_calls', metrics.get('tools_called', 0))}",
        f"- Registros históricos de ferramentas: {metrics.get('history_records', 0)}",
        "",
    ])


def _append_steps(lines: list[str], steps: List[Dict[str, Any]]) -> None:
    lines.append("## Passos")
    if not steps:
        lines.extend(["_Nenhum passo registrado._", ""])
        return
    for step in steps:
        result = step.get("result") or {}
        lines.append(f"### {step.get('index')}. {step.get('tool')} {'ok' if result.get('ok') else 'falha'}")
        lines.append(f"- Args: `{json.dumps(step.get('args', {}), ensure_ascii=False, default=str)}`")
        if result.get("error"):
            lines.append(f"- Erro: {result['error']}")
        lines.append(f"- Resultado (resumo): {result.get('data_summary', '')}")
        if "cache_hit" in step:
            lines.append(f"- Cache hit: {step['cache_hit']}")
        lines.append("")


def _append_replans(lines: list[str], events: List[Dict[str, Any]]) -> None:
    if not events:
        return
    lines.append("## Eventos de Replanejamento")
    for event in events:
        lines.append(
            f"- Passo {event.get('original_step')}: {event.get('error')} -> "
            f"{event.get('strategy')} ({event.get('replacement_steps')} novos passos)"
        )
    lines.append("")


def _append_errors(lines: list[str], errors: List[str]) -> None:
    if errors:
        lines.extend(["## Erros", *(f"- {error}" for error in errors), ""])


__all__ = [
    "DURATION_KEYS",
    "MODEL_CALL_TYPES",
    "TOKEN_KEYS",
    "aggregate_metrics",
    "render_markdown",
]
