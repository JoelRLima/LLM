"""Presentation-only rendering for one interactive CLI turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

_DIAGNOSTIC_LABELS = ("OFF", "DIAG", "VERBOSE")
_THINKING_LABELS = {0: "OFF", 512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}
_RESOLUTION_FIELDS = ("action", "boundary", "provenance", "ambiguity", "directive", "deliberation_profile", "reason_code")


def _literal(value: Any) -> Text:
    """Represent dynamic public values without Rich markup interpretation."""
    return Text(str(value))


def diagnostic_label(level: int) -> str:
    return _DIAGNOSTIC_LABELS[level] if level in range(len(_DIAGNOSTIC_LABELS)) else "OFF"


def diagnostic_prompt_token(level: int) -> str:
    label = diagnostic_label(level)
    return "" if label == "OFF" else f" [{label}]"


def thinking_label(session: object) -> str:
    value = getattr(session, "thinking_budget", 0)
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            return "OFF"
        return _THINKING_LABELS.get(value, str(value))
    configured = getattr(session, "thinking_level", getattr(session, "thinking", "OFF"))
    text = _display_text(configured).upper()
    return {"HIGH": "ALTO", "MEDIUM": "MÉDIO", "LOW": "BAIXO"}.get(text, text or "OFF")


def render_startup_status(console: Console, ctx: object) -> None:
    workspace = getattr(ctx, "workspace", None)
    root = getattr(workspace, "root", workspace)
    orchestrator = getattr(ctx, "orchestrator", None)
    mode = _display_text(getattr(orchestrator, "operational_mode_label", "FULL")) or "FULL"
    level = int(getattr(ctx, "modo_diagnostico", 0))
    console.print("LLM Agent", style="bold cyan")
    console.print(f"Workspace: {root}", markup=False)
    console.print(
        f"{mode} · Think {thinking_label(getattr(ctx, 'session', None))} · Diag {diagnostic_label(level)}",
        markup=False,
    )
    console.print("Digite /help para comandos.", style="dim")


def render_turn_waiting(console: Console) -> None:
    console.print("● Processando", style="dim cyan")


def render_agent_label(console: Console) -> None:
    console.print("Agente:", style="bold blue")


def _display_text(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        value = enum_value
    return str(value) if isinstance(value, (str, int, float, bool)) else ""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _project_mapping(value: Any) -> Mapping[str, Any]:
    mapping = _as_mapping(value)
    if mapping:
        return mapping
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        return {}
    try:
        return _as_mapping(to_dict())
    except (AttributeError, TypeError, ValueError):
        return {}


def _sequence(value: Any) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Mapping)) else ()


def _snapshot_receipt(snapshot: Any, run_result: Any) -> Mapping[str, Any]:
    snapshot_data = _project_mapping(snapshot)
    facts = _as_mapping(snapshot_data.get("projection_facts"))
    outcome = _as_mapping(snapshot_data.get("operational_outcome"))
    projected: dict[str, Any] = {key: facts[key] for key in ("tools", "validation", "rollback") if key in facts}
    if "files_affected" in outcome:
        projected["files_affected"] = outcome["files_affected"]
    if "validation_status" in outcome and "validation" not in projected:
        status = outcome.get("validation_status")
        projected["validation"] = {"ran": status is not None, "outcome": status}
    count = facts.get("replan_count")
    if isinstance(count, int) and not isinstance(count, bool):
        projected["replan"] = {"occurred": count > 0, "count": count}
    if snapshot_data.get("status") is not None:
        projected["status"] = snapshot_data["status"]
    failure = _as_mapping(snapshot_data.get("failure_fact"))
    if failure:
        projected["error"] = failure
    report_path = getattr(run_result, "report_path", None)
    if isinstance(report_path, str) and report_path:
        projected["report_path"] = report_path
    return projected


def _receipt_for(result: Any) -> tuple[Mapping[str, Any], Any]:
    run_result = getattr(result, "run_result", None)
    if run_result is None:
        return {}, None
    receipt = _as_mapping(getattr(run_result, "receipt", None))
    if receipt:
        return receipt, run_result
    snapshot = getattr(run_result, "canonical_snapshot", None)
    if snapshot is None:
        snapshot = getattr(run_result, "snapshot", None)
    return _snapshot_receipt(snapshot, run_result), run_result


def _files_affected(receipt: Mapping[str, Any]) -> tuple[Any, ...]:
    value = receipt.get("files_affected") if "files_affected" in receipt else _as_mapping(receipt.get("operational_outcome")).get("files_affected")
    return _sequence(value)


def _validation(receipt: Mapping[str, Any]) -> str | None:
    value = _as_mapping(receipt.get("validation"))
    if value.get("ran") is not True:
        return None
    outcome = _display_text(value.get("outcome"))
    normalized = outcome.casefold()
    if normalized in {"passed", "pass", "approved", "succeeded", "success", "ok"}:
        return "validação aprovada"
    if normalized in {"failed", "failure", "rejected", "error"}:
        return "validação falhou"
    return f"validação {outcome}" if outcome else "validação executada"


def _rollback(receipt: Mapping[str, Any]) -> str | None:
    value = _as_mapping(receipt.get("rollback"))
    if value.get("occurred") is not True:
        return None
    outcome = _display_text(value.get("outcome"))
    return f"rollback {outcome}" if outcome else "rollback ocorrido"


def _replan_count(receipt: Mapping[str, Any]) -> int | None:
    value = _as_mapping(receipt.get("replan"))
    occurred = value.get("occurred") is True
    count = value.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    return 1 if occurred else None


def build_operational_summary(result: Any) -> tuple[str, ...]:
    receipt, _ = _receipt_for(result)
    if not receipt:
        return ()
    fields: list[str] = []
    tools = _sequence(receipt.get("tools"))
    files = _files_affected(receipt)
    if tools:
        noun = "ferramenta" if len(tools) == 1 else "ferramentas"
        fields.append(f"{len(tools)} {noun}")
    if files:
        noun = "arquivo" if len(files) == 1 else "arquivos"
        fields.append(f"{len(files)} {noun}")
    validation = _validation(receipt)
    if validation is not None:
        fields.append(validation)
    rollback = _rollback(receipt)
    if rollback is not None:
        fields.append(rollback)
    replans = _replan_count(receipt)
    if replans is not None:
        noun = "replanejamento" if replans == 1 else "replanejamentos"
        fields.append(f"{replans} {noun}")
    return tuple(fields)


def _result_success(result: Any) -> bool:
    value = getattr(result, "success", None)
    if value is not None:
        return bool(value)
    return _display_text(getattr(result, "status", "")).casefold() == "succeeded"


def _completion(result: Any) -> tuple[str, str]:
    if _result_success(result):
        return "✓ Concluído", "bold green"
    status = _display_text(getattr(result, "status", "failed")).casefold()
    bounded = {
        "blocked": ("⚠ Bloqueado", "bold yellow"),
        "cancelled": ("■ Cancelado", "bold yellow"),
        "unavailable": ("! Indisponível", "bold yellow"),
    }
    return bounded.get(status, ("✕ Falhou", "bold red"))


def _tool_row(item: Any) -> tuple[str, str]:
    tool = _as_mapping(item)
    name = _display_text(tool.get("tool") or tool.get("name")) or "(sem nome)"
    status = _display_text(tool.get("status")) or "desconhecido"
    executed = _display_text(tool.get("executed")) or "desconhecido"
    return f"tool: {name}", f"status={status}; executed={executed}"


def _verbose_receipt_rows(receipt: Mapping[str, Any], run_result: Any) -> list[tuple[str, str]]:
    rows = [_tool_row(item) for item in _sequence(receipt.get("tools"))]
    files = _files_affected(receipt)
    rows += [("files_affected", ", ".join(_display_text(item) for item in files))] if files else []
    validation = _as_mapping(receipt.get("validation"))
    if validation:
        detail = "não executada" if validation.get("ran") is not True else _display_text(validation.get("outcome")) or "executada"
        rows.append(("validation", detail))
    rollback = _rollback(receipt)
    rows += [("rollback", rollback.removeprefix("rollback "))] if rollback is not None else []
    replans = _replan_count(receipt)
    rows += [("replan", str(replans))] if replans is not None else []
    error = _as_mapping(receipt.get("error"))
    code = _display_text(error.get("code"))
    layer = _display_text(error.get("layer"))
    error_value = "/".join(item for item in (code, layer) if item)
    rows += [("error", error_value)] if error_value else []
    report_path = getattr(run_result, "report_path", None) or receipt.get("report_path")
    rows += [("report_path", report_path)] if isinstance(report_path, str) and report_path else []
    return rows


def _render_verbose_receipt(console: Console, receipt: Mapping[str, Any], run_result: Any) -> None:
    rows = _verbose_receipt_rows(receipt, run_result)
    if not rows:
        return
    table = Table(title="Detalhes canônicos", show_header=False, box=None)
    table.add_column("Campo", style="cyan")
    table.add_column("Valor")
    for key, value in rows:
        table.add_row(_literal(key), _literal(value))
    console.print(table)


def _diagnostic_rows(result: Any) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    resolution = getattr(result, "resolution", None)
    for key in _RESOLUTION_FIELDS:
        raw = resolution.get(key) if isinstance(resolution, Mapping) else getattr(resolution, key, None)
        value = _display_text(raw) if resolution is not None else ""
        if value:
            rows.append((key, value))
    usage = _as_mapping(getattr(result, "interaction_usage", None))
    for key in ("model_calls", "accounted_tokens", "token_usage_complete"):
        usage_value = usage.get(key)
        if isinstance(usage_value, (int, bool)):
            rows.append((key, str(usage_value)))
    status = _display_text(getattr(result, "status", ""))
    if status:
        rows.append(("status", status))
    reason = _display_text(getattr(result, "reason_code", None))
    if reason:
        rows.append(("reason_code", reason))
    return rows


def render_diagnostic_details(console: Console, result: Any, level: int) -> None:
    if level <= 0:
        return
    rows = _diagnostic_rows(result)
    if not rows:
        return
    table = Table(title="Diagnóstico", show_header=False, box=None)
    table.add_column("Campo", style="yellow")
    table.add_column("Valor")
    for key, value in rows:
        table.add_row(_literal(key), _literal(value))
    console.print(table)
    if level >= 2:
        receipt, run_result = _receipt_for(result)
        if receipt:
            _render_verbose_receipt(console, receipt, run_result)


def render_turn_result(console: Console, result: Any, diagnostic_level: int) -> None:
    completion, style = _completion(result)
    summary = build_operational_summary(result)
    line = completion if not summary else f"{completion} · {' · '.join(summary)}"
    console.print(Text(line, style=style))
    render_diagnostic_details(console, result, diagnostic_level)


__all__ = ["build_operational_summary", "diagnostic_label", "diagnostic_prompt_token", "render_agent_label", "render_diagnostic_details", "render_startup_status", "render_turn_result", "render_turn_waiting", "thinking_label"]
