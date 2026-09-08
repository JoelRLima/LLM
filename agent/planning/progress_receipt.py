"""Canonical, bounded progress receipts for long-horizon execution.

The receipt is a pure projection.  It deliberately keeps the complete
canonical credit set separate from the small presentation projection used by
model/inspector surfaces.  Convergence code is the only owner allowed to
turn a receipt delta into a plateau decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from agent.planning.progress_receipt_state import canonical_progress_inputs
from agent.planning.progress_receipt_units import extract_plan_units

SCHEMA_VERSION = 1
MAX_CREDIT_FACT_IDS = 4096
MAX_CREDIT_PROJECTION = 24

_EPHEMERAL_FIELDS = frozenset(
    {
        "invocation_id",
        "request_id",
        "timestamp",
        "created_at",
        "updated_at",
        "elapsed",
        "elapsed_seconds",
        "wall_time",
        "token_count",
        "input_tokens",
        "output_tokens",
        "usage",
        "heartbeat",
    }
)
_RAW_TEXT_FIELDS = frozenset(
    {
        "prompt",
        "content",
        "body",
        "text",
        "message",
        "summary",
        "raw",
        "secret",
        "credentials",
    }
)


def _json_safe(value: Any, *, field: str | None = None) -> Any:
    """Return bounded metadata suitable for a stable digest.

    Receipt identity must never depend on raw model prose or source bodies.
    Unknown structured values are represented by their type name rather than a
    memory-address-bearing ``repr``.
    """

    if field is not None and field.casefold() in _EPHEMERAL_FIELDS:
        return None
    if field is not None and field.casefold() in _RAW_TEXT_FIELDS:
        return "<omitted>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            if key.casefold() in _EPHEMERAL_FIELDS:
                continue
            result[key] = _json_safe(raw_value, field=key)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        values = [_json_safe(item) for item in value]
        return sorted(values, key=lambda item: _canonical_json(item))
    if value is None or type(value) in (str, int, float, bool):
        return value
    enum_value = getattr(value, "value", None)
    if type(enum_value) in (str, int):
        return enum_value
    return type(value).__name__


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_digest(value: Any) -> str:
    """Produce the repository-wide deterministic SHA-256-style identity."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_text(value: Any) -> str:
    raw = str(getattr(value, "value", value)).strip()
    return raw.replace("\\", "/")


def _unique_sorted(values: Sequence[Any] | set[Any] | frozenset[Any]) -> tuple[str, ...]:
    normalized = {_stable_text(value) for value in values if _stable_text(value)}
    return tuple(sorted(normalized))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=_canonical_json))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return converted
    return {}


def _status(value: Any) -> str:
    return _stable_text(getattr(value, "value", value)).casefold()


def _fact_id(prefix: str, value: Any) -> str:
    explicit = value if isinstance(value, str) else None
    if explicit is not None and explicit.strip() and len(explicit.strip()) <= 512:
        return explicit.strip().replace("\\", "/")
    return f"{prefix}:{stable_digest(value)}"


def _observation_credit_id(value: Mapping[str, Any]) -> str | None:
    freshness = str(value.get("freshness", "")).casefold()
    if freshness in {"stale", "stale_or_invalid_file_fact"}:
        return None
    classification = str(
        getattr(value.get("classification"), "value", value.get("classification", ""))
    ).casefold()
    if classification in {"cache_reuse", "redundant"}:
        return None
    explicit = value.get("credit_fact_id")
    if isinstance(explicit, str) and explicit.strip():
        return _fact_id("observation", explicit)
    relevant = any(
        bool(value.get(key))
        for key in (
            "satisfies_pending_need",
            "satisfies_pending",
            "relevant_pending_need",
            "new_canonical_evidence",
            "canonical_credit",
        )
    )
    if not relevant:
        return None
    provenance = str(
        value.get("evidence_provenance", value.get("provenance", "UNKNOWN"))
    ).casefold()
    if (
        provenance not in {"exact_source", "bounded_source"}
        or value.get("complete") is not True
        or value.get("truncated") is not False
    ):
        return None
    identity = value.get("source_identity", value.get("source_id", value.get("path", "")))
    source_hash = value.get("source_hash", value.get("hash", ""))
    extent = value.get("source_extent", value.get("extent", {}))
    return _fact_id(
        "evidence",
        {"identity": identity, "hash": source_hash, "extent": extent, "need": value.get("pending_need")},
    )


