import json

import pytest

from agent.capabilities import Capability
from agent.interaction.intent_claim import parse_intent_claim
from agent.planning.intent_admission import (
    AuthorityEnvelope,
    IntentAdmissionError,
    admit_intent_claim,
)
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance


def _claim(*, operation="do", selector_kind="symbol", selector_value="TIMEOUT", effect="write"):
    subject = "change TIMEOUT"
    start = subject.index("TIMEOUT")
    evidence = {
        "span_id": "e1",
        "start": start,
        "end": start + len("TIMEOUT"),
        "text": "TIMEOUT",
    }
    return parse_intent_claim(
        json.dumps(
            {
                "schema_version": "intent-claim-v1",
                "operation": operation,
                "ambiguity": "none",
                "effects": [
                    {
                        "effect": effect,
                        "polarity": "requested",
                        "selector_ids": ["s1"],
                        "evidence_span_ids": ["e1"],
                    }
                ],
                "selectors": [
                    {
                        "selector_id": "s1",
                        "kind": selector_kind,
                        "value": selector_value,
                        "role": "mutation_target",
                        "evidence_span_ids": ["e1"],
                    }
                ],
                "constraints": [],
                "evidence_spans": [evidence],
            }
        ),
        subject=subject,
    )


def _envelope(*, permissions=("read", "write", "validate"), write_scope=("src",)):
    return AuthorityEnvelope(
        parent_permissions=frozenset(permissions),
        granted_effects=frozenset({"write"}),
        read_resources=(
            ResourceAccess("src", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write_scope
        ),
    )


def _memory_claim():
    subject = "remember this in memory"
    value = "memory"
    start = subject.index(value)
    return parse_intent_claim(
        json.dumps(
            {
                "schema_version": "intent-claim-v1",
                "operation": "do",
                "ambiguity": "none",
                "effects": [
                    {
                        "effect": "memory_write",
                        "polarity": "requested",
                        "selector_ids": ["s1"],
                        "evidence_span_ids": ["e1"],
                    }
                ],
                "selectors": [
                    {
                        "selector_id": "s1",
                        "kind": "resource",
                        "value": value,
                        "role": "memory",
                        "evidence_span_ids": ["e1"],
                    }
                ],
                "constraints": [],
                "evidence_spans": [
                    {"span_id": "e1", "start": start, "end": start + len(value), "text": value}
                ],
            }
        ),
        subject=subject,
    )


def _memory_envelope(*, permissions=("read", "memory"), write_scope=()):
    return AuthorityEnvelope(
        parent_permissions=frozenset(permissions),
        granted_effects=frozenset({"memory_write"}),
        read_resources=(
            ResourceAccess("memory", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write_scope
        ),
    )


def test_symbolic_claim_is_admitted_without_becoming_a_path() -> None:
    admitted = admit_intent_claim(_claim(), _envelope(), current_subject="change TIMEOUT")

    assert admitted.requires_grounding is True
    assert admitted.grounded_targets == ()
    assert admitted.can_mutate is False
    assert admitted.admitted_effects == ("write",)


def test_missing_parent_write_denies_claim() -> None:
    with pytest.raises(IntentAdmissionError, match="INTENT_CAPABILITY_DENIED"):
        admit_intent_claim(
            _claim(),
            _envelope(permissions=("read",), write_scope=()),
            current_subject="change TIMEOUT",
        )


def test_granted_effect_surface_is_independent_from_capability() -> None:
    envelope = _envelope()
    envelope_without_effect = AuthorityEnvelope(
        parent_permissions=envelope.parent_permissions,
        granted_effects=frozenset(),
        read_resources=envelope.read_resources,
        write_resources=envelope.write_resources,
    )
    with pytest.raises(IntentAdmissionError, match="INTENT_EFFECT_NOT_GRANTED"):
        admit_intent_claim(
            _claim(),
            envelope_without_effect,
            current_subject="change TIMEOUT",
        )


def test_unknown_effect_denies_without_lexical_fallback() -> None:
    with pytest.raises(IntentAdmissionError, match="INTENT_UNKNOWN_EFFECT"):
        admit_intent_claim(
            _claim(effect="process"),
            _envelope(),
            current_subject="change TIMEOUT",
        )


def test_literal_write_requires_exact_evidence_and_both_scopes() -> None:
    claim = _claim(selector_kind="path_literal", selector_value="src/file.py")
    with pytest.raises(IntentAdmissionError, match="INTENT_LITERAL_TARGET_NOT_EVIDENCED"):
        admit_intent_claim(claim, _envelope(), current_subject="change TIMEOUT")


def test_approval_flag_does_not_add_capability_or_target() -> None:
    envelope = AuthorityEnvelope(
        parent_permissions=frozenset({Capability.READ.value, Capability.WRITE.value}),
        granted_effects=frozenset({"write"}),
        read_resources=(ResourceAccess("src", "read", "trusted_derived"),),
        write_resources=(ResourceAccess("src", "write", "trusted_derived"),),
        approval_required=True,
    )
    admitted = admit_intent_claim(_claim(), envelope, current_subject="change TIMEOUT")
    assert admitted.approval_required is True
    assert admitted.capabilities == set()
    assert admitted.grounded_targets == ()


def test_memory_write_requires_explicit_memory_write_scope() -> None:
    admitted = admit_intent_claim(
        _memory_claim(),
        _memory_envelope(write_scope=("memory",)),
        current_subject="remember this in memory",
    )
    assert admitted.admitted_effects == ("memory_write",)
    assert admitted.mutation_targets == ("memory",)

    with pytest.raises(IntentAdmissionError, match="INTENT_MEMORY_WRITE_SCOPE_DENIED"):
        admit_intent_claim(
            _memory_claim(),
            _memory_envelope(),
            current_subject="remember this in memory",
        )


def test_memory_write_needs_memory_capability_even_with_resource_scope() -> None:
    with pytest.raises(IntentAdmissionError, match="INTENT_CAPABILITY_DENIED"):
        admit_intent_claim(
            _memory_claim(),
            _memory_envelope(permissions=("read",), write_scope=("memory",)),
            current_subject="remember this in memory",
        )


def test_memory_write_read_only_memory_authority_is_denied() -> None:
    with pytest.raises(IntentAdmissionError, match="INTENT_MEMORY_WRITE_SCOPE_DENIED"):
        admit_intent_claim(
            _memory_claim(),
            _memory_envelope(permissions=("read", "memory")),
            current_subject="remember this in memory",
        )


def test_memory_write_requires_a_separate_granted_effect() -> None:
    envelope = AuthorityEnvelope(
        parent_permissions=frozenset({"read", "memory"}),
        granted_effects=frozenset(),
        read_resources=(
            ResourceAccess("memory", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        ),
        write_resources=(
            ResourceAccess("memory", ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED),
        ),
    )
    with pytest.raises(IntentAdmissionError, match="INTENT_EFFECT_NOT_GRANTED"):
        admit_intent_claim(
            _memory_claim(),
            envelope,
            current_subject="remember this in memory",
        )


def test_model_memory_claim_does_not_expand_resource_authority() -> None:
    with pytest.raises(IntentAdmissionError, match="INTENT_MEMORY_WRITE_SCOPE_DENIED"):
        admit_intent_claim(
            _memory_claim(),
            _memory_envelope(permissions=("read", "memory")),
            current_subject="remember this in memory",
        )
