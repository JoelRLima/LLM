import json
from dataclasses import replace

import pytest

from llm_agent.agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from llm_agent.agent.llm.errors import ModelProviderError
from llm_agent.agent.llm.structured_output import is_grammar_unsupported_error, resolve_model_decision
from tests.unit.llm.test_canonical_decision import _GrammarUnsupported, _resolve, _session


@pytest.mark.parametrize("message", [
    "grammar is unsupported", "grammar is not supported by this server", "GBNF is disabled",
    "unknown field 'grammar'", "unsupported parameter: grammar", "unexpected keyword argument 'grammar'",
    "Unrecognized request argument supplied: grammar", "this server does not support grammar",
])
def test_explicit_capability_rejections_are_recognized(message):
    assert is_grammar_unsupported_error(_GrammarUnsupported(message))
    assert is_grammar_unsupported_error(_GrammarUnsupported(json.dumps({"error": {"message": message}})))


@pytest.mark.parametrize("message", [
    "invalid grammar syntax: missing root rule", "unknown grammar rule: item",
    "unsupported grammar construct: repetition", "grammar parse error", "grammar compilation failed",
    "context window exceeded while using grammar", "invalid schema for grammar response",
    "unsupported max_tokens; request includes grammar", "grammar root is invalid",
])
def test_grammar_syntax_and_unrelated_failures_are_not_capability_rejections(message):
    assert not is_grammar_unsupported_error(_GrammarUnsupported(message))


def test_echoed_request_cannot_disable_grammar():
    body = {"error": {"message": "invalid max_tokens"}, "request": {"grammar": "grammar is unsupported"}}
    assert not is_grammar_unsupported_error(_GrammarUnsupported(json.dumps(body)))


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_other_http_failures_never_degrade_grammar(status):
    error = _GrammarUnsupported()
    error.response.status_code = status
    assert not is_grammar_unsupported_error(error)


def test_non_string_provider_body_is_not_a_capability_signal():
    error = _GrammarUnsupported()
    error.response.text = {"error": "grammar is unsupported"}
    assert not is_grammar_unsupported_error(error)


def test_invalid_grammar_stops_after_one_accounted_attempt_and_preserves_capability():
    session, gateway, metrics = _session([_GrammarUnsupported("invalid grammar syntax: missing root rule")])
    with pytest.raises(ModelProviderError):
        _resolve(session)
    assert len(gateway.requests) == len(metrics) == session.budget_ledger.snapshot().model_calls == 1
    assert session._grammar_supports_grammar is None


@pytest.mark.parametrize("mode", [None, StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.GBNF])
@pytest.mark.parametrize("failure", [False, True])
def test_grammar_cache_describes_only_requests_that_actually_sent_gbnf(mode, failure):
    structured = StructuredOutputRequest(mode=mode, schema={"type": "object"}) if mode else None
    request = ModelRequest(
        messages=(ModelMessage("user", "answer"),), model="test", temperature=0,
        max_output_tokens=20, structured_output=structured,
    )
    cache = []
    calls = []

    def complete(current):
        calls.append(current)
        if failure:
            raise _GrammarUnsupported()
        return ModelResponse(content='{"answer":"ok"}')

    def run():
        return resolve_model_decision(
            request, complete=complete, retry_request=lambda: request,
            grammar='root ::= "ok"', grammar_supported=None, set_grammar_supported=cache.append,
            fallback_request=lambda current: replace(current, structured_output=None),
        )

    if failure:
        with pytest.raises(ModelProviderError):
            run()
    else:
        assert run() == {"answer": "ok"}
    assert cache == []
    assert calls == [request]
