from __future__ import annotations

import json
from typing import Any, Dict, List

TOKEN_KEYS = ("tokens", "total_tokens", "token_count")
DURATION_KEYS = ("duration_ms", "elapsed_ms", "latency_ms")
MODEL_CALL_TYPES = ("model_call", "llm_call", "completion")


def aggregate_metrics(entries: List[Dict[str, Any]], tools_called: int) -> Dict[str, int]:
    total_tokens = sum(_first_number(entry, TOKEN_KEYS) for entry in entries if isinstance(entry, dict))
    duration = sum(_first_number(entry, DURATION_KEYS) for entry in entries if isinstance(entry, dict))
    model_calls = sum(
        1 for entry in entries if isinstance(entry, dict)
        and (entry.get("type") in MODEL_CALL_TYPES or any(key in entry for key in TOKEN_KEYS))
    )
    return {
        "total_tokens": total_tokens,
        "total_duration_ms": duration,
        "model_calls": model_calls,
        "tools_called": tools_called,
    }


def _first_number(entry: Dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# Relatório da Tarefa {report.get('task_id', '')}", "",
        f"- **Objetivo:** {report.get('objective')}",
        f"- **Sucesso:** {'sim' if report.get('success') else 'não'}",
        f"- **Início:** {report.get('start_time')}", f"- **Fim:** {report.get('end_time')}", "",
    ]
    _append_metrics(lines, report.get("metrics") or {})
    _append_steps(lines, report.get("steps") or [])
    _append_replans(lines, report.get("replan_events") or [])
    _append_errors(lines, report.get("errors") or [])
    lines.extend(["## Resposta Final (prévia)", str(report.get("final_answer_preview", "")), ""])
    return "\n".join(lines)


def _append_metrics(lines: list[str], metrics: Dict[str, Any]) -> None:
    lines.extend([
        "## Métricas",
        f"- Total de tokens: {metrics.get('total_tokens', 0)}",
        f"- Duração total (ms): {metrics.get('total_duration_ms', 0)}",
        f"- Chamadas ao modelo: {metrics.get('model_calls', 0)}",
        f"- Ferramentas chamadas: {metrics.get('tools_called', 0)}", "",
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
