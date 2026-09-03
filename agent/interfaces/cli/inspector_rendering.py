"""Terminal rendering helpers for the UI-neutral inspection surface."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.console import Console
from rich.table import Table

from agent.presentation import InspectorSnapshot


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
    console.print(
        f"Run {run.run_id} | status={run.status} | completeness={run.completeness} | "
        f"liveness={liveness_label(run.liveness)} | mode={run.mode}",
        markup=False,
    )
    heartbeat = snapshot.heartbeat
    console.print(
        "heartbeat=" + str(heartbeat.get("observer_heartbeat"))
        + " | semantic=" + str(heartbeat.get("semantic_activity"))
        + " | silence=" + str(heartbeat.get("silence")),
        markup=False,
    )

    timeline = snapshot.timeline if limit is None else snapshot.timeline[: max(0, limit)]
    table = Table(title="Activity timeline")
    table.add_column("Seq", justify="right")
    table.add_column("Time")
    table.add_column("Source")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Status")
    for item in timeline:
        table.add_row(
            str(item.sequence),
            item.timestamp,
            item.source,
            item.category,
            item.title,
            item.status or "",
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
    )
    for title, value in sections:
        console.print(f"{title}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}", markup=False)
    if snapshot.warnings:
        console.print("Warnings/errors/gaps:", markup=False)
        for item in snapshot.warnings:
            console.print(f"  #{item.sequence} {item.title}: {item.summary}", markup=False)
    if snapshot.selected_detail is not None:
        console.print(
            "Detail: " + json.dumps(snapshot.selected_detail, ensure_ascii=False, sort_keys=True),
            markup=False,
        )
    if snapshot.issues:
        console.print("Trace issues: " + "; ".join(snapshot.issues), markup=False)
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
