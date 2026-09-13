from __future__ import annotations

import json
from pathlib import Path

import agent.evaluation.model_compatibility_canaries as canaries
from agent.evaluation.model_compatibility_canaries import (
    CANARY_CASE_IDS,
    CANARY_GBNF_GRAMMAR,
    CANARY_SCHEMA,
    CANARY_SCHEMA_VERSION,
    DeterministicCanaryGateway,
    _request,
    run_model_canaries,
)
from agent.llm.contracts import ModelResponse, StructuredOutputMode
from agent.llm.model_profile import resolve_model_profile
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway


def _profile():
    document = json.loads(
        (Path("agent/resources/default_config.json")).read_text(encoding="utf-8")
    )
    return resolve_model_profile(document, profile_name="local_8gb")


def test_deterministic_canaries_are_generic_and_zero_live() -> None:
    profile = _profile()
    gateway = DeterministicCanaryGateway(profile)

    report = run_model_canaries(
        profile,
        gateway,
        live_model_used=False,
    )

    assert report["schema_version"] == CANARY_SCHEMA_VERSION
    assert report["live_model_used"] is False
    assert [case["case_id"] for case in report["cases"]] == list(CANARY_CASE_IDS)
    assert report["summary"] == {
        "total": 8,
        "passed": 8,
        "failed": 0,
        "not_applicable": 0,
        "blocked": 0,
    }
    assert all(case["status"] == "passed" for case in report["cases"])
    assert "release_verdict" not in report
    assert "score_threshold" not in report
    assert not hasattr(gateway, "requests")
    assert report["observed_model_identity"]["call_count"] == 8
    assert report["observed_model_identity"]["provider_model_id"] == gateway.model


def test_canary_malformed_case_fails_closed_without_salvage() -> None:
    profile = _profile()
    gateway = DeterministicCanaryGateway(profile, malformed=True)

    report = run_model_canaries(
        profile,
        gateway,
        live_model_used=False,
    )

    malformed = next(case for case in report["cases"] if case["case_id"] == "MC16-07")
    assert malformed["status"] == "passed"
    assert malformed["effects"] == 0
    assert malformed["salvaged"] is False


def _profile_for_mode(mode: StructuredOutputMode):
    return resolve_model_profile(
        {
            "provider": "openai_compatible",
            "api_url": "http://localhost/chat",
            "model": "canary-model",
            "capabilities": {
                "reasoning": True,
                "structured_output": mode.value,
            },
        }
    )


def test_canary_constraint_material_reaches_provider_payload_by_mode() -> None:
    for mode in (StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA):
        profile = _profile_for_mode(mode)
        gateway = OpenAICompatibleGateway(profile)
        request, _geometry = _request(profile, gateway, requested=0, mode=mode)

        assert request.structured_output is not None
        payload = gateway.build_payload(request)

        if mode == StructuredOutputMode.GBNF:
            assert request.structured_output.grammar == CANARY_GBNF_GRAMMAR
            assert request.structured_output.schema is None
            assert payload["grammar"] == CANARY_GBNF_GRAMMAR
            assert "response_format" not in payload
        else:
            assert request.structured_output.schema == CANARY_SCHEMA
            assert request.structured_output.grammar is None
            assert payload["response_format"] == {"type": "json_schema", "json_schema": {"name": "agent_response", "schema": CANARY_SCHEMA}}
            assert "grammar" not in payload


def test_canary_observed_identity_is_unavailable_without_provider_observation() -> None:
    profile = _profile()

    class ConformingGatewayWithoutRequests(DeterministicCanaryGateway):
        def complete(self, request):
            response = super().complete(request)
            return type(response)(content=response.content)

    gateway = ConformingGatewayWithoutRequests(profile)
    report = run_model_canaries(profile, gateway, live_model_used=True)

    observed = report["observed_model_identity"]
    assert not hasattr(gateway, "requests")
    assert observed["call_count"] == 8
    assert observed["provider_observation_available"] is False
    assert observed["model"] is None
    assert observed["provider_model_id"] is None


def test_mc16_03_rejects_an_arbitrary_structured_result() -> None:
    profile = _profile()

    class ArbitraryResultGateway(DeterministicCanaryGateway):
        def complete(self, request):
            return ModelResponse(content='{"sentinel":"wrong"}')

    report = run_model_canaries(profile, ArbitraryResultGateway(profile), live_model_used=False)

    constrained_off = next(case for case in report["cases"] if case["case_id"] == "MC16-03")
    assert constrained_off["status"] == "failed"


def test_live_call_ceiling_uses_exported_evidence(monkeypatch) -> None:
    monkeypatch.setattr(canaries, "MAX_LIVE_CALLS", 7)
    profile = _profile()

    report = run_model_canaries(profile, DeterministicCanaryGateway(profile), live_model_used=True)

    ceiling_case = next(case for case in report["cases"] if case["case_id"] == "MC16-08")
    assert ceiling_case["status"] == "failed"
    assert ceiling_case["reason"] == "live call ceiling exceeded"
    assert ceiling_case["calls"] == 8
