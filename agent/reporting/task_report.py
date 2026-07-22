"""Consolidated, serializable audit report for one agent task."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent.reporting.task_report_rendering import aggregate_metrics, render_markdown
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
        self, agent_state: Any, metrics_entries: List[Dict[str, Any]], final_answer: str
    ) -> Dict[str, Any]:
        metrics_entries = metrics_entries or []
        history = getattr(agent_state, "tool_history", None) or []
        events = getattr(agent_state, "events", None) or []
        steps = self._build_steps(history)
        start, end = self._resolve_time_range(events, metrics_entries)
        answer = final_answer or ""
        return {
            "task_id": self._generate_task_id(),
            "objective": getattr(agent_state, "objective", None),
            "success": self._determine_success(steps, answer),
            "start_time": start,
            "end_time": end,
            "steps": steps,
            "replan_events": self._extract_replan_events(events),
            "metrics": aggregate_metrics(metrics_entries, len(history)),
            "errors": self._collect_errors(steps),
            "final_answer_preview": answer[:MAX_PREVIEW_CHARS],
        }

    def save_report(
        self, report: Dict[str, Any], format: str = "json", path: Optional[str] = None
    ) -> str:
        selected = (format or self.default_format or "json").lower()
        selected = selected if selected in ("json", "markdown") else "json"
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            extension = "json" if selected == "json" else "md"
            path = os.path.join(self.output_dir, f"task_{timestamp}.{extension}")
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        content = json.dumps(report, indent=2, ensure_ascii=False, default=str) if selected == "json" else render_markdown(report)
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
        return path

    @staticmethod
    def _generate_task_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

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
        return text[:max_chars] + "…" if len(text) > max_chars else text

    def _build_steps(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [step for index, entry in enumerate(history) if isinstance(entry, dict) for step in [self._build_step(index, entry)]]

    def _build_step(self, index: int, entry: Dict[str, Any]) -> Dict[str, Any]:
        raw_result = entry.get("result")
        if isinstance(raw_result, dict):
            result = {
                "ok": bool(raw_result.get("ok")),
                "error": self._truncate(raw_result.get("error") or ""),
                "data_summary": self._truncate(raw_result.get("data", raw_result)),
            }
            cache_hit = raw_result.get("cache_hit")
        else:
            result = {
                "ok": False,
                "error": "" if raw_result is None else "resultado em formato inesperado",
                "data_summary": self._truncate(raw_result),
            }
            cache_hit = None
        step: Dict[str, Any] = {"index": index, "tool": entry.get("tool"), "args": entry.get("args") or {}, "result": result}
        if cache_hit is not None:
            step["cache_hit"] = bool(cache_hit)
        return step

    @staticmethod
    def _extract_replan_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        replans = []
        for event in events or []:
            if not isinstance(event, dict) or event.get("type") != "replan":
                continue
            data = event.get("data") or {}
            replans.append({
                "original_step": data.get("original_step"), "error": data.get("error", ""),
                "strategy": data.get("strategy", ""), "replacement_steps": data.get("replacement_steps", 0),
            })
        return replans

    @staticmethod
    def _collect_errors(steps: List[Dict[str, Any]]) -> List[str]:
        return [step["result"]["error"] for step in steps if not step["result"].get("ok") and step["result"].get("error")]

    @staticmethod
    def _determine_success(steps: List[Dict[str, Any]], final_answer: str) -> bool:
        return bool((steps and steps[-1].get("result", {}).get("ok")) or final_answer.strip())

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
    def _aggregate_metrics(entries: List[Dict[str, Any]], tools_called: int) -> Dict[str, int]:
        return aggregate_metrics(entries, tools_called)

    @staticmethod
    def _render_markdown(report: Dict[str, Any]) -> str:
        return render_markdown(report)
