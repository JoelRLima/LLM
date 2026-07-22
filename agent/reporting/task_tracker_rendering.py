from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict


def step_to_dict(step: Any) -> Dict[str, Any]:
    if is_dataclass(step) and not isinstance(step, type):
        data = asdict(step)
    elif isinstance(step, dict):
        data = dict(step)
    else:
        data = {key: getattr(step, key, default) for key, default in (
            ("id", ""), ("title", ""), ("goal", ""), ("priority", "medium"),
            ("depends_on", []), ("estimated_tools", []),
        )}
    priority = data.get("priority", "medium")
    if isinstance(priority, Enum):
        priority = priority.value
    return {
        "id": str(data.get("id", "")), "title": str(data.get("title", "")),
        "goal": str(data.get("goal", "")), "priority": priority or "medium",
        "status": "pending", "estimated_tools": list(data.get("estimated_tools") or []),
        "duration_seconds": None, "summary": "", "depends_on": list(data.get("depends_on") or []),
        "notes": [],
    }


def render_markdown(data: Dict[str, Any]) -> str:
    progress, metrics = data.get("progress", {}), data.get("metrics", {})
    lines = [
        f"# Execução Hierárquica: {data.get('objective', '')}", "",
        f"**Status:** {data.get('status', '')}",
        f"**Progresso:** {progress.get('completed', 0)}/{progress.get('total', 0)} ({progress.get('percent', 0.0)}%)",
        f"**Métricas:** {metrics.get('steps', 0)} passos · {metrics.get('tool_calls', 0)} ferramentas · {metrics.get('llm_calls', 0)} chamadas LLM",
        "", "## Sub-objetivos", "",
    ]
    for step in data.get("steps", []):
        _append_step(lines, step)
    if data.get("final_summary"):
        lines.extend(["## Resposta final consolidada", str(data["final_summary"]), ""])
    if data.get("failure_reason"):
        lines.extend(["## Motivo da falha", str(data["failure_reason"]), ""])
    return "\n".join(lines)


def _append_step(lines: list[str], step: Dict[str, Any]) -> None:
    lines.extend([
        f"### {step.get('title', '')} (`{step.get('id', '')}`)",
        f"- **Objetivo:** {step.get('goal', '')}",
        f"- **Prioridade:** {step.get('priority', '')}",
        f"- **Status:** {step.get('status', '')}",
    ])
    optional = (
        ("depends_on", "Depende de", lambda value: ", ".join(value)),
        ("duration_seconds", "Duração", lambda value: f"{value}s"),
        ("estimated_tools", "Ferramentas estimadas", lambda value: ", ".join(value)),
        ("summary", "Resumo", str),
    )
    for key, label, formatter in optional:
        if step.get(key) is not None and step.get(key) not in ("", []):
            lines.append(f"- **{label}:** {formatter(step[key])}")
    if step.get("notes"):
        lines.append("- **Notas:**")
        lines.extend(f"  - {note.get('text', '')}" for note in step["notes"])
    lines.append("")