@dataclass(frozen=True, slots=True)
class ProgressDelta:
    """Pure before/after comparison over complete receipt sets."""

    raw_new_credit_fact_ids: tuple[str, ...] = ()
    lost_credit_fact_ids: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    current_state_changed: bool = False

    @property
    def advanced(self) -> bool:
        return bool(self.raw_new_credit_fact_ids)

    @property
    def new_credit_fact_ids(self) -> tuple[str, ...]:
        return self.raw_new_credit_fact_ids

    @property
    def task_new_credit_fact_ids(self) -> tuple[str, ...]:
        """Compatibility projection; convergence subtracts its seen ledger."""

        return self.raw_new_credit_fact_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_new_credit_fact_ids": list(self.raw_new_credit_fact_ids),
            "lost_credit_fact_ids": list(self.lost_credit_fact_ids),
            "dimensions": list(self.dimensions),
            "current_state_changed": self.current_state_changed,
            "advanced": self.advanced,
        }


@dataclass(frozen=True, slots=True)
class ProgressReceiptV1:
    """Immutable complete canonical progress projection for one state."""

    schema_version: int = SCHEMA_VERSION
    current_state_id: str = ""
    credit_fact_ids: tuple[str, ...] = ()
    credit_fact_projection: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    pending_unit_ids: tuple[str, ...] = ()
    running_unit_ids: tuple[str, ...] = ()
    completed_unit_ids: tuple[str, ...] = ()
    aggregate_progress_id: str | None = None
    canonical_state: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported ProgressReceiptV1 schema")
        credits = _unique_sorted(self.credit_fact_ids)
        if len(credits) > MAX_CREDIT_FACT_IDS:
            raise ValueError("complete progress credit set exceeds bounded owner limit")
        object.__setattr__(self, "credit_fact_ids", credits)
        projection = _unique_sorted(self.credit_fact_projection or credits[:MAX_CREDIT_PROJECTION])
        if not set(projection).issubset(set(credits)):
            raise ValueError("credit presentation must be a projection of complete credits")
        object.__setattr__(self, "credit_fact_projection", projection)
        object.__setattr__(self, "dimensions", _unique_sorted(self.dimensions))
        object.__setattr__(self, "pending_unit_ids", _unique_sorted(self.pending_unit_ids))
        object.__setattr__(self, "running_unit_ids", _unique_sorted(self.running_unit_ids))
        object.__setattr__(self, "completed_unit_ids", _unique_sorted(self.completed_unit_ids))
        state = _freeze(_json_safe(self.canonical_state))
        object.__setattr__(self, "canonical_state", state if isinstance(state, Mapping) else MappingProxyType({}))
        state_id = self.current_state_id.strip() if isinstance(self.current_state_id, str) else ""
        if len(state_id) > 512:
            raise ValueError("current_state_id exceeds bounded receipt limit")
        if not state_id:
            state_id = f"state:{stable_digest(self.canonical_state)}"
        object.__setattr__(self, "current_state_id", state_id)
        aggregate = self.aggregate_progress_id
        expected = f"progress:{stable_digest(list(credits))}"
        if aggregate is not None and aggregate != expected:
            raise ValueError("aggregate_progress_id must derive from complete credit set")
        object.__setattr__(self, "aggregate_progress_id", expected)

    @property
    def receipt_id(self) -> str:
        return self.aggregate_progress_id or ""

    @property
    def complete_credit_fact_ids(self) -> tuple[str, ...]:
        return self.credit_fact_ids

    @property
    def current_state_identity(self) -> str:
        return self.current_state_id

    def to_dict(self) -> dict[str, Any]:
        omitted = len(self.credit_fact_ids) - len(self.credit_fact_projection)
        projection = {
            "items": list(self.credit_fact_projection),
            "complete": omitted == 0,
            "truncated": omitted > 0,
            "total_count": len(self.credit_fact_ids),
            "omitted_count": omitted,
        }
        return {
            "schema_version": self.schema_version,
            "current_state_id": self.current_state_id,
            "credit_fact_ids": list(self.credit_fact_ids),
            "aggregate_progress_id": self.aggregate_progress_id,
            "credit_fact_projection": projection,
            "dimensions": list(self.dimensions),
            "pending_unit_ids": list(self.pending_unit_ids),
            "running_unit_ids": list(self.running_unit_ids),
            "completed_unit_ids": list(self.completed_unit_ids),
        }


def compare_progress_receipts(before: ProgressReceiptV1, after: ProgressReceiptV1) -> ProgressDelta:
    """Compare complete canonical sets; never compare the presentation sample."""

    before_ids = set(before.credit_fact_ids)
    after_ids = set(after.credit_fact_ids)
    raw_new = after_ids - before_ids
    # A plan/step UUID that only appears after a one-shot reactive attempt is
    # not progress by itself.  Terminalization earns credit only when the
    # logical unit was already part of the before frontier.
    before_units = set(before.pending_unit_ids) | set(before.running_unit_ids) | set(before.completed_unit_ids)
    filtered_new = {
        fact_id
        for fact_id in raw_new
        if not fact_id.startswith("step_terminal:")
        or fact_id.removeprefix("step_terminal:") in before_units
    }
    dimensions = {
        fact_id.split(":", 1)[0]
        for fact_id in filtered_new
        if ":" in fact_id
    }
    return ProgressDelta(
        raw_new_credit_fact_ids=tuple(sorted(filtered_new)),
        lost_credit_fact_ids=tuple(sorted(before_ids - after_ids)),
        dimensions=tuple(sorted(dimensions)),
        current_state_changed=before.current_state_id != after.current_state_id,
    )


