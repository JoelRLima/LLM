"""Small primitives used to validate H-series call-identity evidence."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.analysis_support import _evidence
from agent.llm.identity import GENERIC_MODEL_ALIASES

CALL_IDENTITY_FIELDS = (
    "call_index",
    "provider",
    "endpoint_identity",
    "declared_model",
    "observed_provider_model_id",
    "identity_source",
)
def ordered_identity_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if not isinstance(value, Mapping):
        return []
    sequences = value.get("sequences")
    if not isinstance(sequences, (list, tuple)):
        return []
    values = [
        item
        for sequence in sequences
        if isinstance(sequence, (list, tuple))
        for item in sequence
    ]
    return list(dict.fromkeys(values)) if value.get("unique") is True else values


def identity_aliases(declared: Any) -> set[str]:
    aliases = set(GENERIC_MODEL_ALIASES)
    if not isinstance(declared, Mapping):
        return aliases
    explicit = declared.get("generic_model_alias")
    if explicit not in (None, ""):
        aliases.add(str(explicit).casefold())
    for key in ("configured_model_id", "model"):
        value = declared.get(key)
        if value not in (None, "") and str(value).casefold() in GENERIC_MODEL_ALIASES:
            aliases.add(str(value).casefold())
    return aliases


def inspect_call_records(
    raw_calls: Any, prefix: str
) -> tuple[list[str], list[str], list[str], list[str], bool]:
    reasons: list[str] = []
    if not isinstance(raw_calls, (list, tuple)):
        return [], [], [], [f"{prefix}:model_call_identities_missing"], False
    if not raw_calls:
        reasons.append(f"{prefix}:model_call_identities_empty")
    observed_ids: list[str] = []
    providers: list[str] = []
    endpoints: list[str] = []
    for position, raw_call in enumerate(raw_calls, start=1):
        call_reasons, value, provider, endpoint = _inspect_call_record(raw_call, position, prefix)
        reasons.extend(call_reasons)
        if value not in (None, ""):
            observed_ids.append(str(value))
        if provider not in (None, ""):
            providers.append(str(provider))
        if endpoint not in (None, ""):
            endpoints.append(str(endpoint))
    return observed_ids, providers, endpoints, reasons, bool(raw_calls)


def _inspect_call_record(
    raw_call: Any, position: int, prefix: str
) -> tuple[list[str], Any, Any, Any]:
    if not isinstance(raw_call, Mapping):
        return [f"{prefix}:model_call_identity_invalid:{position}"], None, None, None
    reasons: list[str] = []
    missing = [field for field in CALL_IDENTITY_FIELDS if field not in raw_call]
    if missing:
        reasons.append(
            f"{prefix}:model_call_identity_fields_missing:{position}:{','.join(missing)}"
        )
    call_index = raw_call.get("call_index")
    if not isinstance(call_index, int) or isinstance(call_index, bool) or call_index != position:
        reasons.append(f"{prefix}:model_call_identity_order_invalid:{position}")
    if raw_call.get("identity_source") in (None, ""):
        reasons.append(f"{prefix}:model_call_identity_source_missing:{position}")
    return (
        reasons,
        raw_call.get("observed_provider_model_id"),
        raw_call.get("provider"),
        raw_call.get("endpoint_identity"),
    )


def external_identity(
    evidence: Mapping[str, Any], observed: Mapping[str, Any], prefix: str
) -> tuple[str | None, list[str]]:
    value = observed.get("external_identity")
    value = str(value).strip() if value not in (None, "") else None
    aliases = identity_aliases(evidence.get("declared_model_identity"))
    reasons: list[str] = []
    if value is not None and value.casefold() in aliases:
        value = None
    if value is not None and observed.get("external_identity_source") != "external_identity":
        reasons.append(f"{prefix}:external_identity_source_invalid")
    return value, reasons


def aggregate_identity_inputs(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "observed_ids": [],
        "providers": [],
        "endpoints": [],
        "external_identities": [],
        "complete": True,
        "eligible_count": 0,
        "run_consistent": True,
        "run_sufficient": True,
        "call_count": 0,
    }
    for run in runs:
        evidence = _evidence(run)
        if bool(run.get("environmental", False)) or not bool(
            run.get("valid_repetition", evidence.get("valid_repetition", True))
        ):
            continue
        state["eligible_count"] += 1
        raw_calls = evidence.get("model_call_identities")
        if not isinstance(raw_calls, (list, tuple)) or not raw_calls:
            state["complete"] = False
            continue
        state["call_count"] += len(raw_calls)
        observed = evidence.get("observed_model_identity")
        observed = observed if isinstance(observed, Mapping) else {}
        if observed.get("consistent") is False:
            state["run_consistent"] = False
        if observed.get("complete") is False:
            state["complete"] = False
        if observed.get("identity_sufficient") is not True:
            state["run_sufficient"] = False
        external = observed.get("external_identity")
        complete, ids, providers, endpoints = _aggregate_call_values(
            raw_calls,
            allow_missing_observed_id=external not in (None, ""),
        )
        state["complete"] = state["complete"] and complete
        state["observed_ids"].extend(ids)
        state["providers"].extend(providers)
        state["endpoints"].extend(endpoints)
        if external not in (None, ""):
            state["external_identities"].append(str(external).strip()[:256])
    return state


def _aggregate_call_values(
    raw_calls: list[Any] | tuple[Any, ...],
    *,
    allow_missing_observed_id: bool = False,
) -> tuple[bool, list[str], list[str], list[str]]:
    complete = True
    ids: list[str] = []
    providers: list[str] = []
    endpoints: list[str] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping) or any(field not in raw_call for field in CALL_IDENTITY_FIELDS):
            complete = False
            continue
        value = raw_call.get("observed_provider_model_id")
        if value not in (None, ""):
            ids.append(str(value))
        elif not allow_missing_observed_id:
            complete = False
        provider = raw_call.get("provider")
        if provider not in (None, ""):
            providers.append(str(provider))
        endpoint = raw_call.get("endpoint_identity")
        if endpoint not in (None, ""):
            endpoints.append(str(endpoint))
    return complete, ids, providers, endpoints


def aggregate_identity_projection(report: Mapping[str, Any], runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    state = aggregate_identity_inputs(runs)
    aliases = identity_aliases(report.get("declared_model_identity"))
    ordered_ids = list(state["observed_ids"])
    ids = list(dict.fromkeys(ordered_ids))
    providers = list(dict.fromkeys(state["providers"]))
    endpoints = list(dict.fromkeys(state["endpoints"]))
    external = [
        value for value in dict.fromkeys(state["external_identities"])
        if value.casefold() not in aliases
    ]
    stable_external = external[0] if len(external) == 1 else None
    consistent = (
        len(ids) <= 1
        and len(providers) <= 1
        and len(endpoints) <= 1
        and len(external) <= 1
        and state["run_consistent"]
    )
    specific = len(ids) == 1 and ids[0].casefold() not in aliases
    return {
        "available": bool(ids or stable_external),
        "provider_observation_available": bool(ids),
        "identity_sufficient": bool(
            state["eligible_count"]
            and state["complete"]
            and consistent
            and state["run_sufficient"]
            and (specific or stable_external)
        ),
        "consistent": consistent,
        "specific": specific,
        "complete": bool(state["eligible_count"]) and state["complete"],
        "observed_model_ids": ordered_ids,
        "distinct_observed_model_ids": ids,
        "provider_model_id": ids[0] if len(ids) == 1 else None,
        "actual_provider_model_id": ids[0] if len(ids) == 1 else None,
        "provider": providers[0] if len(providers) == 1 else None,
        "endpoint_identity": endpoints[0] if len(endpoints) == 1 else None,
        "external_identity": stable_external,
        "external_identity_source": "external_identity" if stable_external else None,
        "call_count": state["call_count"],
    }


__all__ = [
    "CALL_IDENTITY_FIELDS",
    "aggregate_identity_projection",
    "external_identity",
    "identity_aliases",
    "inspect_call_records",
    "ordered_identity_values",
]
