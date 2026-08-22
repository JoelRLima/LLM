from __future__ import annotations

from pathlib import Path

from agent.evaluation.block7 import (
    H_SERIES,
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    HRunEvidence,
    RepetitionPolicy,
    digest_fixture,
    sanitize_evidence,
    validate_h_series,
)
from agent.evaluation.block7_runner import campaign_config, phase4_audit, resume_compatible
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import ModelMessage, ModelRequest, ModelResponse, ProviderCapabilities


def test_h_series_is_exactly_versioned_h1_to_h12() -> None:
    validate_h_series()
    assert H_SERIES_VERSION == "B7-HSERIES-V1.0"
    assert [item.h_id for item in H_SERIES] == [f"H{index}" for index in range(1, 13)]
    assert len({item.h_id for item in H_SERIES}) == 12


def test_h2_preserves_historical_scalar_fixture_and_binding_shape() -> None:
    h2 = next(item for item in H_SERIES if item.h_id == "H2")
    arm = h2.arms[0]

    assert arm.initial_files == {"fonte_h2.txt": "orion_584271"}
    assert arm.oracle["binding_target"] == "pattern"
    assert arm.oracle["binding_path"] == []
    assert h2.required_repetitions == 5


def test_nested_binding_uses_current_structured_grep_schema() -> None:
    h3 = next(item for item in H_SERIES if item.h_id == "H3")
    assert h3.arms[0].oracle["binding_path"] == [0, "content"]


def test_repetition_policy_is_bounded_and_h2_is_special() -> None:
    policy = RepetitionPolicy()

    assert policy.required_for("H1") == 3
    assert policy.required_for("H2") == 5
    assert policy.required_for("h2") == 5
    assert policy.maximum_repetitions == 5


def test_evidence_export_is_bounded_and_secret_safe() -> None:
    evidence = HRunEvidence(
        scenario_id="H8-failure",
        repetition=1,
        epoch="dry-run-1",
        candidate_identity="candidate-sha",
        model_fingerprint={"model": "scripted", "authorization": "Bearer TOPSECRET"},
        evidence_level=EvidenceLevel.DETERMINISTIC,
        objective="H8 objective",
        initial_fixture_digest=digest_fixture({"h8.txt": "fixture"}),
        model_decisions=(
            "request failed api_key=TOPSECRET password=TOPSECRET token=TOPSECRET",
        ),
        final_answer="controlled failure",
        measurement={"token_usage_complete": False, "estimated_tokens": 4},
        deterministic_failures=("tool_failed",),
        causal_classification=CausalFailureClass.MODEL_CAPABILITY,
    )

    exported = evidence.to_dict()
    rendered = str(exported)
    assert "TOPSECRET" not in rendered
    assert "api_key=" not in rendered
    assert "Authorization" not in rendered
    assert exported["evidence_schema_version"] == 1
    assert exported["causal_classification"] == "MODEL_CAPABILITY"

    bounded = sanitize_evidence({"values": ["x"] * 100})
    assert bounded["values"][-1] == "[ITEM_LIMIT]"


def test_recording_gateway_is_observational_and_keeps_call_identity() -> None:
    class Gateway:
        provider_name = "scripted"
        model = "scripted-model"
        profile = {"temperature": 0.0}
        capabilities = ProviderCapabilities(streaming=False)

        def __init__(self) -> None:
            self.requests = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(content='{"action":"final"}')

        def count_tokens(self, text: str) -> int:
            return len(text)

    underlying = Gateway()
    recorder = RecordingGateway(underlying)
    request = ModelRequest(
        messages=(ModelMessage("system", "You are a Router Agent"), ModelMessage("user", "objective")),
        model="scripted-model",
        temperature=0.0,
        max_output_tokens=128,
    )

    response = recorder.complete(request)

    assert underlying.requests == [request]
    assert response.content == '{"action":"final"}'
    assert recorder.count_tokens("abc") == 3
    exported = recorder.export_evidence()
    assert len(exported["route_decisions"]) == 1
    assert exported["model_calls"][0]["response"] == '{"action":"final"}'


def test_phase4_audit_covers_every_arm_with_sixteen_answers() -> None:
    audit = phase4_audit(Path(__file__).parents[3])

    assert audit["known_deterministic_blockers"] == []
    assert len(audit["questions"]) == 16
    assert len(audit["reviews"]) == 13
    assert all(len(review["questions"]) == 16 for review in audit["reviews"])
    assert audit["h2_specific_audit"]["fixture_content"] == "orion_584271"
    assert audit["grounding_audit"]["H9"]


def test_resume_requires_exact_candidate_and_campaign_identity(tmp_path) -> None:
    repo_root = Path(__file__).parents[3]
    config = campaign_config(repo_root, output_dir=tmp_path / "block7")

    assert resume_compatible(config, config)
    changed = dict(config)
    changed["candidate"] = dict(config["candidate"])
    changed["candidate"]["source_fingerprint"] = "changed"
    assert not resume_compatible(config, changed)
