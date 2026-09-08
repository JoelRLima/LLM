"""Single root-task owner for broad no-progress plateau convergence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

from agent.planning.progress_receipt import (
    MAX_CREDIT_FACT_IDS,
    ProgressDelta,
    ProgressReceiptV1,
    compare_progress_receipts,
    stable_digest,
)

MIN_PLATEAU = 4
MAX_PLATEAU = 100
MAX_CHECKPOINT_CYCLES = 100
WATCHDOG_NO_PROGRESS_PLATEAU = "WATCHDOG_NO_PROGRESS_PLATEAU"
_HEX_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def _strict_plateau(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_PLATEAU <= value <= MAX_PLATEAU:
        raise ValueError("max_no_progress_plateau must be an integer between 4 and 100")
    return value


@dataclass(frozen=True, slots=True)
class ConvergenceThresholds:
    max_no_progress_plateau: int
    refresh_threshold: int
    replan_threshold: int
    terminal_threshold: int

    @classmethod
    def from_limit(cls, value: int) -> "ConvergenceThresholds":
        limit = _strict_plateau(value)
        refresh = max(2, limit // 3)
        replan = max(refresh + 1, (2 * limit) // 3)
        if not refresh < replan < limit:
            raise ValueError("plateau thresholds must be strictly ordered")
        return cls(limit, refresh, replan, limit)


@dataclass(frozen=True, slots=True)
class ConvergenceAccountingContext:
    """Explicit route accounting contract; no call-stack inference."""

    root_task_id: str
    cycle_kind: str = "model_actionable"
    delegated: bool = False
    cache_reuse: bool = False

    @classmethod
    def root_attempt(cls, root_task_id: str, cycle_kind: str = "model_actionable") -> "ConvergenceAccountingContext":
        return cls(str(root_task_id), cycle_kind, delegated=False, cache_reuse=False)

    @classmethod
    def delegated_attempt(cls, root_task_id: str, cycle_kind: str = "model_actionable") -> "ConvergenceAccountingContext":
        return cls(str(root_task_id), cycle_kind, delegated=True, cache_reuse=False)

    @classmethod
    def cache_reuse_attempt(cls, root_task_id: str, cycle_kind: str = "cache_reuse") -> "ConvergenceAccountingContext":
        return cls(str(root_task_id), cycle_kind, delegated=False, cache_reuse=True)


@dataclass(frozen=True, slots=True)
class ConvergenceObservation:
    delta: ProgressDelta
    task_new_credit_fact_ids: tuple[str, ...]
    cycles_since_progress: int
    stage: str
    cycle_charged: bool
    advanced: bool
    should_refresh: bool = False
    should_replan: bool = False
    terminal: bool = False
    reason_code: str | None = None

    @property
    def no_progress(self) -> bool:
        return self.cycle_charged and not self.advanced

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_new_credit_fact_ids": list(self.delta.raw_new_credit_fact_ids),
            "lost_credit_fact_ids": list(self.delta.lost_credit_fact_ids),
            "task_new_credit_fact_ids": list(self.task_new_credit_fact_ids),
            "dimensions": list(self.delta.dimensions),
            "cycles_since_progress": self.cycles_since_progress,
            "stage": self.stage,
            "cycle_charged": self.cycle_charged,
            "advanced": self.advanced,
            "should_refresh": self.should_refresh,
            "should_replan": self.should_replan,
            "terminal": self.terminal,
            "reason_code": self.reason_code,
        }


def _normalize_ids(values: Iterable[str]) -> tuple[str, ...]:
    normalized = {str(value).strip() for value in values if isinstance(value, str) and value.strip()}
    if len(normalized) > MAX_CREDIT_FACT_IDS:
        raise ValueError("credited_fact_ids_seen exceeds bounded monotonic owner limit")
    return tuple(sorted(normalized))


class ConvergenceStateV1:
    """Thread-safe monotonic credit ledger and derived plateau state."""

    schema_version = 1

    def __init__(self, max_no_progress_plateau: int = 6) -> None:
        self._thresholds = ConvergenceThresholds.from_limit(max_no_progress_plateau)
        self._plateau_epoch_id = stable_digest([])
        self._credited_fact_ids_seen: set[str] = set()
        self._last_observed_current_state_id = ""
        self._cycles_since_progress = 0
        self._refresh_performed_in_epoch = False
        self._replan_performed_in_epoch = False
        self._last_progress_dimensions: tuple[str, ...] = ()
        self._terminal_event_emitted = False
        self._bootstrapped = False
        self._lock = Lock()

    @property
    def max_no_progress_plateau(self) -> int:
        return self._thresholds.max_no_progress_plateau

    @property
    def thresholds(self) -> ConvergenceThresholds:
        return self._thresholds

    @property
    def plateau_epoch_id(self) -> str:
        with self._lock:
            return self._plateau_epoch_id

    @property
    def credited_fact_ids_seen(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._credited_fact_ids_seen))

    @property
    def complete_credited_fact_ids_seen(self) -> tuple[str, ...]:
        return self.credited_fact_ids_seen

    @property
    def last_observed_current_state_id(self) -> str:
        with self._lock:
            return self._last_observed_current_state_id

    @property
    def cycles_since_progress(self) -> int:
        with self._lock:
            return self._cycles_since_progress

    @property
    def refresh_performed_in_epoch(self) -> bool:
        with self._lock:
            return self._refresh_performed_in_epoch

    @property
    def replan_performed_in_epoch(self) -> bool:
        with self._lock:
            return self._replan_performed_in_epoch

    @property
    def bootstrapped(self) -> bool:
        with self._lock:
            return self._bootstrapped

    @property
    def last_progress_dimensions(self) -> tuple[str, ...]:
        with self._lock:
            return self._last_progress_dimensions

    def mark_terminal_event_emitted(self) -> bool:
        """Atomically claim the one bounded terminal projection for this owner."""

        with self._lock:
            if self._terminal_event_emitted:
                return False
            self._terminal_event_emitted = True
            return True

    @property
    def stage(self) -> str:
        with self._lock:
            return self._derived_stage_locked()

    def _derived_stage_locked(self) -> str:
        if self._cycles_since_progress >= self._thresholds.terminal_threshold:
            return "plateau_terminal"
        if self._cycles_since_progress >= self._thresholds.replan_threshold and not self._replan_performed_in_epoch:
            return "replan_escalation"
        if self._cycles_since_progress >= self._thresholds.refresh_threshold and not self._refresh_performed_in_epoch:
            return "context_refresh"
        return "normal"

    def bootstrap(self, receipt: ProgressReceiptV1) -> None:
        """Seed the complete current credit set without charging a cycle."""

        if not isinstance(receipt, ProgressReceiptV1):
            raise TypeError("convergence bootstrap requires ProgressReceiptV1")
        with self._lock:
            if self._bootstrapped:
                return
            self._credited_fact_ids_seen.update(receipt.credit_fact_ids)
            self._plateau_epoch_id = stable_digest(sorted(self._credited_fact_ids_seen))
            self._last_observed_current_state_id = receipt.current_state_id
            self._cycles_since_progress = 0
            self._bootstrapped = True

    def reconfigure(self, max_no_progress_plateau: int) -> None:
        thresholds = ConvergenceThresholds.from_limit(max_no_progress_plateau)
        with self._lock:
            self._thresholds = thresholds

    def _ensure_bootstrapped_locked(self, before: ProgressReceiptV1) -> None:
        if self._bootstrapped:
            return
        self._credited_fact_ids_seen.update(before.credit_fact_ids)
        self._plateau_epoch_id = stable_digest(sorted(self._credited_fact_ids_seen))
        self._last_observed_current_state_id = before.current_state_id
        self._bootstrapped = True

    def mark_progress(
        self,
        after: ProgressReceiptV1,
        task_new_credit_fact_ids: Sequence[str],
        delta: ProgressDelta | None = None,
    ) -> ConvergenceObservation:
        """Accept genuinely new task credit and start a new epoch."""

        selected = _normalize_ids(task_new_credit_fact_ids)
        with self._lock:
            self._credited_fact_ids_seen.update(after.credit_fact_ids)
            self._credited_fact_ids_seen.update(selected)
            self._plateau_epoch_id = stable_digest(sorted(self._credited_fact_ids_seen))
            self._cycles_since_progress = 0
            self._refresh_performed_in_epoch = False
            self._replan_performed_in_epoch = False
            self._last_progress_dimensions = tuple(delta.dimensions if delta is not None else ())
            self._terminal_event_emitted = False
            self._last_observed_current_state_id = after.current_state_id
            self._bootstrapped = True
            selected_delta = delta or ProgressDelta(raw_new_credit_fact_ids=selected)
            return ConvergenceObservation(
                delta=selected_delta,
                task_new_credit_fact_ids=selected,
                cycles_since_progress=0,
                stage="normal",
                cycle_charged=False,
                advanced=True,
                reason_code="PROGRESS_ADVANCED",
            )

    def mark_no_progress(
        self,
        after: ProgressReceiptV1,
        cycle_kind: str = "model_actionable",
        delta: ProgressDelta | None = None,
        *,
        cache_reuse: bool = False,
    ) -> ConvergenceObservation:
        with self._lock:
            if cache_reuse or cycle_kind == "cache_reuse":
                self._last_observed_current_state_id = after.current_state_id
                return ConvergenceObservation(
                    delta=delta or ProgressDelta(),
                    task_new_credit_fact_ids=(),
                    cycles_since_progress=self._cycles_since_progress,
                    stage=self._derived_stage_locked(),
                    cycle_charged=False,
                    advanced=False,
                )
            self._cycles_since_progress = min(MAX_CHECKPOINT_CYCLES, self._cycles_since_progress + 1)
            self._last_observed_current_state_id = after.current_state_id
            stage = self._derived_stage_locked()
            should_refresh = stage == "context_refresh" and not self._refresh_performed_in_epoch
            should_replan = stage == "replan_escalation" and not self._replan_performed_in_epoch
            if should_refresh:
                self._refresh_performed_in_epoch = True
            if should_replan:
                # Reaching the replan boundary consumes the one-shot decision
                # even when the existing recovery owner later denies it.
                self._replan_performed_in_epoch = True
            terminal = self._cycles_since_progress >= self._thresholds.terminal_threshold
            return ConvergenceObservation(
                delta=delta or ProgressDelta(),
                task_new_credit_fact_ids=(),
                cycles_since_progress=self._cycles_since_progress,
                stage=stage,
                cycle_charged=True,
                advanced=False,
                should_refresh=should_refresh,
                should_replan=should_replan,
                terminal=terminal,
                reason_code=WATCHDOG_NO_PROGRESS_PLATEAU if terminal else None,
            )

    def observe(
        self,
        before: ProgressReceiptV1,
        after: ProgressReceiptV1,
        accounting: ConvergenceAccountingContext | None = None,
    ) -> ConvergenceObservation:
        """Account one root attempt or explicitly ignore a delegated inner one."""

        if not isinstance(before, ProgressReceiptV1) or not isinstance(after, ProgressReceiptV1):
            raise TypeError("convergence observe requires ProgressReceiptV1 before/after")
        context = accounting or ConvergenceAccountingContext.root_attempt("root")
        delta = compare_progress_receipts(before, after)
        with self._lock:
            self._ensure_bootstrapped_locked(before)
            seen = set(self._credited_fact_ids_seen)
            task_new = tuple(sorted(set(delta.raw_new_credit_fact_ids) - seen))
            if context.delegated:
                self._last_observed_current_state_id = after.current_state_id
                return ConvergenceObservation(
                    delta=delta,
                    task_new_credit_fact_ids=task_new,
                    cycles_since_progress=self._cycles_since_progress,
                    stage=self._derived_stage_locked(),
                    cycle_charged=False,
                    advanced=bool(task_new),
                )
        if task_new:
            return self.mark_progress(after, task_new, delta)
        return self.mark_no_progress(after, context.cycle_kind, delta, cache_reuse=context.cache_reuse)

    def reconcile_resume(self, receipt: ProgressReceiptV1) -> dict[str, Any]:
        """Reconcile current state without turning divergence into progress."""

        if not isinstance(receipt, ProgressReceiptV1):
            raise TypeError("resume reconciliation requires ProgressReceiptV1")
        with self._lock:
            external = set(receipt.credit_fact_ids) - self._credited_fact_ids_seen
            self._credited_fact_ids_seen.update(receipt.credit_fact_ids)
            self._last_observed_current_state_id = receipt.current_state_id
            return {
                "classification": "RESUME_STATE_DIVERGED" if external else "RESUME_CONTINUOUS",
                "external_credit_fact_ids": tuple(sorted(external)),
                "cycles_since_progress": self._cycles_since_progress,
                "stage": self._derived_stage_locked(),
                "plateau_epoch_id": self._plateau_epoch_id,
            }

    def mark_refresh_attempted(self) -> bool:
        with self._lock:
            if self._refresh_performed_in_epoch:
                return False
            self._refresh_performed_in_epoch = True
            return True

    def mark_replan_attempted(self) -> bool:
        with self._lock:
            if self._replan_performed_in_epoch:
                return False
            self._replan_performed_in_epoch = True
            return True

    def terminal_reached(self) -> bool:
        with self._lock:
            return self._cycles_since_progress >= self._thresholds.terminal_threshold

    def to_checkpoint_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": self.schema_version,
                "plateau_epoch_id": self._plateau_epoch_id,
                "credited_fact_ids_seen": sorted(self._credited_fact_ids_seen),
                "last_observed_current_state_id": self._last_observed_current_state_id,
                "cycles_since_progress": self._cycles_since_progress,
                "refresh_performed_in_epoch": self._refresh_performed_in_epoch,
                "replan_performed_in_epoch": self._replan_performed_in_epoch,
            }

    @classmethod
    def from_checkpoint_dict(
        cls,
        data: Any,
        *,
        max_no_progress_plateau: int = 6,
    ) -> "ConvergenceStateV1":
        if not isinstance(data, dict):
            raise ValueError("W15 convergence checkpoint must be an object")
        required = {
            "schema_version", "plateau_epoch_id",
            "credited_fact_ids_seen",
            "last_observed_current_state_id",
            "cycles_since_progress",
            "refresh_performed_in_epoch", "replan_performed_in_epoch",
        }
        if set(data) != required:
            raise ValueError("W15 convergence checkpoint has unknown or missing fields")
        if data["schema_version"] != 1:
            raise ValueError("unsupported W15 convergence checkpoint schema")
        epoch = data["plateau_epoch_id"]
        if not isinstance(epoch, str) or not _HEX_FINGERPRINT.fullmatch(epoch):
            raise ValueError("invalid W15 plateau epoch fingerprint")
        current = data["last_observed_current_state_id"]
        if not isinstance(current, str) or len(current) > 512:
            raise ValueError("invalid W15 current state fingerprint")
        raw_ids = data["credited_fact_ids_seen"]
        if not isinstance(raw_ids, list) or any(type(item) is not str or not item.strip() for item in raw_ids):
            raise ValueError("invalid W15 credited fact ledger")
        ids = _normalize_ids(raw_ids)
        if ids != tuple(raw_ids):
            raise ValueError("W15 credited fact ledger must be sorted and unique")
        cycles = data["cycles_since_progress"]
        if isinstance(cycles, bool) or not isinstance(cycles, int) or not 0 <= cycles <= MAX_CHECKPOINT_CYCLES:
            raise ValueError("invalid W15 convergence cycle counter")
        refresh = data["refresh_performed_in_epoch"]
        replan = data["replan_performed_in_epoch"]
        if type(refresh) is not bool or type(replan) is not bool:
            raise ValueError("W15 convergence action flags must be strict booleans")
        owner = cls(max_no_progress_plateau)
        with owner._lock:
            owner._plateau_epoch_id = epoch
            owner._credited_fact_ids_seen = set(ids)
            owner._last_observed_current_state_id = current
            owner._cycles_since_progress = cycles
            owner._refresh_performed_in_epoch = refresh
            owner._replan_performed_in_epoch = replan
            owner._last_progress_dimensions = ()
            owner._terminal_event_emitted = False
            owner._bootstrapped = True
        return owner

    restore_checkpoint_dict = from_checkpoint_dict

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": self.schema_version,
                "plateau_epoch_id": self._plateau_epoch_id,
                "credited_fact_ids_seen": sorted(self._credited_fact_ids_seen),
                "last_observed_current_state_id": self._last_observed_current_state_id,
                "cycles_since_progress": self._cycles_since_progress,
                "refresh_performed_in_epoch": self._refresh_performed_in_epoch,
                "replan_performed_in_epoch": self._replan_performed_in_epoch,
                "stage": self._derived_stage_locked(),
                "refresh_threshold": self._thresholds.refresh_threshold,
                "replan_threshold": self._thresholds.replan_threshold,
                "terminal_threshold": self._thresholds.terminal_threshold,
                "last_progress_dimensions": list(self._last_progress_dimensions),
            }

    def __deepcopy__(self, memo: dict[int, Any]) -> "ConvergenceStateV1":
        """Copy the task owner without attempting to deepcopy its thread lock."""

        del memo
        clone = type(self)(self.max_no_progress_plateau)
        with self._lock:
            clone._plateau_epoch_id = self._plateau_epoch_id
            clone._credited_fact_ids_seen = set(self._credited_fact_ids_seen)
            clone._last_observed_current_state_id = self._last_observed_current_state_id
            clone._cycles_since_progress = self._cycles_since_progress
            clone._refresh_performed_in_epoch = self._refresh_performed_in_epoch
            clone._replan_performed_in_epoch = self._replan_performed_in_epoch
            clone._last_progress_dimensions = tuple(self._last_progress_dimensions)
            clone._terminal_event_emitted = self._terminal_event_emitted
            clone._bootstrapped = self._bootstrapped
        return clone


def validate_convergence_checkpoint(data: Any, *, max_no_progress_plateau: int = 6) -> ConvergenceStateV1:
    """Validate the closed W15 convergence object at every durable boundary."""

    return ConvergenceStateV1.from_checkpoint_dict(data, max_no_progress_plateau=max_no_progress_plateau)


__all__ = [
    "ConvergenceAccountingContext",
    "ConvergenceObservation",
    "ConvergenceStateV1",
    "ConvergenceThresholds",
    "MAX_CHECKPOINT_CYCLES",
    "MAX_PLATEAU",
    "MIN_PLATEAU",
    "WATCHDOG_NO_PROGRESS_PLATEAU",
    "validate_convergence_checkpoint",
]
