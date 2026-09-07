"""Bounded W14 intent continuation projection for checkpoint resume."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from agent.interaction.intent_claim import (
    IntentClaimError,
    IntentClaimV1,
    bind_current_subject_evidence,
    parse_intent_claim,
)

W14_CONTINUATION_SCHEMA_VERSION = 1
_CONTINUATION_FIELDS = frozenset(
    {
        "schema_version",
        "subject",
        "claim",
        "claim_fingerprint",
        "authority_identity",
        "proposal_only",
        "requires_validation",
        "integrity",
    }
)
_MAX_SUBJECT_LENGTH = 8192
_MAX_AUTHORITY_IDENTITY_LENGTH = 512


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def claim_fingerprint(claim: IntentClaimV1) -> str:
    return hashlib.sha256(_canonical_json(claim.to_dict()).encode("utf-8")).hexdigest()


def _integrity_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in _CONTINUATION_FIELDS if key != "integrity"}


def _integrity(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(_integrity_payload(value)).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class W14IntentContinuation:
    """Intent-only checkpoint data; it contains no reusable authority grant."""

    subject: str
    claim: Mapping[str, Any]
    claim_fingerprint: str
    authority_identity: str
    proposal_only: bool
    requires_validation: bool
    integrity: str
    schema_version: int = W14_CONTINUATION_SCHEMA_VERSION

    @classmethod
    def from_claim(
        cls,
        claim: IntentClaimV1,
        *,
        subject: str,
        admitted_intent: Any,
    ) -> "W14IntentContinuation":
        if not isinstance(claim, IntentClaimV1):
            raise ValueError("W14 continuation claim is invalid")
        if type(subject) is not str or not subject.strip() or len(subject) > _MAX_SUBJECT_LENGTH:
            raise ValueError("W14 continuation subject is invalid")
        try:
            bind_current_subject_evidence(claim, subject)
        except IntentClaimError as exc:
            raise ValueError("W14 continuation evidence is not bound to subject") from exc
        fingerprint = claim_fingerprint(claim)
        if fingerprint != str(getattr(admitted_intent, "claim_fingerprint", "")):
            raise ValueError("W14 continuation claim fingerprint is inconsistent")
        authority_identity = str(getattr(admitted_intent, "authority_identity", ""))
        if not authority_identity or len(authority_identity) > _MAX_AUTHORITY_IDENTITY_LENGTH:
            raise ValueError("W14 continuation authority identity is invalid")
        claim_dict = claim.to_dict()
        proposal_only = getattr(admitted_intent, "proposal_only", None)
        requires_validation = getattr(admitted_intent, "requires_validation", None)
        raw = {
            "schema_version": W14_CONTINUATION_SCHEMA_VERSION,
            "subject": subject,
            "claim": claim_dict,
            "claim_fingerprint": fingerprint,
            "authority_identity": authority_identity,
            "proposal_only": proposal_only,
            "requires_validation": requires_validation,
        }
        if type(raw["proposal_only"]) is not bool or type(raw["requires_validation"]) is not bool:
            raise ValueError("W14 continuation constraint flags are invalid")
        proposal_only_value: bool = bool(raw["proposal_only"])
        requires_validation_value: bool = bool(raw["requires_validation"])
        raw["integrity"] = _integrity(raw)
        return cls(
            subject=subject,
            claim=claim_dict,
            claim_fingerprint=fingerprint,
            authority_identity=authority_identity,
            proposal_only=proposal_only_value,
            requires_validation=requires_validation_value,
            integrity=str(raw["integrity"]),
        )

    @classmethod
    def from_checkpoint_dict(cls, raw: Any) -> "W14IntentContinuation":
        if not isinstance(raw, Mapping) or set(raw) != _CONTINUATION_FIELDS:
            raise ValueError("W14 continuation fields are not exact")
        if raw.get("schema_version") != W14_CONTINUATION_SCHEMA_VERSION:
            raise ValueError("W14 continuation schema version is unsupported")
        subject = raw.get("subject")
        if type(subject) is not str or not subject.strip() or len(subject) > _MAX_SUBJECT_LENGTH:
            raise ValueError("W14 continuation subject is invalid")
        claim_raw = raw.get("claim")
        if not isinstance(claim_raw, (str, Mapping)):
            raise ValueError("W14 continuation claim is invalid")
        try:
            claim = parse_intent_claim(claim_raw, subject=subject)
        except (IntentClaimError, TypeError, ValueError) as exc:
            raise ValueError("W14 continuation claim is invalid") from exc
        fingerprint_value = raw.get("claim_fingerprint")
        if type(fingerprint_value) is not str or fingerprint_value != claim_fingerprint(claim):
            raise ValueError("W14 continuation claim fingerprint is invalid")
        fingerprint = str(fingerprint_value)
        authority_identity = raw.get("authority_identity")
        if (
            type(authority_identity) is not str
            or not authority_identity.strip()
            or len(authority_identity) > _MAX_AUTHORITY_IDENTITY_LENGTH
        ):
            raise ValueError("W14 continuation authority identity is invalid")
        if type(raw.get("proposal_only")) is not bool or type(raw.get("requires_validation")) is not bool:
            raise ValueError("W14 continuation constraint flags are invalid")
        proposal_only_value: bool = bool(raw["proposal_only"])
        requires_validation_value: bool = bool(raw["requires_validation"])
        integrity_value = raw.get("integrity")
        if type(integrity_value) is not str or integrity_value != _integrity(raw):
            raise ValueError("W14 continuation integrity is invalid")
        return cls(
            subject=subject,
            claim=claim.to_dict(),
            claim_fingerprint=fingerprint,
            authority_identity=authority_identity,
            proposal_only=proposal_only_value,
            requires_validation=requires_validation_value,
            integrity=str(integrity_value),
        )

    def claim_object(self) -> IntentClaimV1:
        try:
            return parse_intent_claim(self.claim, subject=self.subject)
        except (IntentClaimError, TypeError, ValueError) as exc:
            raise ValueError("W14 continuation claim is invalid") from exc

    def to_checkpoint_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "subject": self.subject,
            "claim": json.loads(json.dumps(self.claim, ensure_ascii=False)),
            "claim_fingerprint": self.claim_fingerprint,
            "authority_identity": self.authority_identity,
            "proposal_only": self.proposal_only,
            "requires_validation": self.requires_validation,
        }
        value["integrity"] = _integrity(value)
        if value["integrity"] != self.integrity:
            raise ValueError("W14 continuation integrity changed")
        return value


__all__ = [
    "W14_CONTINUATION_SCHEMA_VERSION",
    "W14IntentContinuation",
    "claim_fingerprint",
]
