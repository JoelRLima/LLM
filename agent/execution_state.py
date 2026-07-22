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


TERMINAL_STEP_STATUSES = {
    StepStatus.COMPLETED,
    StepStatus.FAILED,
    StepStatus.SKIPPED,
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
        step_id = str(data.get("step_id", ""))
        try:
            status = StepStatus(str(data.get("status", StepStatus.PENDING.value)))
        except ValueError:
            status = StepStatus.PENDING
        attempts = data.get("attempts", 0)
        if not isinstance(attempts, int) or attempts < 0:
            attempts = 0
        return cls(
            step_id=step_id,
            status=status,
            attempts=attempts,
            last_error=str(data.get("last_error", "") or ""),
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