def _history_receipt(history: Sequence[Mapping[str, Any]]) -> ProgressReceiptV1:
    facts: set[str] = set()
    entries: list[Any] = []
    for item in history:
        result = item.get("result") if isinstance(item, Mapping) else None
        result_mapping = _mapping(result)
        semantic = {
            "tool": item.get("tool", ""),
            "args": item.get("args", {}),
            "result": {
                key: result_mapping.get(key)
                for key in ("status", "ok", "executed", "error_code", "complete", "truncated")
                if key in result_mapping
            },
        }
        if "data" in result_mapping:
            # Result data is canonical evidence for this compatibility
            # receipt, while raw prose/summary fields remain excluded.
            semantic["result"]["data_digest"] = stable_digest(result_mapping.get("data"))
        digest = stable_digest(semantic)
        facts.add(f"reasoning:{digest}")
        entries.append(semantic)
    return ProgressReceiptV1(
        current_state_id=f"state:{stable_digest(entries)}",
        credit_fact_ids=tuple(sorted(facts)),
        dimensions=("reasoning",) if facts else (),
        canonical_state={"entries": entries},
    )


def progress_receipt_from_history(history: Sequence[Mapping[str, Any]]) -> ProgressReceiptV1:
    """Build a bounded semantic receipt for the reasoning compatibility edge."""

    return _history_receipt(history)


def _semantic_projection(semantics: Any) -> tuple[list[dict[str, Any]], set[str]]:
    credits: set[str] = set()
    semantic_items: Sequence[Any] = ()
    snapshot = getattr(semantics, "snapshot", None)
    if callable(snapshot):
        try:
            candidate = snapshot()
            semantic_items = candidate if isinstance(candidate, Sequence) else ()
        except Exception:
            semantic_items = ()
    semantic_projection: list[dict[str, Any]] = []
    for item in semantic_items:
        mapping = _mapping(item)
        identifier = _stable_text(mapping.get("id", ""))
        status = _status(mapping.get("status", "pending"))
        if identifier:
            semantic_projection.append({"id": identifier, "status": status})
            if status == "satisfied":
                credits.add(f"semantic:{identifier}")
    for effect in getattr(semantics, "_executed_effects", ()):
        if isinstance(effect, str) and effect.strip():
            credits.add(f"effect:{effect.strip()}")
    return semantic_projection, credits


