"""Schema and grammar transport descriptions for intent claims."""

from __future__ import annotations

from typing import Any

from .intent_claim_validation import (
    _AMBIGUITIES,
    _CONSTRAINT_KINDS,
    _OPERATIONS,
    _POLARITIES,
    _SELECTOR_KINDS,
    _SELECTOR_ROLES,
    INTENT_CLAIM_SCHEMA_VERSION,
    MAX_CONSTRAINT_VALUE_LENGTH,
    MAX_CONSTRAINTS,
    MAX_EFFECT_LENGTH,
    MAX_EFFECTS,
    MAX_EVIDENCE_SPANS,
    MAX_ID_LENGTH,
    MAX_SELECTORS,
    MAX_VALUE_LENGTH,
)

INTENT_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "operation",
        "ambiguity",
        "effects",
        "selectors",
        "constraints",
        "evidence_spans",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": INTENT_CLAIM_SCHEMA_VERSION},
        "operation": {"type": "string", "enum": sorted(_OPERATIONS)},
        "ambiguity": {"type": "string", "enum": sorted(_AMBIGUITIES)},
        "effects": {
            "type": "array",
            "maxItems": MAX_EFFECTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["effect", "polarity", "selector_ids", "evidence_span_ids"],
                "properties": {
                    "effect": {"type": "string", "minLength": 1, "maxLength": MAX_EFFECT_LENGTH},
                    "polarity": {"type": "string", "enum": sorted(_POLARITIES)},
                    "selector_ids": {"type": "array", "maxItems": MAX_SELECTORS, "items": {"type": "string"}},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "selectors": {
            "type": "array",
            "maxItems": MAX_SELECTORS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["selector_id", "kind", "value", "role", "evidence_span_ids"],
                "properties": {
                    "selector_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH},
                    "kind": {"type": "string", "enum": sorted(_SELECTOR_KINDS)},
                    "value": {"type": "string", "minLength": 1, "maxLength": MAX_VALUE_LENGTH},
                    "role": {"type": "string", "enum": sorted(_SELECTOR_ROLES)},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "constraints": {
            "type": "array",
            "maxItems": MAX_CONSTRAINTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "evidence_span_ids"],
                "properties": {
                    "kind": {"type": "string", "enum": sorted(_CONSTRAINT_KINDS)},
                    "value": {"type": "string", "maxLength": MAX_CONSTRAINT_VALUE_LENGTH},
                    "selector_ids": {"type": "array", "maxItems": MAX_SELECTORS, "items": {"type": "string"}},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "evidence_spans": {
            "type": "array",
            "maxItems": MAX_EVIDENCE_SPANS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["span_id", "start", "end", "text"],
                "properties": {
                    "span_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                    "text": {"type": "string", "minLength": 1, "maxLength": MAX_VALUE_LENGTH},
                },
            },
        },
    },
}


# The grammar is intentionally a structural transport fallback.  The parser
# above remains authoritative in every mode and rejects all unexpected keys.
INTENT_CLAIM_GBNF = r'''root ::= ws object ws
object ::= "{" ws (members)? ws "}"
members ::= string ws ":" ws value (ws "," ws string ws ":" ws value)*
value ::= string | number | array | object | "true" | "false" | "null"
array ::= "[" ws (value (ws "," ws value)*)? ws "]"
number ::= "0" | [1-9][0-9]*
string ::= "\"" chars "\""
chars ::= char*
char ::= [^"\\\x00-\x1F] | escape
escape ::= "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*'''
