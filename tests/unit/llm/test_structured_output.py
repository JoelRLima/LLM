import pytest

from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode
from agent.llm.structured_output import (
    StructuredOutputError,
    StructuredOutputStrategy,
    parse_structured_response,
)


def test_strategy_prefers_native_schema_then_grammar_then_prompt():
    schema = {"type": "object"}
    native = StructuredOutputStrategy(
        ProviderCapabilities(
            structured_output_modes=(
                StructuredOutputMode.JSON_SCHEMA,
                StructuredOutputMode.GBNF,
                StructuredOutputMode.JSON_PROMPT,
            )
        )
    ).select(schema=schema, grammar="root ::= object")
    grammar = StructuredOutputStrategy(
        ProviderCapabilities(
            structured_output_modes=(
                StructuredOutputMode.GBNF,
                StructuredOutputMode.JSON_PROMPT,
            )
        )
    ).select(schema=schema, grammar="root ::= object")
    prompt = StructuredOutputStrategy(ProviderCapabilities()).select(schema=schema)

    assert native.mode == StructuredOutputMode.JSON_SCHEMA
    assert grammar.mode == StructuredOutputMode.GBNF
    assert prompt.mode == StructuredOutputMode.JSON_PROMPT


def test_parser_accepts_fenced_complete_json_and_validates_schema():
    schema = {
        "type": "object",
        "required": ["action"],
        "properties": {"action": {"type": "string"}},
        "additionalProperties": False,
    }

    assert parse_structured_response('```json\n{"action":"final"}\n```', schema) == {
        "action": "final"
    }


def test_parser_rejects_truncated_or_schema_invalid_json():
    with pytest.raises(StructuredOutputError):
        parse_structured_response('{"action":')
    with pytest.raises(StructuredOutputError, match="campo obrigatório"):
        parse_structured_response("{}", {"type": "object", "required": ["action"]})
