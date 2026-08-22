"""Recovery-aware projections of failed tool and step observations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.execution_state import StepStatus

_TERMINAL_FAILURE_RESULTS = frozenset(
    {"blocked", "cancelled", "failed", "timed_out", "permission_denied", "protocol_error", "unavailable", "unverified"}
)


class StateFailureRecoveryMixin:
    @staticmethod
    def _result_is_successful(result: Any) -> bool:
        if not isinstance(result, Mapping):
            return False
        status = str(result.get("status") or "")
        if status in _TERMINAL_FAILURE_RESULTS:
            return False
        return result.get("ok") is True and (
            status == "succeeded" or result.get("done") is True or result.get("executed") is True
        )

    @staticmethod
    def _result_is_failure(result: Any) -> bool:
        if not isinstance(result, Mapping):
            return False
        status = str(result.get("status") or "")
        return status in _TERMINAL_FAILURE_RESULTS or result.get("ok") is False

    @staticmethod
    def _same_operation(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_step = str(left.get("step_id") or "")
        right_step = str(right.get("step_id") or "")
        if left_step and right_step and left_step == right_step:
            return True
        left_tool = str(left.get("tool") or "").casefold()
        right_tool = str(right.get("tool") or "").casefold()
        return bool(left_tool and left_tool == right_tool and left.get("args") == right.get("args"))

    def _later_recovery(self: Any, failure_index: int, failure: Mapping[str, Any]) -> bool:
        return any(
            isinstance(candidate, Mapping)
            and self._result_is_successful(candidate.get("result"))
            and self._same_operation(failure, candidate)
            for candidate in self.tool_history[failure_index + 1 :]
        )

    def has_recovered_failure(self: Any) -> bool:
        return any(
            isinstance(item, Mapping)
            and self._result_is_failure(item.get("result"))
            and self._later_recovery(index, item)
            for index, item in enumerate(self.tool_history)
        )

    def has_unrecovered_invocation_failures(self: Any) -> bool:
        return any(
            isinstance(item, Mapping)
            and self._result_is_failure(item.get("result"))
            and not self._later_recovery(index, item)
            for index, item in enumerate(self.tool_history)
        )

    def has_unrecovered_step_failures(self: Any) -> bool:
        failed = {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.UNVERIFIED}
        return any(self._step_has_unrecovered_failure(record, failed) for record in self.step_records.values())

    def _step_has_unrecovered_failure(self: Any, record: Any, failed: set[StepStatus]) -> bool:
        if record.status not in failed:
            return False
        relevant = [
            item
            for item in self.tool_history
            if isinstance(item, Mapping) and str(item.get("step_id") or "") == record.step_id
        ]
        if not relevant:
            return True
        failures = [
            (index, item)
            for index, item in enumerate(self.tool_history)
            if isinstance(item, Mapping)
            and str(item.get("step_id") or "") == record.step_id
            and self._result_is_failure(item.get("result"))
        ]
        if any(not self._later_recovery(index, item) for index, item in failures):
            return True
        return not failures and not self._result_is_successful(relevant[-1].get("result"))

    def has_unrecovered_task_failures(
        self: Any,
        *,
        task_failed: bool = False,
        include_invocation_history: bool = False,
    ) -> bool:
        if self.has_unrecovered_step_failures():
            return True
        if include_invocation_history and self.has_unrecovered_invocation_failures():
            return True
        return bool(task_failed and not self.has_recovered_failure())
