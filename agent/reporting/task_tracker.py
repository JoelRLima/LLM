"""Persistent progress tracking for hierarchical execution."""

import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, cast

from agent.reporting.task_tracker_rendering import render_markdown, step_to_dict
from agent.runtime.logging import logger


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step_to_dict(step: Any) -> Dict[str, Any]:
    """Compatibility wrapper for the normalized tracker representation."""
    return step_to_dict(step)


class TaskTracker:
    def __init__(self, json_path: str = "task_tracker.json", markdown_path: str = "task_tracker.md") -> None:
        self.json_path = json_path
        self.markdown_path = markdown_path
        self._data: Dict[str, Any] = {}

    def start(
        self, objective: str, steps: List[Any], planning_metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        try:
            normalized = [_step_to_dict(step) for step in (steps or [])]
            now, metadata = _now_iso(), planning_metadata or {}
            self._data = {
                "objective": objective, "status": TaskStatus.RUNNING.value,
                "progress": {"completed": 0, "total": len(normalized), "percent": 0.0},
                "metrics": {"steps": len(normalized), "tool_calls": 0, "llm_calls": 0},
                "planning": {
                    "model": metadata.get("model", ""), "timestamp": metadata.get("timestamp", now),
                    "prompt": metadata.get("prompt", ""),
                },
                "steps": normalized, "final_summary": "", "failure_reason": "",
                "created_at": now, "updated_at": now,
            }
            self._persist()
        except Exception as exc:
            logger.warning("TaskTracker: falha ao iniciar tracking: %s", exc)

    def _find_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for step in self._data.get("steps", []):
            if step.get("id") == step_id:
                return cast(Dict[str, Any], step)
        return None

    def mark_running(self, step_id: str) -> None:
        self._update_step_status(step_id, StepStatus.RUNNING)

    def mark_completed(self, step_id: str, summary: str = "", duration_seconds: Optional[float] = None) -> None:
        self._update_step_status(step_id, StepStatus.COMPLETED, summary, duration_seconds)
        self._recompute_progress()

    def mark_failed(self, step_id: str, summary: str = "", duration_seconds: Optional[float] = None) -> None:
        self._update_step_status(step_id, StepStatus.FAILED, summary, duration_seconds)
        self._recompute_progress()

    def mark_skipped(self, step_id: str, reason: str = "") -> None:
        self._update_step_status(step_id, StepStatus.SKIPPED, reason)
        self._recompute_progress()

    def add_note(self, step_id: str, note: str) -> None:
        try:
            step = self._find_step(step_id)
            if step is not None:
                step.setdefault("notes", []).append({"text": note, "timestamp": _now_iso()})
                self._persist()
        except Exception as exc:
            logger.warning("TaskTracker: falha ao adicionar nota ao passo '%s': %s", step_id, exc)

    def record_tool_call(self, amount: int = 1) -> None:
        self._bump_metric("tool_calls", amount)

    def record_llm_call(self, amount: int = 1) -> None:
        self._bump_metric("llm_calls", amount)

    def _bump_metric(self, key: str, amount: int) -> None:
        try:
            metrics = self._data.setdefault("metrics", {})
            metrics[key] = metrics.get(key, 0) + amount
            self._persist()
        except Exception as exc:
            logger.warning("TaskTracker: falha ao registrar métrica '%s': %s", key, exc)

    def _update_step_status(
        self, step_id: str, status: StepStatus, summary: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> None:
        try:
            step = self._find_step(step_id)
            if step is None:
                logger.warning("TaskTracker: passo '%s' não encontrado.", step_id)
                return
            step["status"] = status.value
            if summary is not None:
                step["summary"] = summary
            if duration_seconds is not None:
                step["duration_seconds"] = round(duration_seconds, 3)
            self._data["updated_at"] = _now_iso()
            self._persist()
        except Exception as exc:
            logger.warning("TaskTracker: falha ao atualizar passo '%s': %s", step_id, exc)

    def _recompute_progress(self) -> None:
        steps = self._data.get("steps", [])
        finished = {StepStatus.COMPLETED.value, StepStatus.FAILED.value, StepStatus.SKIPPED.value}
        completed = sum(1 for step in steps if step.get("status") in finished)
        total = len(steps)
        self._data["progress"] = {
            "completed": completed, "total": total,
            "percent": round(completed / total * 100.0, 1) if total else 0.0,
        }

    def finish_success(self, final_summary: str = "") -> None:
        self._finish(TaskStatus.COMPLETED, "final_summary", final_summary)

    def finish_failure(self, reason: str = "") -> None:
        self._finish(TaskStatus.FAILED, "failure_reason", reason)

    def _finish(self, status: TaskStatus, field: str, value: str) -> None:
        try:
            self._data["status"] = status.value
            self._data[field] = value or ""
            self._data["updated_at"] = _now_iso()
            self._persist()
        except Exception as exc:
            logger.warning("TaskTracker: falha ao finalizar: %s", exc)

    def _persist(self) -> None:
        self._write_json()
        self._write_markdown()

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)

    def _write_json(self) -> None:
        try:
            self._atomic_write(self.json_path, json.dumps(self._data, indent=2, ensure_ascii=False, default=str))
        except Exception as exc:
            logger.warning("TaskTracker: falha ao gravar JSON em '%s': %s", self.json_path, exc)

    def _write_markdown(self) -> None:
        try:
            self._atomic_write(self.markdown_path, self._render_markdown())
        except Exception as exc:
            logger.warning("TaskTracker: falha ao gravar Markdown em '%s': %s", self.markdown_path, exc)

    def _render_markdown(self) -> str:
        return render_markdown(self._data)
