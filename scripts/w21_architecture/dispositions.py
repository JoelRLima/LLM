"""Closed-world dispositions for frozen W21 architecture authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DISPOSITION_STATES = frozenset({"NAMESPACE_PROJECTED", "W22_SUPERSEDED", "RETIRED_WITH_PROOF"})


def expected_authorities(
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any],
) -> set[tuple[str, str]]:
    """Return the complete identity set covered by the W21 authority documents."""

    return set().union(
        _authority_keys(policy.get("rules", []), "rule", "rule_id"),
        _authority_keys(policy.get("approved_neutral_owners", []), "neutral_owner", "package"),
        _authority_keys(policy.get("explicit_canonical_directions", []), "canonical_direction", "direction_id"),
        _authority_keys(compatibility.get("bridges", []), "compatibility_bridge", "bridge_id"),
        _authority_keys(compatibility.get("adapter_edges", []), "adapter", "adapter_id"),
    )


def _authority_keys(records: Iterable[Any], kind: str, field: str) -> set[tuple[str, str]]:
    return {
        (kind, str(record[field]))
        for record in records
        if isinstance(record, Mapping) and isinstance(record.get(field), str)
    }


def _status_errors(document: Mapping[str, Any], *, require_closed: bool) -> list[str]:
    errors: list[str] = []
    if document.get("status") not in {"PROVISIONAL", "CLOSED"}:
        errors.append("W21 disposition document status must be PROVISIONAL or CLOSED")
    if require_closed and document.get("status") != "CLOSED":
        errors.append("W21 dispositions are not marked CLOSED")
    return errors


def _w22_rule_ids(w22_policy: Mapping[str, Any]) -> set[str]:
    return {
        str(record["rule_id"])
        for record in w22_policy.get("forbidden_owner_edges", [])
        if isinstance(record, Mapping) and isinstance(record.get("rule_id"), str)
    }


def _record_identity(
    index: int,
    record: Any,
    seen: set[tuple[str, str]],
) -> tuple[tuple[str, str] | None, list[str]]:
    label = f"dispositions[{index}]"
    if not isinstance(record, Mapping):
        return None, [f"{label} must be an object"]
    kind, authority_id = record.get("authority_type"), record.get("authority_id")
    if not isinstance(kind, str) or not isinstance(authority_id, str):
        return None, [f"{label} requires string authority_type and authority_id"]
    key = (kind, authority_id)
    errors = [f"duplicate W21 disposition: {kind}:{authority_id}"] if key in seen else []
    seen.add(key)
    return key, errors


def _evidence_metadata_errors(label: str, record: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
    errors: list[str] = []
    if not isinstance(record.get("rationale"), str) or not record["rationale"].strip():
        errors.append(f"{label} requires a non-empty rationale")
    evidence = record.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
        errors.append(f"{label} evidence must be a list of non-empty strings")
        evidence = []
    return errors, evidence


def _valid_replacements(record: Mapping[str, Any], w22_rules: set[str]) -> bool:
    replacements = record.get("replacement_rule_ids", [])
    return isinstance(replacements, list) and bool(replacements) and all(item in w22_rules for item in replacements)


def _evidence_state_errors(
    label: str,
    record: Mapping[str, Any],
    disposition: str,
    evidence: list[Any],
    w22_rules: set[str],
) -> list[str]:
    errors: list[str] = []
    if disposition == "NAMESPACE_PROJECTED" and not evidence:
        errors.append(f"{label} namespace projection requires provenance evidence")
    if disposition == "W22_SUPERSEDED":
        if not _valid_replacements(record, w22_rules):
            errors.append(f"{label} supersession must name existing W22 owner rule IDs")
    if disposition == "RETIRED_WITH_PROOF" and not evidence:
        errors.append(f"{label} retirement requires proof evidence")
    return errors


def _record_evidence_errors(
    label: str,
    record: Mapping[str, Any],
    disposition: str,
    w22_rules: set[str],
) -> list[str]:
    errors, evidence = _evidence_metadata_errors(label, record)
    errors.extend(_evidence_state_errors(label, record, disposition, evidence, w22_rules))
    return errors


def _record_errors(
    index: int,
    record: Any,
    w22_rules: set[str],
    seen: set[tuple[str, str]],
) -> list[str]:
    key, errors = _record_identity(index, record, seen)
    if key is None or not isinstance(record, Mapping):
        return errors
    disposition = record.get("disposition")
    if disposition not in DISPOSITION_STATES:
        errors.append(f"dispositions[{index}] has unsupported disposition: {disposition!r}")
        return errors
    errors.extend(_record_evidence_errors(f"dispositions[{index}]", record, str(disposition), w22_rules))
    return errors


def _coverage_errors(
    expected: set[tuple[str, str]], seen: set[tuple[str, str]]
) -> list[str]:
    missing = sorted(expected - seen)
    unexpected = sorted(seen - expected)
    return [
        *(f"missing W21 disposition: {kind}:{authority_id}" for kind, authority_id in missing),
        *(f"unknown W21 disposition authority: {kind}:{authority_id}" for kind, authority_id in unexpected),
    ]


def validate_dispositions(
    document: Mapping[str, Any],
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    w22_policy: Mapping[str, Any],
    *,
    require_closed: bool = False,
) -> list[str]:
    """Validate exact, unique coverage and proof required by each disposition."""

    errors = _status_errors(document, require_closed=require_closed)

    raw = document.get("dispositions")
    if not isinstance(raw, list):
        return errors + ["W21 disposition document must contain a dispositions list"]

    expected = expected_authorities(policy, compatibility)
    w22_rules = _w22_rule_ids(w22_policy)
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(raw):
        errors.extend(_record_errors(index, record, w22_rules, seen))
    errors.extend(_coverage_errors(expected, seen))
    return sorted(set(errors))
