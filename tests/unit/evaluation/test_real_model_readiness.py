from __future__ import annotations

import json

import pytest

from agent.evaluation.block7 import H_SERIES
from agent.evaluation.real_model_readiness import (
    REAL_MODEL_READINESS_VERSION,
    readiness_campaign_policy,
    real_model_readiness_scenarios,
)
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)


class _TraceGateway:
    provider_name = "trace-provider"
    model = "trace-model"

    def count_tokens(self, text: str) -> int:
        return len(text)

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content='{"answer":"observed"}',
            reasoning="private reasoning must not be stored",
            usage=TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        )


class _TraceStreamGateway(_TraceGateway):
    def stream(self, request: ModelRequest):
        del request
        yield StreamEvent(
            StreamEventType.REASONING,
            text="PRIVATE_REASONING_MARKER",
        )
        yield StreamEvent(StreamEventType.CONTENT, text="VISIBLE_CONTENT_MARKER")


class _FailingTraceStreamGateway(_TraceStreamGateway):
    def stream(self, request: ModelRequest):
        yield from super().stream(request)
        raise RuntimeError("stream failed after visible content")


def test_readiness_set_versions_r1_to_r10_through_existing_contracts() -> None:
    scenarios = real_model_readiness_scenarios()

    assert [item.metadata["readiness_id"] for item in scenarios] == [
        f"R{index}" for index in range(1, 11)
    ]
    assert all(
        item.metadata["scenario_set_version"] == REAL_MODEL_READINESS_VERSION
        for item in scenarios
    )
    assert all(item.metadata["real_model_readiness"] is True for item in scenarios)
    assert {item.metadata["source_h_id"] for item in scenarios} <= {
        item.h_id for item in H_SERIES
    }


def test_readiness_policy_is_small_deterministic_and_preserves_every_raw_run() -> None:
    policy = readiness_campaign_policy()

    assert policy["repetitions_per_scenario"] == 3
    assert policy["raw_runs_preserved"] is True
    assert policy["cherry_picking_permitted"] is False
    assert policy["decoding"]["temperature"] == 0.0
    assert policy["decoding"]["seed"] == 0
    assert policy["decoding"]["unsupported_controls"] == "record_as_unsupported_without_substitution"
    assert "per_scenario_pass_rate" in policy["aggregation"]
    assert "reported_and_accounted_token_summary" in policy["aggregation"]


def test_existing_trace_records_attempt_config_usage_and_latency_without_reasoning() -> None:
    gateway = RecordingGateway(_TraceGateway())
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="objective"),),
        model="trace-model",
        temperature=0.0,
        max_output_tokens=32,
        context_compacted=True,
        context_limit=100,
    )

    gateway.complete(request)
    record = gateway.export_evidence()["model_calls"][0]

    assert record["provider_call_succeeded"] is True
    assert record["duration_ms"] >= 0
    assert record["request"]["config_fingerprint"]
    assert record["request"]["estimated_request_tokens"] == len("objective")
    assert record["request"]["request_estimation_source"] == "provider_text_tokenizer"
    assert record["request"]["context_compacted"] is True
    assert record["request"]["context_limit"] == 100
    assert record["request"]["request_utilization_ratio"] == len("objective") / 100
    assert record["usage"]["source"] == "provider_reported"
    assert record["usage"]["complete"] is True
    assert "reasoning" not in record


def test_stream_trace_persists_visible_content_without_private_reasoning() -> None:
    gateway = RecordingGateway(_TraceStreamGateway())
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="objective"),),
        model="trace-model",
        temperature=0.0,
        max_output_tokens=32,
    )

    list(gateway.stream(request))
    evidence_text = json.dumps(gateway.export_evidence(), sort_keys=True)

    assert "VISIBLE_CONTENT_MARKER" in evidence_text
    assert "PRIVATE_REASONING_MARKER" not in evidence_text


def test_failed_stream_trace_keeps_visible_content_without_private_reasoning() -> None:
    gateway = RecordingGateway(_FailingTraceStreamGateway())
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="objective"),),
        model="trace-model",
        temperature=0.0,
        max_output_tokens=32,
    )

    with pytest.raises(RuntimeError, match="stream failed after visible content"):
        list(gateway.stream(request))
    evidence_text = json.dumps(gateway.export_evidence(), sort_keys=True)

    assert "VISIBLE_CONTENT_MARKER" in evidence_text
    assert "PRIVATE_REASONING_MARKER" not in evidence_text
    assert gateway.export_evidence()["model_calls"][0]["provider_call_succeeded"] is False


def test_trace_config_fingerprint_excludes_dynamic_request_measurements() -> None:
    gateway = RecordingGateway(_TraceGateway())
    requests = [
        ModelRequest(
            messages=(ModelMessage(role="user", content="short"),),
            model="trace-model",
            temperature=0.0,
            max_output_tokens=32,
        ),
        ModelRequest(
            messages=(ModelMessage(role="user", content="a much longer objective"),),
            model="trace-model",
            temperature=0.0,
            max_output_tokens=32,
        ),
    ]

    for request in requests:
        gateway.complete(request)

    records = gateway.export_evidence()["model_calls"]
    assert records[0]["request"]["config_fingerprint"] == records[1]["request"]["config_fingerprint"]
    assert records[0]["request"]["estimated_request_tokens"] != records[1]["request"]["estimated_request_tokens"]
