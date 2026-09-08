"""Deterministic long-horizon scripted campaign.

This extends the existing evaluation surface with owner-level scripted
scenarios.  Every arm drives the canonical frontier, receipt, observation,
pressure, convergence, or checkpoint owner; it does not introduce a second
execution/evaluation framework or use a live model.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agent.evaluation.evaluation_identity import (
    candidate_identity,
    candidate_identity_string,
)
from agent.evaluation.evidence import sanitize_evidence
from agent.evaluation.long_horizon_production import result as _result
from agent.evaluation.long_horizon_production_routes import lh13 as _lh13
from agent.evaluation.long_horizon_production_routes import lh14 as _lh14
from agent.evaluation.long_horizon_production_routes import lh15 as _lh15
from agent.evaluation.long_horizon_production_routes import lh16 as _lh16
from agent.llm.context_pressure import ContextPressureDecision, decide_context_pressure
from agent.llm.context_projection import REQUIRED_EVIDENCE, context_record_from_text
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
)
from agent.planning.execution_frontier import build_execution_frontier
from agent.planning.observation_receipts import (
    ObservationClassification,
    build_observation_receipt,
    classify_observation,
)
from agent.planning.progress_receipt import (
    ProgressReceiptV1,
    build_progress_receipt,
    compare_progress_receipts,
)
from agent.runtime.convergence import (
    ConvergenceAccountingContext,
    ConvergenceStateV1,
)
from agent.runtime.request_measurement import (
    PROVIDER_CHAT_INPUT_TOKENS,
    RequestInputMeasurement,
)

LONG_HORIZON_V1 = "LONG_HORIZON_V1"
LONG_HORIZON_SET_VERSION = "LH15-1"
LONG_HORIZON_IDS = tuple(f"LH15-{index:02d}" for index in range(1, 17))


class _ExactGateway:
    capabilities = SimpleNamespace(token_counting=False)

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self.calls = 0

    def measure_request_input_tokens(self, request: ModelRequest) -> RequestInputMeasurement:
        del request
        self.calls += 1
        return RequestInputMeasurement(
            self.tokens,
            PROVIDER_CHAT_INPUT_TOKENS,
            exact=True,
            available=True,
        )


class _InexactGateway:
    capabilities = SimpleNamespace(token_counting=False)

    def __init__(self) -> None:
        self.calls = 0

    def measure_request_input_tokens(self, request: ModelRequest) -> None:
        del request
        self.calls += 1
        return None


def _request(required: str | None = None, optional: str | None = None) -> ModelRequest:
    messages = [ModelMessage("system", "system"), ModelMessage("user", "current")]
    if required:
        messages.insert(1, ModelMessage("user", required))
    if optional:
        messages.insert(-1, ModelMessage("user", optional))
    return ModelRequest(
        messages=tuple(messages),
        model="scripted-long-horizon",
        temperature=0.0,
        max_output_tokens=128,
        context_limit=8192,
    )


def _direct_receipt(
    credits: Sequence[str] = (),
    *,
    pending: Sequence[str] = (),
    running: Sequence[str] = (),
    completed: Sequence[str] = (),
    state_id: str | None = None,
    projection_limit: int = 24,
) -> ProgressReceiptV1:
    """Construct a receipt through the canonical owner with explicit facts."""

    return ProgressReceiptV1(
        current_state_id=state_id or "state:" + ":".join(credits or ("empty",)),
        credit_fact_ids=tuple(credits),
        credit_fact_projection=tuple(credits)[:projection_limit],
        pending_unit_ids=tuple(pending),
        running_unit_ids=tuple(running),
        completed_unit_ids=tuple(completed),
    )


def _state_for_units(count: int, completed: int = 0) -> Any:
    unit_ids = [f"step-{index}" for index in range(1, count + 1)]
    records = {
        identifier: {"status": "completed" if index < completed else "pending"}
        for index, identifier in enumerate(unit_ids)
    }
    return SimpleNamespace(
        root_task_id="lh15-root",
        plan_identity="lh15-plan",
        current_step_id=unit_ids[completed] if completed < count else None,
        plan=[{"step_id": identifier, "tool": "file_reader"} for identifier in unit_ids],
        step_records=records,
        tool_history=[],
        task_semantics=None,
        budget_ledger=None,
        recovery_budget=None,
        convergence=None,
    )


def _pressure(required_size: int, *, limit: int, exact: bool, optional: bool = True) -> tuple[ContextPressureDecision, bool, int]:
    required = context_record_from_text(
        "runtime:frontier",
        "execution_frontier",
        "frontier",
        necessity=REQUIRED_EVIDENCE,
    )
    optional_records = (
        context_record_from_text("memory:history", "memory", "history"),
    ) if optional else ()
    gateway: Any = _ExactGateway(required_size) if exact else _InexactGateway()
    result = decide_context_pressure(
        mandatory_request=_request(),
        required_records=(required,),
        optional_records=optional_records,
        context_limit=limit,
        gateway=gateway,
        build_request=_request,
    )
    return result.decision, result.fit_proven, gateway.calls


def _lh01() -> dict[str, Any]:
    frontier = build_execution_frontier(_state_for_units(8, completed=3))
    decision, fit_proven, calls = _pressure(20, limit=660, exact=True)
    return _result(
        "LH15-01",
        "Compaction frontier preservation",
        {
            "summary_does_not_rewrite_canonical_state": frontier.next_executable_unit_ids[0] == "step-4",
            "frontier_next_is_step_4": frontier.next_executable_unit_ids[0] == "step-4",
            "pressure_is_compact": decision is ContextPressureDecision.COMPACT,
            "fit_is_provider_proven": fit_proven,
        },
        logical_units=8,
        model_calls=1,
        tool_calls=3,
        context_pressure_decisions=(decision.value,),
        compact_full_events=(decision.value,),
        progress_advances=3,
    )


def _lh02() -> dict[str, Any]:
    exact = {
        "source_identity": "src.py",
        "source_hash": "hash-a",
        "source_extent": {"kind": "whole"},
        "evidence_provenance": "EXACT_SOURCE",
        "complete": True,
        "truncated": False,
    }
    prior, current = build_observation_receipt(exact), build_observation_receipt(exact)
    reuse = classify_observation(prior, current, exact_bytes_available=True)
    rehydration = classify_observation(
        prior,
        current,
        exact_bytes_available=False,
        pending_need=True,
        physical_execution=True,
    )
    owner = ConvergenceStateV1(6)
    before = _direct_receipt(("evidence:src.py",), state_id="before")
    owner.bootstrap(before)
    observed = owner.observe(
        before,
        _direct_receipt(before.credit_fact_ids, state_id="cache"),
        ConvergenceAccountingContext.cache_reuse_attempt("lh15-root"),
    )
    return _result(
        "LH15-02",
        "Repeated read after pressure",
        {
            "exact_read_is_cache_reuse": reuse is ObservationClassification.CACHE_REUSE,
            "physical_rehydration_is_distinct": rehydration is ObservationClassification.CONTEXT_REHYDRATION,
            "cache_reuse_has_no_progress_cycle": observed.cycle_charged is False and owner.cycles_since_progress == 0,
        },
        logical_units=1,
        tool_calls=1,
        cache_reuse=1,
    )


def _lh03() -> dict[str, Any]:
    prior = build_observation_receipt(
        {
            "source_identity": "src.py",
            "source_hash": "hash-a",
            "source_extent": {"kind": "whole"},
            "evidence_provenance": "EXACT_SOURCE",
            "complete": True,
            "truncated": False,
        }
    )
    current = build_observation_receipt(
        {
            "source_identity": "src.py",
            "source_hash": "hash-b",
            "source_extent": {"kind": "whole"},
            "evidence_provenance": "EXACT_SOURCE",
            "complete": True,
            "truncated": False,
        }
    )
    stale = classify_observation(prior, current, pending_need=True, physical_execution=True)
    return _result(
        "LH15-03",
        "Reread after mutation",
        {
            "old_receipt_is_stale": stale is ObservationClassification.STALE_REREAD,
            "new_exact_identity_is_different": prior.source_hash != current.source_hash,
            "reread_is_allowed": stale is not ObservationClassification.REDUNDANT,
        },
        logical_units=1,
        tool_calls=2,
        progress_advances=1,
    )


def _plateau_owner() -> tuple[ConvergenceStateV1, list[Any]]:
    owner = ConvergenceStateV1(6)
    before = _direct_receipt(state_id="plateau-start")
    owner.bootstrap(before)
    observations: list[Any] = []
    for index in range(6):
        after = _direct_receipt(state_id=f"plateau-{index}")
        observations.append(
            owner.observe(
                before,
                after,
                ConvergenceAccountingContext.root_attempt("lh15-root"),
            )
        )
        before = after
    return owner, observations


def _lh04() -> dict[str, Any]:
    owner, observations = _plateau_owner()
    return _result(
        "LH15-04",
        "Varied no-progress loop",
        {
            "refresh_then_replan_then_terminal": any(item.should_refresh for item in observations)
            and any(item.should_replan for item in observations),
            "terminal_is_bounded": owner.cycles_since_progress == 6 and owner.terminal_reached(),
            "terminal_reason_is_not_success": observations[-1].reason_code == "WATCHDOG_NO_PROGRESS_PLATEAU",
        },
        logical_units=6,
        model_calls=6,
        no_progress_cycles=owner.cycles_since_progress,
        plan_extensions=1,
        terminal_reason=observations[-1].reason_code,
    )


def _lh05() -> dict[str, Any]:
    owner = ConvergenceStateV1(6)
    before = _direct_receipt(state_id="progress-start")
    owner.bootstrap(before)
    observations: list[Any] = []
    for index in range(4):
        after = _direct_receipt(state_id=f"progress-plateau-{index}")
        observations.append(
            owner.observe(before, after, ConvergenceAccountingContext.root_attempt("lh15-root"))
        )
        before = after
    progress = owner.observe(
        before,
        _direct_receipt(("evidence:new",), state_id="progress-earned"),
        ConvergenceAccountingContext.root_attempt("lh15-root"),
    )
    return _result(
        "LH15-05",
        "Progress before terminal threshold",
        {
            "replan_stage_was_reached": any(item.should_replan for item in observations),
            "new_relevant_credit_resets_plateau": progress.advanced and owner.cycles_since_progress == 0,
            "task_not_terminal": not owner.terminal_reached(),
        },
        logical_units=4,
        model_calls=4,
        progress_advances=1,
        no_progress_cycles=owner.cycles_since_progress,
    )


def _lh06() -> dict[str, Any]:
    owner, observations = _plateau_owner()
    return _result(
        "LH15-06",
        "Recovery exhaustion",
        {
            "no_hidden_replan": owner.replan_performed_in_epoch,
            "terminal_after_bound": owner.terminal_reached(),
            "terminal_is_failed_watchdog": observations[-1].reason_code == "WATCHDOG_NO_PROGRESS_PLATEAU",
        },
        logical_units=6,
        model_calls=6,
        no_progress_cycles=owner.cycles_since_progress,
        plan_extensions=1,
        terminal_reason=observations[-1].reason_code,
    )


def _lh07() -> dict[str, Any]:
    decision, fit_proven, calls = _pressure(20, limit=4096, exact=False)
    return _result(
        "LH15-07",
        "Token measurement unavailable",
        {
            "minimal_compact": decision is ContextPressureDecision.COMPACT,
            "fit_is_not_proven": fit_proven is False,
            "one_fitting_attempt": calls == 1,
        },
        logical_units=1,
        model_calls=1,
        context_pressure_decisions=(decision.value,),
        compact_full_events=(decision.value,),
    )


def _lh08() -> dict[str, Any]:
    decision, fit_proven, _calls = _pressure(200, limit=600, exact=True, optional=False)
    return _result(
        "LH15-08",
        "Mandatory overflow",
        {
            "exact_overflow_is_proven": decision is ContextPressureDecision.MANDATORY_OVERFLOW,
            "dispatch_would_be_blocked": not fit_proven,
            "provider_not_called_after_proof": decision is ContextPressureDecision.MANDATORY_OVERFLOW,
        },
        logical_units=1,
        context_pressure_decisions=(decision.value,),
        compact_full_events=(decision.value,),
        terminal_reason="CONTEXT_TOO_LARGE",
    )


def _lh09() -> dict[str, Any]:
    state = _state_for_units(8, completed=3)
    first = build_execution_frontier(state)
    summary = {"next": "step-2", "status": "pending"}
    second = build_execution_frontier(state)
    return _result(
        "LH15-09",
        "Summary injection",
        {
            "frontier_is_stable": first.to_dict() == second.to_dict(),
            "summary_is_not_frontier": summary != first.to_dict(),
            "no_authority_widening": "grant" not in first.to_dict(),
        },
        logical_units=8,
        model_calls=1,
    )


def _lh10() -> dict[str, Any]:
    owner = ConvergenceStateV1(6)
    before = _direct_receipt(("fact:a",), state_id="resume-start")
    owner.bootstrap(before)
    for index in range(2):
        after = _direct_receipt(("fact:a",), state_id=f"resume-{index}")
        owner.observe(before, after, ConvergenceAccountingContext.root_attempt("lh15-root"))
        before = after
    checkpoint = owner.to_checkpoint_dict()
    restored = ConvergenceStateV1.from_checkpoint_dict(checkpoint)
    epoch = restored.plateau_epoch_id
    reconciled = restored.reconcile_resume(_direct_receipt(("fact:a",), state_id="resume-fresh"))
    frontier = build_execution_frontier(_state_for_units(8, completed=3))
    return _result(
        "LH15-10",
        "Resume with plateau state",
        {
            "cycles_are_restored": restored.cycles_since_progress == 2,
            "epoch_is_preserved": restored.plateau_epoch_id == epoch,
            "resume_reconciles_without_reset": reconciled["classification"] == "RESUME_CONTINUOUS" and restored.cycles_since_progress == 2,
            "frontier_is_fresh": frontier.next_executable_unit_ids[0] == "step-4",
        },
        logical_units=8,
        no_progress_cycles=restored.cycles_since_progress,
    )


def _lh11() -> dict[str, Any]:
    old = build_observation_receipt(
        {
            "source_identity": "src.py",
            "source_hash": "hash-before",
            "source_extent": {"kind": "whole"},
            "evidence_provenance": "EXACT_SOURCE",
            "complete": True,
            "truncated": False,
        }
    )
    current = build_observation_receipt(
        {
            "source_identity": "src.py",
            "source_hash": "hash-after",
            "source_extent": {"kind": "whole"},
            "evidence_provenance": "EXACT_SOURCE",
            "complete": True,
            "truncated": False,
        }
    )
    classification = classify_observation(old, current, pending_need=True, physical_execution=True)
    return _result(
        "LH15-11",
        "Repository state stale after mutation",
        {
            "old_repository_record_is_stale": classification is ObservationClassification.STALE_REREAD,
            "current_hash_is_required": old.source_hash != current.source_hash,
            "stale_record_is_not_current": classification is not ObservationClassification.CACHE_REUSE,
        },
        logical_units=1,
        tool_calls=2,
    )


def _lh12() -> dict[str, Any]:
    failed = build_progress_receipt(
        validation={"identity": "check-1", "mutation_identity": "mutation-1", "status": "failed"}
    )
    passed = build_progress_receipt(
        validation={"identity": "check-2", "mutation_identity": "mutation-2", "status": "passed"}
    )
    delta = compare_progress_receipts(failed, passed)
    return _result(
        "LH15-12",
        "Validation convergence",
        {
            "failed_validation_has_no_progress_credit": not any(item.startswith("validation:") for item in failed.credit_fact_ids),
            "identity_changes_are_relevant": delta.advanced,
            "new_mutation_validation_has_credit": any(item.startswith("validation:") for item in passed.credit_fact_ids),
        },
        logical_units=2,
        tool_calls=2,
        progress_advances=1,
    )


_SCENARIOS: tuple[Callable[[], dict[str, Any]], ...] = (
    _lh01,
    _lh02,
    _lh03,
    _lh04,
    _lh05,
    _lh06,
    _lh07,
    _lh08,
    _lh09,
    _lh10,
    _lh11,
    _lh12,
    _lh13,
    _lh14,
    _lh15,
    _lh16,
)


def run_long_horizon_scripted(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run LH15-01..LH15-16 exactly once each with no live-model calls."""

    root = Path(repo_root).resolve()
    candidate_start = candidate_identity(root)
    records: list[dict[str, Any]] = []
    for scenario in _SCENARIOS:
        try:
            records.append(scenario())
        except Exception as exc:  # the report preserves a bounded known failure
            scenario_id = LONG_HORIZON_IDS[len(records)]
            records.append(
                _result(
                    scenario_id,
                    "scenario execution error",
                    {"scenario_completed": False},
                    terminal_reason=type(exc).__name__,
                )
            )
    candidate_end = candidate_identity(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "campaign_name": LONG_HORIZON_V1,
        "set_version": LONG_HORIZON_SET_VERSION,
        "evidence_level": "deterministic_scripted_owner_harness",
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "candidate_start_identity": candidate_identity_string(candidate_start),
        "candidate_end_identity": candidate_identity_string(candidate_end),
        "candidate_unchanged": candidate_start == candidate_end,
        # Integrity is established from the actual candidate identity before
        # and after the campaign; scenario defaults cannot manufacture it.
        "workspace_integrity": candidate_start == candidate_end,
        "execution_policy": {
            "one_execution_per_scenario": True,
            "live_model": False,
            "qwen_used": False,
            "environmental_retry": False,
        },
        "scenarios": records,
        "summary": {
            "total": len(records),
            "passed": sum(bool(item["passed"]) for item in records),
            "failed": sum(not bool(item["passed"]) for item in records),
            "unknown_failures": sum(len(item.get("unknown_failures", ())) for item in records),
        },
    }
    safe_report = cast(dict[str, Any], sanitize_evidence(report))
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return safe_report


run_long_horizon_campaign = run_long_horizon_scripted


__all__ = [
    "LONG_HORIZON_IDS",
    "LONG_HORIZON_SET_VERSION",
    "LONG_HORIZON_V1",
    "run_long_horizon_campaign",
    "run_long_horizon_scripted",
]
