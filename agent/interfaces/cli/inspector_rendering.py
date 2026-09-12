"""Terminal rendering helpers for the UI-neutral inspection surface."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.presentation import InspectorSnapshot


def _literal(value: Any) -> Text:
    """Represent public dynamic content without Rich markup interpretation."""

    return Text(str(value))


def liveness_label(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "unavailable"
    state = str(value.get("state", "unavailable"))
    if state == "stale":
        return "stale/uncertain"
    if state == "live":
        return "live/recently-live"
    return state


def render_snapshot(snapshot: InspectorSnapshot, console: Console, *, limit: int | None = None) -> None:
    """Render a structured snapshot; all values originate from redacted models."""

    run = snapshot.run
    header = Table.grid(padding=(0, 1))
    header.add_column("Field", style="cyan")
    header.add_column("Value")
    header.add_row("Run", _literal(run.run_id))
    header.add_row("Status", _literal(run.status))
    header.add_row("Completeness", _literal(run.completeness))
    header.add_row("Liveness", _literal(liveness_label(run.liveness)))
    header.add_row("Mode", _literal(run.mode))
    console.print(Panel(header, title="Inspector", border_style="cyan"))
    heartbeat = snapshot.heartbeat
    heartbeat_text = (
        f"observer={heartbeat.get('observer_heartbeat')} | "
        f"semantic={heartbeat.get('semantic_activity')} | "
        f"silence={heartbeat.get('silence')}"
    )
    console.print(Panel(_literal(heartbeat_text), title="Heartbeat", border_style="dim"))

    timeline = snapshot.timeline if limit is None else snapshot.timeline[: max(0, limit)]
    table = Table(title="Activity timeline", border_style="blue")
    table.add_column("Seq", justify="right")
    table.add_column("Time")
    table.add_column("Source")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Status")
    for item in timeline:
        table.add_row(
            _literal(item.sequence),
            _literal(item.timestamp),
            _literal(item.source),
            _literal(item.category),
            _literal(item.title),
            _literal(item.status or ""),
        )
    console.print(table)

    sections = (
        ("Current", snapshot.current),
        ("Plan/steps", snapshot.plan_steps),
        ("Model calls", snapshot.model_calls),
        ("Tools", snapshot.tools),
        ("Validation", snapshot.validation),
        ("Recovery", snapshot.recovery),
        ("Changes", snapshot.changes),
        ("Metrics", snapshot.metrics),
        ("Convergence", snapshot.convergence),
    )
    for title, value in sections:
        console.print(
            Panel(
                _literal(json.dumps(value, ensure_ascii=False, sort_keys=True)),
                title=title,
                border_style="dim",
            )
        )
    if snapshot.warnings:
        warnings = Table(title="Warnings/errors/gaps", show_header=False, border_style="yellow")
        warnings.add_column("Sequence", style="yellow")
        warnings.add_column("Detail")
        for item in snapshot.warnings:
            warnings.add_row(_literal(f"#{item.sequence}"), _literal(f"{item.title}: {item.summary}"))
        console.print(warnings)
    if snapshot.selected_detail is not None:
        console.print(
            Panel(
                _literal(json.dumps(snapshot.selected_detail, ensure_ascii=False, sort_keys=True)),
                title="Detail",
                border_style="magenta",
            )
        )
    if snapshot.issues:
        console.print(Panel(_literal("; ".join(snapshot.issues)), title="Trace issues", border_style="red"))
    console.print("[q/Ctrl-C] detach/quit; a refresh never mutates the run", markup=False)


def render_runs(runs: tuple[Any, ...], console: Console) -> None:
    table = Table(title="Retained observability runs")
    for column in ("Run ID", "Start", "End", "Liveness", "Completeness", "Mode", "Outcome"):
        table.add_column(column)
    for run in runs:
        outcome = run.final_outcome or {}
        outcome_text = outcome.get("status", "unavailable") if isinstance(outcome, Mapping) else "unavailable"
        table.add_row(
            run.run_id,
            run.start_time,
            run.end_time or "",
            liveness_label(run.liveness),
            run.completeness,
            run.mode,
            str(outcome_text),
        )
    console.print(table)


__all__ = ["liveness_label", "render_runs", "render_snapshot"]
