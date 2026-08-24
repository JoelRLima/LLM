"""Per-run collection helpers for campaign model-identity evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

CALL_IDENTITY_FIELDS = (
    "call_index",
    "provider",
    "endpoint_identity",
    "declared_model",
    "observed_provider_model_id",
    "identity_source",
)
GENERIC_MODEL_ALIASES = frozenset({"default"})


def ordered_values(value: Any) -> list[Any]:
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


def identity_eligible(record: Mapping[str, Any]) -> bool:
    evidence = record.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    if bool(record.get("environmental", False)):
        return False
    return bool(record.get("valid_repetition", evidence.get("valid_repetition", True)))


def call_identity_values(record: Mapping[str, Any]) -> tuple[list[Any], bool]:
    """Return the bounded per-call list and whether it was losslessly exported."""

    evidence = record.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    raw_calls = evidence.get("model_call_identities")
    if isinstance(raw_calls, (list, tuple)):
        return list(raw_calls), True

    observed = evidence.get("observed_model_identity")
    observed = observed if isinstance(observed, Mapping) else {}
    ordered = ordered_values(observed.get("observed_model_ids"))
    ids = [value for value in ordered if value not in (None, "")]
    if not ids:
        value = observed.get("provider_model_id", observed.get("actual_provider_model_id"))
        ids = [value] if value not in (None, "") else []
    if not ids:
        return [], False
    provider = observed.get("provider")
    endpoint = observed.get("endpoint_identity")
    declared_model = observed.get("model") or ""
    source = observed.get("identity_source", observed.get("source", "unavailable"))
    return [
        {
            "call_index": index,
            "provider": provider,
            "endpoint_identity": endpoint,
            "declared_model": declared_model,
            "observed_provider_model_id": value,
            "identity_source": source,
        }
        for index, value in enumerate(ids, start=1)
    ], False


def generic_aliases(declared_model_identity: Mapping[str, Any] | None) -> set[str]:
    aliases = set(GENERIC_MODEL_ALIASES)
    if not isinstance(declared_model_identity, Mapping):
        return aliases
    explicit = declared_model_identity.get("generic_model_alias")
    if explicit not in (None, ""):
        aliases.add(str(explicit).casefold())
    for key in ("configured_model_id", "model"):
        value = declared_model_identity.get(key)
        if value not in (None, "") and str(value).casefold() in GENERIC_MODEL_ALIASES:
            aliases.add(str(value).casefold())
    return aliases


@dataclass
class IdentityRecord:
    calls: list[dict[str, Any]]
    observed_ids: list[str]
    providers: list[str]
    endpoints: list[str]
    run_ids: list[str]
    external: Any
    complete: bool
    consistent: bool
    sufficient: bool
    unavailable: bool
    projected: dict[str, Any]


@dataclass
class IdentityCollection:
    ordered_calls: list[dict[str, Any]] = field(default_factory=list)
    observed_ids: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    external_identities: list[str] = field(default_factory=list)
    identities: list[dict[str, Any]] = field(default_factory=list)
    run_id_sequences: list[list[str]] = field(default_factory=list)
    run_call_sequences: list[list[dict[str, Any]]] = field(default_factory=list)
    eligible_count: int = 0
    unavailable_count: int = 0
    complete: bool = True
    run_consistent: bool = True
    run_sufficient: bool = True

    def merge(self, item: IdentityRecord, aliases: set[str]) -> None:
        self.ordered_calls.extend(item.calls)
        self.observed_ids.extend(item.observed_ids)
        self.providers.extend(item.providers)
        self.endpoints.extend(item.endpoints)
        self.run_id_sequences.append(item.run_ids)
        self.run_call_sequences.append(item.calls)
        self.complete = self.complete and item.complete
        self.run_consistent = self.run_consistent and item.consistent
        self.run_sufficient = self.run_sufficient and item.sufficient
        if item.external not in (None, "") and str(item.external).casefold() not in aliases:
            self.external_identities.append(str(item.external).strip()[:256])
        if item.unavailable:
            self.unavailable_count += 1
        if item.projected not in self.identities and any(
            value not in (None, "") for value in item.projected.values()
        ):
            self.identities.append(item.projected)


def _valid_call_mappings(calls: list[Any]) -> tuple[list[Mapping[str, Any]], bool]:
    valid = [call for call in calls if isinstance(call, Mapping)]
    complete = len(valid) == len(calls) and all(
        all(field in call for field in CALL_IDENTITY_FIELDS) for call in valid
    )
    return valid, complete


def _project_calls(
    calls: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str], list[str]]:
    projected = [{field: call.get(field) for field in CALL_IDENTITY_FIELDS} for call in calls]
    observed_ids: list[str] = []
    providers: list[str] = []
    endpoints: list[str] = []
    for call in calls:
        value = call.get("observed_provider_model_id")
        if value not in (None, ""):
            observed_ids.append(str(value))
        provider = call.get("provider")
        if provider not in (None, ""):
            providers.append(str(provider))
        endpoint = call.get("endpoint_identity")
        if endpoint not in (None, ""):
            endpoints.append(str(endpoint))
    return projected, observed_ids, providers, endpoints, list(observed_ids)


def _record_projection(observed: Mapping[str, Any], run_ids: list[str]) -> dict[str, Any]:
    single_run = len(run_ids) <= 1
    return {
        "available": bool(observed.get("available")),
        "provider_model_id": (
            observed.get("provider_model_id", observed.get("actual_provider_model_id"))
            if single_run else None
        ),
        "actual_provider_model_id": (
            observed.get("actual_provider_model_id", observed.get("provider_model_id"))
            if single_run else None
        ),
        "provider": observed.get("provider"),
        "model": observed.get("model"),
        "endpoint_identity": observed.get("endpoint_identity"),
        "source": observed.get("source", observed.get("identity_source")),
        "external_identity": observed.get("external_identity"),
    }


def _record_identity(record: Mapping[str, Any]) -> IdentityRecord:
    evidence = record.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    observed = evidence.get("observed_model_identity")
    observed = observed if isinstance(observed, Mapping) else {}
    calls, lossless = call_identity_values(record)
    valid_calls, calls_complete = _valid_call_mappings(calls)
    projected_calls, ids, providers, endpoints, run_ids = _project_calls(valid_calls)
    for value, target in (
        (observed.get("provider"), providers),
        (observed.get("endpoint_identity"), endpoints),
    ):
        if value not in (None, ""):
            target.append(str(value))
    external = observed.get("external_identity")
    provider_observation_complete = bool(valid_calls) and all(
        call.get("observed_provider_model_id") not in (None, "")
        for call in valid_calls
    )
    complete = bool(
        lossless
        and bool(calls)
        and calls_complete
        and (provider_observation_complete or external not in (None, ""))
    )
    expected_ids = [
        str(value)
        for value in ordered_values(observed.get("observed_model_ids"))
        if value not in (None, "")
    ]
    if expected_ids and expected_ids != run_ids:
        complete = False
    if observed.get("complete") is False:
        complete = False
    consistent = observed.get("consistent") is not False
    sufficient = observed.get("identity_sufficient") is True
    unavailable = not ids and not external
    return IdentityRecord(
        calls=projected_calls,
        observed_ids=ids,
        providers=providers,
        endpoints=endpoints,
        run_ids=run_ids,
        external=external,
        complete=complete,
        consistent=consistent,
        sufficient=sufficient,
        unavailable=unavailable,
        projected=_record_projection(observed, expected_ids),
    )


def collect_identity_state(
    records: Sequence[Mapping[str, Any]], aliases: set[str]
) -> IdentityCollection:
    collection = IdentityCollection()
    for record in records:
        if not isinstance(record, Mapping) or not identity_eligible(record):
            continue
        collection.eligible_count += 1
        collection.merge(_record_identity(record), aliases)
    return collection


__all__ = [
    "CALL_IDENTITY_FIELDS",
    "IdentityCollection",
    "collect_identity_state",
    "generic_aliases",
]
