from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict

from agent.contracts import StepRecordData


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNVERIFIED = "unverified"


TERMINAL_STEP_STATUSES = {
    StepStatus.COMPLETED,
    StepStatus.FAILED,
    StepStatus.SKIPPED,
    StepStatus.BLOCKED,
    StepStatus.CANCELLED,
    StepStatus.UNVERIFIED,
}


@dataclass
class StepExecutionRecord:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    last_error: str = ""

    def to_dict(self) -> StepRecordData:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepExecutionRecord":
        if not isinstance(data, dict):
            raise ValueError("step execution record must be an object")
        step_id = str(data.get("step_id", ""))
        if not step_id.strip():
            raise ValueError("step execution record requires a step_id")
        try:
            status = StepStatus(str(data.get("status", StepStatus.PENDING.value)))
        except ValueError as exc:
            raise ValueError("step execution record has an invalid status") from exc
        attempts = data.get("attempts", 0)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise ValueError("step execution record has invalid attempts")
        last_error = data.get("last_error", "")
        if not isinstance(last_error, str):
            raise ValueError("step execution record has invalid last_error")
        return cls(
            step_id=step_id,
            status=status,
            attempts=attempts,
            last_error=last_error,
        )

    def prepare_for_resume(
        self, retry_failed: bool = False, retry_skipped: bool = False
    ) -> None:
        if self.status is StepStatus.FAILED and retry_failed:
            self.status = StepStatus.PENDING
        elif self.status is StepStatus.SKIPPED and retry_skipped:
            self.status = StepStatus.PENDING
        elif self.status is StepStatus.RUNNING:
            self.status = StepStatus.PENDING
            self.last_error = "interrompido antes da conclusão"