def build_progress_receipt(
    state: Any = None,
    *,
    plan: Any = None,
    step_records: Mapping[str, Any] | None = None,
    graph_state: Any = None,
    task_semantics: Any = None,
    observations: Sequence[Mapping[str, Any]] | None = None,
    observation_receipts: Sequence[Mapping[str, Any]] | None = None,
    validation: Mapping[str, Any] | None = None,
    mutation: Mapping[str, Any] | None = None,
    grounded_target_ids: Sequence[str] | None = None,
    facts: Mapping[str, Any] | None = None,
    credit_fact_ids: Sequence[str] = (),
    max_credit_projection: int = MAX_CREDIT_PROJECTION,
) -> ProgressReceiptV1:
    """Build a fresh pure receipt from current canonical owners.

    The function accepts small explicit projections for adapters/tests, while
    the normal path reads only canonical typed state and bounded metadata.
    It never scans the workspace, calls a model/tool, or mutates its inputs.
    """

    state_inputs = canonical_progress_inputs(state)
    selected_facts = facts if isinstance(facts, Mapping) else state_inputs["facts"]
    semantics = task_semantics if task_semantics is not None else getattr(state, "task_semantics", None)
    pending, running, completed, unit_ids, units = extract_plan_units(
        state, plan, step_records, graph_state
    )
    credits: set[str] = {_stable_text(item) for item in credit_fact_ids if _stable_text(item)}
    for item in selected_facts.get("credit_fact_ids", ()):
        if isinstance(item, str) and item.strip():
            credits.add(item.strip())
    for unit_id in completed:
        credits.add(f"step_terminal:{unit_id}")

    semantic_projection, semantic_credits = _semantic_projection(semantics)
    credits.update(semantic_credits)
    if observation_receipts is not None:
        selected_observations = observation_receipts
    elif observations is not None:
        selected_observations = observations
    else:
        selected_observations = state_inputs["observations"]
    observation_projection: list[dict[str, Any]] = []
    for item in selected_observations:
        if not isinstance(item, Mapping):
            continue
        observation_projection.append(
            {
                key: _json_safe(item.get(key), field=key)
                for key in (
                    "source_identity",
                    "source_id",
                    "source_hash",
                    "source_extent",
                    "evidence_provenance",
                    "freshness",
                    "pending_need",
                    "satisfies_pending_need",
                    "satisfies_pending",
                    "relevant_pending_need",
                    "provenance",
                    "complete",
                    "truncated",
                    "reusable_exact_bytes",
                    "classification",
                    "physical_execution",
                    "credit_fact_id",
                    "new_canonical_evidence",
                    "canonical_credit",
                )
                if key in item
            }
        )
        observed_credit = _observation_credit_id(item)
        if observed_credit is not None:
            credits.add(observed_credit)
    selected_validation = (
        validation if validation is not None else state_inputs["validation"]
    )
    validation_projection: dict[str, Any] = {}
    if isinstance(selected_validation, Mapping):
        validation_projection = {
            key: _json_safe(selected_validation.get(key), field=key)
            for key in ("identity", "validation_id", "status", "mutation_identity", "freshness")
            if key in selected_validation
        }
        status = _status(selected_validation.get("status", ""))
        identity = selected_validation.get("identity", selected_validation.get("validation_id"))
        if status in {"passed", "succeeded", "valid", "complete"} and identity:
            mutation_identity = selected_validation.get("mutation_identity")
            validation_key = {
                "identity": _stable_text(identity),
                "mutation_identity": _stable_text(mutation_identity)
                if mutation_identity
                else None,
            }
            credits.add(f"validation:{stable_digest(validation_key)}")
    selected_mutation = mutation if mutation is not None else state_inputs["mutation"]
    mutation_projection: dict[str, Any] = {}
    if isinstance(selected_mutation, Mapping):
        mutation_projection = {
            key: _json_safe(selected_mutation.get(key), field=key)
            for key in ("identity", "mutation_id", "source_hash", "status", "freshness")
            if key in selected_mutation
        }
    selected_grounded = (
        grounded_target_ids
        if grounded_target_ids is not None
        else state_inputs["grounded_target_ids"]
    )
    for target_id in selected_grounded:
        if isinstance(target_id, str) and target_id.strip():
            credits.add(f"grounding:{target_id.strip()}")
    for target_id in selected_facts.get("grounded_target_ids", ()):
        if isinstance(target_id, str) and target_id.strip():
            credits.add(f"grounding:{target_id.strip()}")

    canonical_state = {
        "units": units,
        "pending": list(pending),
        "running": list(running),
        "completed": list(completed),
        "semantics": semantic_projection,
        "observations": observation_projection,
        "validation": validation_projection,
        "mutation": mutation_projection,
        "grounded_targets": sorted(_stable_text(item) for item in selected_grounded if _stable_text(item)),
        "task_root": _stable_text(getattr(state, "root_task_id", "")) if state is not None else "",
        "plan_identity": _stable_text(getattr(state, "plan_identity", "")) if state is not None else "",
    }
    explicit_current = selected_facts.get("current_state_id")
    state_id = _stable_text(explicit_current) if isinstance(explicit_current, str) and explicit_current.strip() else ""
    if not state_id:
        state_id = f"state:{stable_digest(canonical_state)}"
    complete_credits = tuple(sorted(credits))
    projection_limit = max(0, min(int(max_credit_projection), MAX_CREDIT_PROJECTION))
    projection = complete_credits[:projection_limit]
    dimensions = set(str(item) for item in selected_facts.get("dimensions", ()) if str(item).strip())
    dimensions.update(
        item.split(":", 1)[0]
        for item in complete_credits
        if ":" in item
    )
    return ProgressReceiptV1(
        current_state_id=state_id,
        credit_fact_ids=complete_credits,
        credit_fact_projection=projection,
        dimensions=tuple(sorted(dimensions)),
        pending_unit_ids=pending,
        running_unit_ids=running,
        completed_unit_ids=completed,
        canonical_state=canonical_state,
    )


build_progress_receipt_v1 = build_progress_receipt
compare_progress_receipt = compare_progress_receipts


__all__ = [
    "MAX_CREDIT_FACT_IDS",
    "MAX_CREDIT_PROJECTION",
    "canonical_progress_inputs",
    "ProgressDelta",
    "ProgressReceiptV1",
    "build_progress_receipt",
    "build_progress_receipt_v1",
    "compare_progress_receipt",
    "compare_progress_receipts",
    "progress_receipt_from_history",
    "stable_digest",
]
