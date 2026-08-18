from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict, List

TOKEN_KEYS = ("tokens", "total_tokens", "token_count", "prompt_tokens", "completion_tokens")
DURATION_KEYS = ("duration_ms", "elapsed_ms", "latency_ms")
MODEL_CALL_TYPES = ("model_call", "llm_call", "completion")


def aggregate_metrics(
    entries: List[Dict[str, Any]],
    tools_called: int | None = None,
    *,
    tool_calls: int | None = None,
    history_records: int | None = None,
    budget_snapshot: Any = None,
) -> Dict[str, Any]:
    valid_entries = [entry for entry in entries if isinstance(entry, dict)]
    model_entries = [entry for entry in valid_entries if _metric_type(entry) == "model_call"]
    run_entries = [entry for entry in valid_entries if _metric_type(entry) == "run"]
    historical_entries = [
        entry for entry in valid_entries if _metric_type(entry) == "model_metadata"
    ]
    historical_fallback = not model_entries and any(
        _has_number(entry, TOKEN_KEYS) for entry in historical_entries
    )
    token_entries = historical_entries if historical_fallback else model_entries
    token_values = [
        _token_count(entry) for entry in token_entries if _has_number(entry, TOKEN_KEYS)
    ]
    complete_flags = [_entry_usage_complete(entry) for entry in model_entries]
    token_usage_complete = bool(model_entries) and all(complete_flags)
    reported_input_tokens = sum(
        _first_number(entry, ("input_tokens", "prompt_tokens"))
        for entry in model_entries
    )
    reported_output_tokens = sum(
        _first_number(entry, ("output_tokens", "completion_tokens"))
        for entry in model_entries
    )
    reported_total_tokens = sum(
        _first_number(entry, ("total_tokens",)) for entry in model_entries
        if _has_number(entry, ("total_tokens",))
    )
    estimated_tokens = sum(
        _first_number(entry, ("estimated_tokens",)) for entry in model_entries
    )
    accounted_tokens = sum(_entry_accounted_tokens(entry) for entry in model_entries)
    all_model_totals = bool(model_entries) and all(
        _has_number(entry, ("total_tokens",)) for entry in model_entries
    )
    derived_total_tokens = sum(_complete_token_count(entry) for entry in model_entries)
    if historical_fallback:
        total_tokens: int | None = sum(token_values) if token_values else None
        reported_tokens: int | None = total_tokens
        token_usage_complete = False
    elif token_usage_complete:
        total_tokens = (
            reported_total_tokens if all_model_totals else derived_total_tokens
        )
        reported_tokens = total_tokens
    else:
        total_tokens = None
        reported_tokens = reported_total_tokens if reported_total_tokens else None

    actual_tool_calls = tool_calls if tool_calls is not None else tools_called
    actual_tool_calls = int(actual_tool_calls or 0)
    history_records = int(history_records or 0)
    model_calls = (
        _snapshot_number(budget_snapshot, "model_calls", len(model_entries))
        if budget_snapshot is not None
        else len(model_entries)
    )
    snapshot_total_calls = (
        _snapshot_number(budget_snapshot, "model_calls_with_reported_total", 0)
        if budget_snapshot is not None
        else 0
    )

    if budget_snapshot is not None:
        actual_tool_calls = _snapshot_number(
            budget_snapshot, "tool_calls", actual_tool_calls
        )
        reported_input_tokens = _snapshot_number(
            budget_snapshot, "reported_input_tokens", reported_input_tokens
        )
        reported_output_tokens = _snapshot_number(
            budget_snapshot, "reported_output_tokens", reported_output_tokens
        )
        reported_total_tokens = _snapshot_number(
            budget_snapshot, "reported_total_tokens", reported_total_tokens
        )
        estimated_tokens = _snapshot_number(
            budget_snapshot, "estimated_tokens", estimated_tokens
        )
        accounted_tokens = _snapshot_number(
            budget_snapshot, "accounted_tokens", accounted_tokens
        )
        token_usage_complete = bool(
            _snapshot_value(budget_snapshot, "token_usage_complete", token_usage_complete)
        )
        if model_calls == 0:
            total_tokens = 0
            reported_tokens = 0
        elif token_usage_complete:
            total_tokens = (
                reported_total_tokens
                if all_model_totals
                or (not model_entries and snapshot_total_calls == model_calls)
                else derived_total_tokens
                if model_entries
                else reported_input_tokens + reported_output_tokens
            )
            reported_tokens = total_tokens
        else:
            total_tokens = None
            reported_tokens = reported_total_tokens or None

    duration_entries = run_entries or model_entries
    duration = sum(_first_number(entry, DURATION_KEYS) for entry in duration_entries)
    return {
        "total_tokens": total_tokens,
        "reported_tokens": reported_tokens,
        "reported_input_tokens": reported_input_tokens,
        "reported_output_tokens": reported_output_tokens,
        "reported_total_tokens": reported_total_tokens,
        "estimated_tokens": estimated_tokens,
        "accounted_tokens": accounted_tokens,
        "token_usage_complete": token_usage_complete,
        "total_duration_ms": duration,
        "duration_available": bool(
            duration_entries and any(_has_number(entry, DURATION_KEYS) for entry in duration_entries)
        ),
        "model_calls": model_calls,
        "run_calls": len(run_entries),
        "tool_calls": actual_tool_calls,
        "tools_called": actual_tool_calls,
        "history_records": history_records,
        "token_usage_available": bool(token_usage_complete)
        or bool(token_values)
        or bool(reported_input_tokens or reported_output_tokens),
        "historical_token_fallback": historical_fallback,
    }


