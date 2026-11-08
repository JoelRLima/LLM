"""Request-stage selection for optional model context records.

The execution profile controls only which optional context records are offered
to the existing request fitter. Required runtime evidence and the canonical
trust/projection owners remain unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from llm_agent.agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    ContextSourceRecord,
)
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.runtime.execution_profile import (
    ExecutionProfile,
    resolve_execution_profile,
)

_DUPLICATED_OPTIONAL_KINDS: dict[ModelRequestContract, frozenset[str]] = {
    # PlanBuilder places these hints directly in the prompt; recent derived
    # history is not useful to the one-shot initial planning decision.
    ModelRequestContract.INITIAL_PLAN: frozenset(
        {"workspace_hints", "derived_session"}
    ),
    # These requests already carry bounded current progress/observations or
    # the recent observations directly in their focal prompt.
    ModelRequestContract.REACTIVE_TOOL_DECISION: frozenset(
        {"workspace_hints", "derived_session"}
    ),
    ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION: frozenset(
        {"workspace_hints", "derived_session"}
    ),
    ModelRequestContract.REASONING_BOUNDARY_CONTINUATION: frozenset(
        {"workspace_hints", "derived_session"}
    ),
    # Final generation does not select tools or files. Preserve scoped memory
    # and project guidance, which can still help formulate the answer.
    ModelRequestContract.FINAL_GENERATION: frozenset(
        {"workspace_hints", "derived_session"}
    ),
}


@dataclass(frozen=True, slots=True)
class StageContextSelection:
    """Optional records selected for one stage plus bounded diagnostics."""

    records: tuple[ContextSourceRecord, ...]
    excluded_source_kind_counts: tuple[tuple[str, int], ...] = ()


def project_stage_records(
    config: Mapping[str, Any],
    contract: ModelRequestContract | None,
    optional_records: Sequence[ContextSourceRecord],
) -> StageContextSelection:
    """Select compact-profile optional sources without changing record data.

    Unknown contracts and the standard profile preserve the original sequence
    exactly. Required evidence and project guidance are never removed.
    """

    records = tuple(optional_records)
    profile = resolve_execution_profile(config)
    if profile is not ExecutionProfile.COMPACT or contract not in _DUPLICATED_OPTIONAL_KINDS:
        return StageContextSelection(records)

    removable = _DUPLICATED_OPTIONAL_KINDS[contract]
    selected: list[ContextSourceRecord] = []
    excluded: Counter[str] = Counter()
    for record in records:
        kind = str(record.source_kind)
        # Applicable AGENTS.md guidance is policy-bearing project context and
        # stays available even when the rest of the stage is compacted.
        if (
            record.necessity != REQUIRED_EVIDENCE
            and kind in removable
            and kind != "project_guidance"
        ):
            excluded[kind] += 1
            continue
        selected.append(record)
    return StageContextSelection(
        tuple(selected),
        tuple(sorted(excluded.items())[:16]),
    )


def emit_stage_context_selection(
    manager: Any,
    selection: StageContextSelection,
    *,
    contract: ModelRequestContract | None,
) -> None:
    """Emit only bounded counts; context contents never enter the event."""

    if not selection.excluded_source_kind_counts:
        return
    emit = getattr(manager, "_emit_runtime_projection", None)
    if not callable(emit):
        return
    try:
        emit(
            "stage_context_selected",
            {
                "profile": "compact",
                "stage": contract.value if contract is not None else "unknown",
                "selected_record_count": len(selection.records),
                "excluded_record_count": sum(
                    count for _, count in selection.excluded_source_kind_counts
                ),
                "excluded_source_kind_counts": dict(
                    selection.excluded_source_kind_counts
                ),
            },
        )
    except Exception:
        # Stage-selection telemetry is observational and cannot alter dispatch.
        return


__all__ = [
    "StageContextSelection",
    "emit_stage_context_selection",
    "project_stage_records",
]
