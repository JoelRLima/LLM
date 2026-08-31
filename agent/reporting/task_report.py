"""Consolidated, serializable audit report for one agent task."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast
from uuid import uuid4

from agent.planning.task_progress_projection import build_task_progress_projection
from agent.reporting.metrics import project_run_metrics
from agent.reporting.observation_evidence import project_tool_observation
from agent.reporting.public_projection import (
    canonical_effect_projection,
    reconcile_receipt_projection,
    reconcile_report_status,
)
from agent.reporting.public_safety import sanitize_public_text
from agent.reporting.run_projection_facts import thaw_projection
from agent.reporting.task_report_events import (
    extract_replan_events,
    project_event,
    project_events,
    project_planner_outcome,
)
from agent.reporting.task_report_rendering import render_markdown
from agent.runtime.paths import REPORTS_DIR

TIMESTAMP_KEYS = ("timestamp", "time", "ts")
MAX_SUMMARY_CHARS = 500
MAX_PREVIEW_CHARS = 500

class TaskReportBuilder:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        raw = (config or {}).get("task_report") or {}
        settings = raw if isinstance(raw, dict) else {}
        self.enabled = bool(settings.get("enabled", True))
        self.default_format = str(settings.get("format", "json"))
        self.output_dir = str(settings.get("output_dir", REPORTS_DIR))

    def build_report(
        self,
        agent_state: Any,
        metrics_entries: List[Dict[str, Any]],
        final_answer: str,
        *,
        canonical_outcome: Dict[str, Any] | None = None,
        receipt: Dict[str, Any] | None = None,
        snapshot: Any = None,
    ) -> Dict[str, Any]:
        if snapshot is None:
            if canonical_outcome is None:
                raise TypeError("canonical_outcome is required without a canonical snapshot")
            requested_status = canonical_outcome.get("status")
            if not isinstance(requested_status, str) or not requested_status:
                raise ValueError("canonical_outcome.status is required")
            status = reconcile_report_status(agent_state, requested_status)
            report_metrics = self._aggregate_task_metrics(
                agent_state, metrics_entries or [], len(getattr(agent_state, "tool_history", None) or [])
            )
            operational_outcome = canonical_effect_projection(
                agent_state, status
            )["operational_outcome"]
            metrics_entries = metrics_entries or []
            history = getattr(agent_state, "tool_history", None) or []
            events = getattr(agent_state, "events", None) or []
            steps = self._build_steps(history)
            start, end = self._resolve_time_range(events, metrics_entries)
            objective = getattr(agent_state, "objective", None)
            planner_outcome = self._project_planner_outcome(events)
            event_summary = self._project_events(events)
            replan_events = self._extract_replan_events(events)
            progress = build_task_progress_projection(agent_state, operational_outcome=operational_outcome).to_dict()
        else:
            status = str(snapshot.status)
            canonical_outcome = {
                "status": status,
                "error": (
                    snapshot.failure_fact.message
                    if snapshot.failure_fact is not None
                    else snapshot.diagnostics[0].get("message")
                    if snapshot.diagnostics and isinstance(snapshot.diagnostics[0], Mapping)
                    else None
                ),
            }
            report_metrics = snapshot.metrics.to_dict()
            operational_outcome = snapshot.operational_outcome.to_dict()
            facts = snapshot.projection_facts
            steps = [thaw_projection(item) for item in facts.report_steps]
            start = facts.report_start_time
            end = facts.report_end_time
            objective = facts.objective
            planner_outcome = facts.planner_outcome
            event_summary = [thaw_projection(item) for item in facts.event_summary]
            replan_events = [thaw_projection(item) for item in facts.replan_events]
            progress = thaw_projection(facts.progress)
        answer = final_answer or ""
        report = {
            "report_id": self._generate_report_id(),
            "objective": objective,
            "success": status == "succeeded",
            "start_time": start,
            "end_time": end,
            "steps": steps,
            "progress": progress,
            "planner_outcome": planner_outcome,
            "event_summary": event_summary,
            "replan_events": replan_events,
            "metrics": report_metrics,
            "errors": self._collect_errors(steps),
            "final_answer_preview": sanitize_public_text(answer[:MAX_PREVIEW_CHARS]),
        }
        report["status"] = status
        raw_error = canonical_outcome.get("error")
        report["error"] = (
            sanitize_public_text(raw_error) if raw_error is not None else None
        )
        report["operational_outcome"] = operational_outcome
        if snapshot is not None:
            correlation = snapshot.correlation
            report.update(
                {
                    "run_id": correlation.run_id,
                    "root_task_id": correlation.root_task_id,
                    "task_id": correlation.task_id,
                }
            )
        if receipt is not None:
            if snapshot is None:
                receipt_projection = reconcile_receipt_projection(agent_state, status, receipt)
                report["operational_outcome"] = receipt_projection["operational_outcome"]
                report["receipt"] = receipt_projection
            else:
                report["receipt"] = dict(receipt)
        return report
    @staticmethod
    def _aggregate_task_metrics(
        agent_state: Any,
        metrics_entries: List[Dict[str, Any]],
        history_records: int,
        *,
        snapshot: Any = None,
    ) -> Dict[str, Any]:
        if snapshot is not None:
            return cast(Dict[str, Any], snapshot.metrics.to_dict())
        if snapshot is None:
            ledger = getattr(agent_state, "budget_ledger", None)
            budget_snapshot = ledger.snapshot() if ledger is not None and hasattr(ledger, "snapshot") else None
            return cast(Dict[str, Any], project_run_metrics(
                metrics_entries,
                tool_calls=(getattr(budget_snapshot, "tool_calls", None) if budget_snapshot is not None else None),
                history_records=history_records,
                budget_snapshot=budget_snapshot,
            ).to_dict())
        return cast(Dict[str, Any], snapshot.metrics.to_dict())
    def save_report(
        self, report: Dict[str, Any], format: str = "json", path: Optional[str] = None
    ) -> str:
        selected = (format or self.default_format or "json").lower()
        selected = selected if selected in ("json", "markdown") else "json"
        if path is None:
            extension = "json" if selected == "json" else "md"
            identity = str(report.get("report_id") or self._generate_report_id())
            report["report_id"] = identity
            path = os.path.join(self.output_dir, f"report_{identity}.{extension}")
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        report["report_path"] = str(path)
        receipt = report.get("receipt")
        if isinstance(receipt, dict):
            receipt["report_path"] = str(path)
        content = json.dumps(report, indent=2, ensure_ascii=False, default=str) if selected == "json" else render_markdown(report)
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
        return path

    @staticmethod
    def _generate_report_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{str(uuid4().hex)[:8]}"

    @staticmethod
    def _truncate(value: Any, max_chars: int = MAX_SUMMARY_CHARS) -> str:
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        text = sanitize_public_text(text)
        return text[:max_chars] + "…" if len(text) > max_chars else text

    def _build_steps(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [step for index, entry in enumerate(history) if isinstance(entry, dict) for step in [self._build_step(index, entry)]]

    def _build_step(self, index: int, entry: Dict[str, Any]) -> Dict[str, Any]:
        raw_result = entry.get("result")
        evidence = project_tool_observation(entry) if isinstance(raw_result, Mapping) else None
        if isinstance(raw_result, Mapping):
            assert evidence is not None
            data = evidence.value if evidence.present else None
            output_text = self._truncate(data) if evidence.present else ""
            result = {
                "ok": evidence.ok is True,
                "error": self._truncate(raw_result.get("error") or ""),
                "data_summary": output_text,
                "status": evidence.status,
                "executed": evidence.executed,
                "reason_code": evidence.error_code,
                "output_chars": len(output_text),
                "present": evidence.present,
                "complete": evidence.complete,
                "truncated": evidence.truncated,
                "value_type": evidence.value_type,
            }
            cache_hit = raw_result.get("cache_hit")
        else:
            result = {
                "ok": False,
                "error": "" if raw_result is None else "resultado em formato inesperado",
                "data_summary": self._truncate(raw_result),
            }
            cache_hit = None
        step: Dict[str, Any] = {
            "index": index,
            "tool": entry.get("tool"),
            "args": self._project_args(entry.get("args")),
            "result": result,
        }
        invocation_id = evidence.invocation_id if evidence is not None else None
        if invocation_id:
            step["invocation_id"] = str(invocation_id)
        if cache_hit is not None:
            step["cache_hit"] = bool(cache_hit)
        return step

    @staticmethod
    def _project_args(raw_args: Any) -> Dict[str, str]:
        """Keep bounded resource identity, never arbitrary tool payloads."""
        if not isinstance(raw_args, dict):
            return {}
        projected: Dict[str, str] = {}
        for key in ("file_path", "path", "target", "mode", "action"):
            value = raw_args.get(key)
            if isinstance(value, (str, int, float, bool)):
                projected[key] = str(value)[:200]
        return projected

    @staticmethod
    def _project_planner_outcome(events: List[Dict[str, Any]]) -> str | None:
        return cast(str | None, project_planner_outcome(events))

    @staticmethod
    def _project_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], project_events(events))

    @staticmethod
    def _project_event(event: Any) -> Dict[str, Any] | None:
        return cast(Dict[str, Any] | None, project_event(event))

    @staticmethod
    def _extract_replan_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], extract_replan_events(events))

    @staticmethod
    def _collect_errors(steps: List[Dict[str, Any]]) -> List[str]:
        return [step["result"]["error"] for step in steps if not step["result"].get("ok") and step["result"].get("error")]

    @staticmethod
    def _resolve_time_range(
        events: List[Dict[str, Any]], metrics_entries: List[Dict[str, Any]]
    ) -> tuple[str, str]:
        del events
        timestamps = [
            str(entry[key]) for entry in metrics_entries if isinstance(entry, dict)
            for key in TIMESTAMP_KEYS if entry.get(key)
        ]
        if timestamps:
            timestamps.sort()
            return timestamps[0], timestamps[-1]
        now = datetime.now(timezone.utc).isoformat()
        return now, now

    @staticmethod
    def _aggregate_metrics(entries: List[Dict[str, Any]], tools_called: int, *, snapshot: Any = None) -> Dict[str, Any]:
        if snapshot is None:
            return cast(Dict[str, Any], project_run_metrics(entries, tools_called).to_dict())
        return cast(Dict[str, Any], snapshot.metrics.to_dict())

    @staticmethod
    def _render_markdown(report: Dict[str, Any]) -> str:
        return cast(str, render_markdown(report))