def _first_number(entry: Dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def _has_number(entry: Dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(
        isinstance(entry.get(key), (int, float)) and not isinstance(entry.get(key), bool)
        for key in keys
    )


def _entry_usage_complete(entry: Dict[str, Any]) -> bool:
    explicit = entry.get("token_usage_complete")
    if isinstance(explicit, bool):
        return explicit
    if _has_number(entry, ("input_tokens", "prompt_tokens")) and _has_number(
        entry, ("output_tokens", "completion_tokens")
    ):
        return True
    return _has_number(entry, ("total_tokens", "tokens", "token_count"))


def _entry_accounted_tokens(entry: Dict[str, Any]) -> int:
    if _has_number(entry, ("accounted_tokens",)):
        return _first_number(entry, ("accounted_tokens",))
    if _entry_usage_complete(entry):
        return _complete_token_count(entry)
    return _first_number(entry, ("estimated_tokens",))


def _complete_token_count(entry: Dict[str, Any]) -> int:
    if _has_number(entry, ("total_tokens",)):
        return _first_number(entry, ("total_tokens",))
    input_tokens = _first_number(entry, ("input_tokens", "prompt_tokens"))
    output_tokens = _first_number(entry, ("output_tokens", "completion_tokens"))
    if _has_number(entry, ("input_tokens", "prompt_tokens")) and _has_number(
        entry, ("output_tokens", "completion_tokens")
    ):
        return input_tokens + output_tokens
    return _token_count(entry)


def _snapshot_value(snapshot: Any, name: str, default: Any) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def _snapshot_number(snapshot: Any, name: str, default: int) -> int:
    value = _snapshot_value(snapshot, name, default)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _token_count(entry: Dict[str, Any]) -> int:
    for key in ("total_tokens", "tokens", "token_count"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return sum(
        int(entry[key])
        for key in ("prompt_tokens", "completion_tokens")
        if isinstance(entry.get(key), (int, float)) and not isinstance(entry.get(key), bool)
    )


def _metric_type(entry: Dict[str, Any]) -> str:
    value = entry.get("type") or entry.get("metric_type") or ""
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    raw_receipt = report.get("receipt")
    receipt = raw_receipt if isinstance(raw_receipt, dict) else {}
    raw_cause = receipt.get("error")
    cause = raw_cause if isinstance(raw_cause, dict) else {}
    reason_code = cause.get("code") or report.get("error")
    lines = [
        f"# Relatório da Tarefa {report.get('task_id', '')}", "",
        f"- **Objetivo:** {report.get('objective')}",
        f"- **Sucesso:** {'sim' if report.get('success') else 'não'}",
        f"- **Início:** {report.get('start_time')}", f"- **Fim:** {report.get('end_time')}", "",
    ]
    lines.insert(3, f"- **Status operacional:** {report.get('status', 'unverified')}")
    lines.insert(5, f"- **Codigo de resultado:** {reason_code or 'nenhum'}")
    _append_metrics(lines, report.get("metrics") or {})
    _append_steps(lines, report.get("steps") or [])
    _append_replans(lines, report.get("replan_events") or [])
    _append_errors(lines, report.get("errors") or [])
    lines.extend(["## Resposta Final (prévia)", str(report.get("final_answer_preview", "")), ""])
    return "\n".join(lines)


def _append_metrics(lines: list[str], metrics: Dict[str, Any]) -> None:
    total_tokens = metrics.get("total_tokens")
    duration = metrics.get("total_duration_ms") if metrics.get("duration_available", True) else None
    lines.extend([
        "## Métricas",
        f"- Total de tokens: {total_tokens if total_tokens is not None else 'desconhecido'}",
        f"- Tokens reportados pelo provedor: {metrics.get('reported_tokens') if metrics.get('reported_tokens') is not None else 'desconhecidos'}",
        f"- Tokens estimados para contabilização: {metrics.get('estimated_tokens', 0)}",
        f"- Tokens contabilizados: {metrics.get('accounted_tokens', 0)}",
        f"- Uso de tokens completo: {'sim' if metrics.get('token_usage_complete') else 'não'}",
        f"- Duração total (ms): {duration if duration is not None else 'desconhecida'}",
        f"- Chamadas ao modelo: {metrics.get('model_calls', 0)}",
        f"- Chamadas reais de ferramentas: {metrics.get('tool_calls', metrics.get('tools_called', 0))}",
        f"- Registros históricos de ferramentas: {metrics.get('history_records', 0)}", "",
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
