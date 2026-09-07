"""Deterministic semantic-intent responses for the W14 practical fixtures."""

from __future__ import annotations

import json
import re


def scripted_semantic_response(marker: str, subject: str) -> str:
    """Return deterministic W14 claims for the controlled local fixtures."""

    if marker == "PV1-03-clarify":
        evidence_text = "não forneci preferência"
        start = subject.find(evidence_text)
        if start < 0:
            evidence_text = "nao forneci preferencia"
            start = subject.casefold().find(evidence_text.casefold())
        if start < 0:
            evidence_text = subject
            start = 0
        claim = {
            "schema_version": "intent-claim-v1",
            "operation": "do",
            "ambiguity": "effect",
            "effects": [],
            "selectors": [],
            "constraints": [],
            "evidence_spans": [{
                "span_id": "e1",
                "start": start,
                "end": start + len(evidence_text),
                "text": evidence_text,
            }],
        }
        outer = {
            "action": "clarify",
            "directive": "none",
            "ambiguity": "effect",
            "grounding": "current_turn",
            "operation_requested": True,
            "proposal_only": False,
            "resume_requested": False,
            "evidence": evidence_text,
            "intent_claim": claim,
        }
        return json.dumps(outer, ensure_ascii=False)

    if marker == "PV1-05":
        selector_value = "feature.py"
        start = subject.find(selector_value)
        if start < 0:
            start = subject.casefold().find(selector_value.casefold())
        if start < 0:
            start = 0
        evidence = {
            "span_id": "e1",
            "start": start,
            "end": start + len(selector_value),
            "text": selector_value,
        }
        claim = {
            "schema_version": "intent-claim-v1",
            "operation": "read",
            "ambiguity": "none",
            "effects": [],
            "selectors": [{
                "selector_id": "s1",
                "kind": "path_literal",
                "value": selector_value,
                "role": "source",
                "evidence_span_ids": ["e1"],
            }],
            "constraints": [],
            "evidence_spans": [evidence],
        }
        return json.dumps({
            "action": "run",
            "directive": "read",
            "ambiguity": "none",
            "grounding": "current_turn",
            "operation_requested": False,
            "proposal_only": False,
            "resume_requested": False,
            "evidence": selector_value,
            "intent_claim": claim,
        }, ensure_ascii=False)

    paths = {
        "PV1-01": "calculator.py",
        "PV1-02": "parser.py",
        "PV1-03": "config.py",
        "PV1-06": "src/module.py",
        "PV1-07": "app.py",
        "PV1-08": "src/math_ops.py",
    }
    generic_selector_value: str | None = "DEFAULT_TIMEOUT" if marker == "PV1-04" else paths.get(marker)
    if generic_selector_value is None:
        match = re.search(r"`([^`]+)`", subject)
        generic_selector_value = match.group(1) if match is not None else "workspace"
    selector_kind = "symbol" if marker == "PV1-04" else "path_literal"
    start = subject.find(generic_selector_value)
    if start < 0:
        start = subject.casefold().find(generic_selector_value.casefold())
    if start < 0:
        generic_selector_value = subject
        start = 0
    evidence = {
        "span_id": "e1",
        "start": start,
        "end": start + len(generic_selector_value),
        "text": generic_selector_value,
    }
    claim = {
        "schema_version": "intent-claim-v1",
        "operation": "do",
        "ambiguity": "none",
        "effects": [{
            "effect": "write",
            "polarity": "requested",
            "selector_ids": ["s1"],
            "evidence_span_ids": ["e1"],
        }],
        "selectors": [{
            "selector_id": "s1",
            "kind": selector_kind,
            "value": generic_selector_value,
            "role": "mutation_target",
            "evidence_span_ids": ["e1"],
        }],
        "constraints": [],
        "evidence_spans": [evidence],
    }
    return json.dumps({
        "action": "run",
        "directive": "do",
        "ambiguity": "none",
        "grounding": "current_turn",
        "operation_requested": True,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": generic_selector_value,
        "intent_claim": claim,
    }, ensure_ascii=False)
