"""Campaign-wide observed-model identity aggregation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.evaluation.campaign_identity_records import (
    IdentityCollection,
    collect_identity_state,
    generic_aliases,
)
from agent.evaluation.scenario_contracts import MAX_EVIDENCE_ITEMS


def _bounded_identity_sequence(
    values: list[Any],
    sequences: list[list[Any]],
    *,
    unique: bool = False,
) -> Any:
    if len(values) <= MAX_EVIDENCE_ITEMS:
        return values
    return {
        "encoding": "per_run_ordered_identity_sequence",
        "sequences": sequences,
        "count": len(values),
        "unique": unique,
    }


def _identity_limitation(
    distinct_ids: list[str], provider_observed: bool, specific: bool
) -> str | None:
    if len(distinct_ids) > 1:
        return "observed_identity_drift"
    if provider_observed and not specific:
        return "generic_provider_model_id"
    if not provider_observed:
        return "backend_identity_unavailable"
    return None


def _identity_source(
    provider_observed: bool, specific: bool, stable_external: str | None
) -> str:
    if provider_observed and specific:
        return "response.provider_metadata"
    if stable_external:
        return "external_identity"
    if provider_observed:
        return "response.provider_metadata"
    return "unavailable"


def _identity_projection(
    state: IdentityCollection,
    aliases: set[str],
) -> dict[str, Any]:
    distinct_ids = list(dict.fromkeys(state.observed_ids))
    distinct_providers = list(dict.fromkeys(state.providers))
    distinct_endpoints = list(dict.fromkeys(state.endpoints))
    distinct_external = list(dict.fromkeys(state.external_identities))
    stable_external = distinct_external[0] if len(distinct_external) == 1 else None
    provider_observed = bool(state.observed_ids)
    specific = len(distinct_ids) == 1 and distinct_ids[0].casefold() not in aliases
    consistent = (
        len(distinct_ids) <= 1
        and len(distinct_providers) <= 1
        and len(distinct_endpoints) <= 1
        and len(distinct_external) <= 1
        and state.run_consistent
    )
    observed_model_id = distinct_ids[0] if len(distinct_ids) == 1 else None
    provider = distinct_providers[0] if len(distinct_providers) == 1 else None
    endpoint = distinct_endpoints[0] if len(distinct_endpoints) == 1 else None
    source = _identity_source(provider_observed, specific, stable_external)
    limitation = _identity_limitation(distinct_ids, provider_observed, specific)
    sufficient = bool(
        state.eligible_count
        and state.complete
        and consistent
        and state.run_sufficient
        and (specific or stable_external)
    )
    return {
        "available": provider_observed or bool(stable_external),
        "provider_observation_available": provider_observed,
        "identity_sufficient": sufficient,
        "consistent": consistent,
        "specific": specific,
        "complete": bool(state.eligible_count) and state.complete,
        "unavailable_run_count": state.unavailable_count,
        "identities": state.identities,
        "provider_model_id": observed_model_id,
        "actual_provider_model_id": observed_model_id,
        "model": observed_model_id,
        "provider": provider,
        "endpoint_identity": endpoint,
        "source": source,
        "identity_source": source,
        "observed_model_ids": _bounded_identity_sequence(
            state.observed_ids, state.run_id_sequences
        ),
        "distinct_observed_model_ids": _bounded_identity_sequence(
            distinct_ids,
            state.run_id_sequences,
            unique=True,
        ),
        "external_identity": stable_external,
        "external_identity_source": "external_identity" if stable_external else None,
        "external_identities": distinct_external,
        "provider_observation_limitation": limitation,
        "call_count": len(state.ordered_calls),
        "call_identities": _bounded_identity_sequence(
            state.ordered_calls, state.run_call_sequences
        ),
        "model_call_identities": _bounded_identity_sequence(
            state.ordered_calls, state.run_call_sequences
        ),
        "run_count": state.eligible_count,
    }


def _observed_identity_summary(
    records: Sequence[Mapping[str, Any]],
    declared_model_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate every eligible run without projecting a last-call identity."""
    aliases = generic_aliases(declared_model_identity)
    return _identity_projection(collect_identity_state(records, aliases), aliases)


__all__ = ["_observed_identity_summary"]
