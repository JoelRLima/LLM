"""Continuity lineage state owned by :class:`agent.state.AgentState`."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from agent.state_checkpoint import CONTINUITY_SCHEMA_VERSION, validate_continuity_metadata


class StateContinuityMixin:
    """Keep bounded attempt lineage separate from task execution facts."""

    continuity: Dict[str, Any] | None
    _runtime_correlation: Any
    _continuity_resume_pending: bool
    _continuity_bound_run_id: str | None

    @property
    def runtime_correlation(self) -> Any:
        return self._runtime_correlation

    @runtime_correlation.setter
    def runtime_correlation(self, value: Any) -> None:
        self._runtime_correlation = value
        run_id = getattr(value, "run_id", None)
        if not isinstance(run_id, str) or not run_id.strip():
            return
        if getattr(self, "_continuity_resume_pending", False):
            self.record_run_attempt(run_id, resumed=True)
            self._continuity_resume_pending = False
        elif getattr(self, "continuity", None) is None:
            self.record_run_attempt(run_id, resumed=False)

    def ensure_continuity_for_current_run(self) -> None:
        run_id = getattr(self.runtime_correlation, "run_id", None)
        if not isinstance(run_id, str) or not run_id.strip():
            return
        if self._continuity_resume_pending:
            self.record_run_attempt(run_id, resumed=True)
            self._continuity_resume_pending = False
        elif self.continuity is None:
            self.record_run_attempt(run_id, resumed=False)

    def record_run_attempt(self, run_id: str, *, resumed: bool) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("continuity run_id must be a non-empty string")
        current = self.continuity
        if not resumed:
            if current is not None:
                normalized = validate_continuity_metadata(current)
                if normalized.get("last_run_id") == run_id:
                    self.continuity = normalized
                    self._continuity_bound_run_id = run_id
                return
            self.continuity = {
                "schema_version": CONTINUITY_SCHEMA_VERSION,
                "resume_generation": 0,
                "last_run_id": run_id,
                "interrupted": False,
                "interruption_reason": None,
                "interrupted_at": None,
            }
            self._continuity_bound_run_id = run_id
            return

        previous = validate_continuity_metadata(current) if current is not None else None
        previous_run_id = previous.get("last_run_id") if previous is not None else None
        next_metadata: Dict[str, Any] = {
            "schema_version": CONTINUITY_SCHEMA_VERSION,
            "resume_generation": int(previous["resume_generation"]) + 1 if previous is not None else 0,
            "last_run_id": run_id,
            "interrupted": False,
            "interruption_reason": None,
            "interrupted_at": None,
        }
        if isinstance(previous_run_id, str) and previous_run_id.strip():
            next_metadata["resumed_from_run_id"] = previous_run_id
        self.continuity = validate_continuity_metadata(next_metadata)
        self._continuity_bound_run_id = run_id

    def record_continuity_interruption(
        self,
        reason: str = "keyboard_interrupt",
        *,
        interrupted_at: str | None = None,
    ) -> None:
        self.ensure_continuity_for_current_run()
        current = self.continuity
        if current is None:
            current = {
                "schema_version": CONTINUITY_SCHEMA_VERSION,
                "resume_generation": 0,
                "last_run_id": None,
                "interrupted": False,
                "interruption_reason": None,
                "interrupted_at": None,
            }
        candidate = dict(validate_continuity_metadata(current))
        candidate["interrupted"] = True
        candidate["interruption_reason"] = reason
        candidate["interrupted_at"] = _utc_timestamp() if interrupted_at is None else interrupted_at
        self.continuity = validate_continuity_metadata(candidate)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["StateContinuityMixin"]
