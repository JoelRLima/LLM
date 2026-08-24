from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from agent.evaluation.block7_analysis_verdict import verdict
from agent.evaluation.block7_campaign_report import _observed_identity_summary
from agent.evaluation.block7_execution_evidence import identity_drift
from agent.evaluation.block7_identity import campaign_config, model_config_identity, resume_compatible
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import ModelMessage, ModelRequest, ModelResponse, ProviderCapabilities


class SequenceGateway:
    provider_name = "scripted-provider"
    model = "declared-model"
    endpoint_identity = "http://identity.example/v1"
    provider_model_id = None
    profile = {"temperature": 0.0}
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self, observed_ids: list[str | None]) -> None:
        self.observed_ids = list(observed_ids)

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        observed = self.observed_ids.pop(0)
        metadata = {} if observed is None else {"observed_provider_model_id": observed}
        return ModelResponse(content="{}", provider_metadata=metadata)

    def count_tokens(self, text: str) -> int:
        return len(text)


def _record(observed_ids: list[str | None], *, external_identity: str | None = None) -> dict:
    recorder = RecordingGateway(
        SequenceGateway(observed_ids),
        external_identity=external_identity,
    )
    for _ in observed_ids:
        recorder.complete(
            ModelRequest(
                messages=(ModelMessage("user", "identity test"),),
                model="declared-model",
                temperature=0.0,
                max_output_tokens=32,
            )
        )
    exported = recorder.export_evidence()
    return {
        "valid_repetition": True,
        "evidence": {
            "model_call_identities": exported["model_call_identities"],
            "observed_model_identity": exported["observed_provider_identity"],
        },
    }


def test_c6b_same_specific_id_is_complete_and_sufficient() -> None:
    run = _record(["model-A", "model-A", "model-A"])
    observed = run["evidence"]["observed_model_identity"]

    assert observed["available"] is True
    assert observed["consistent"] is True
    assert observed["specific"] is True
    assert observed["identity_sufficient"] is True
    assert observed["provider_model_id"] == "model-A"
    assert observed["observed_model_ids"] == ["model-A", "model-A", "model-A"]
    assert observed["distinct_observed_model_ids"] == ["model-A"]
    assert all(
        set(call) == {
            "call_index",
            "provider",
            "endpoint_identity",
            "declared_model",
            "observed_provider_model_id",
            "identity_source",
        }
        for call in run["evidence"]["model_call_identities"]
    )


def test_c6b_intra_run_drift_is_preserved_and_has_no_last_id_projection() -> None:
    run = _record(["model-A", "model-B", "model-A"])
    observed = run["evidence"]["observed_model_identity"]
    aggregate = _observed_identity_summary([run], {"model": "default"})

    assert observed["consistent"] is False
    assert observed["identity_sufficient"] is False
    assert observed["observed_model_ids"] == ["model-A", "model-B", "model-A"]
    assert observed["distinct_observed_model_ids"] == ["model-A", "model-B"]
    assert observed["provider_model_id"] is None
    assert aggregate["consistent"] is False
    assert aggregate["identity_sufficient"] is False
    assert aggregate["provider_model_id"] is None
    assert aggregate["observed_model_ids"] == ["model-A", "model-B", "model-A"]


def test_c6b_missing_per_call_provider_identity_is_insufficient_without_external_fallback() -> None:
    run = _record(["model-A", None])
    observed = run["evidence"]["observed_model_identity"]
    aggregate = _observed_identity_summary([run], {"model": "default"})

    assert observed["observed_model_ids"] == ["model-A"]
    assert observed["provider_observation_complete"] is False
    assert observed["complete"] is False
    assert observed["identity_sufficient"] is False
    assert aggregate["complete"] is False
    assert aggregate["identity_sufficient"] is False


def test_c6b_generic_default_is_available_but_insufficient() -> None:
    run = _record(["default", "default"])
    observed = run["evidence"]["observed_model_identity"]
    aggregate = _observed_identity_summary([run], {"model": "default"})

    assert observed["available"] is True
    assert observed["provider_observation_available"] is True
    assert observed["specific"] is False
    assert observed["identity_sufficient"] is False
    assert observed["provider_observation_limitation"] == "generic_provider_model_id"
    assert aggregate["identity_sufficient"] is False
    assert verdict(
        evidence_level="real_model",
        identity_consistent=True,
        complete=True,
        unknown_failures=0,
        scenario_summary={},
        aggregate_rate=1.0,
        incidents={},
        classifications={},
        installed_acceptance={"acceptance": True},
        observed_identity_available=False,
    )[0] == "INCONCLUSIVE"


def test_c6b_generic_configured_alias_does_not_drift_specific_observed_id() -> None:
    run = _record(["Qwen-specific-id"])
    observed = run["evidence"]["observed_model_identity"]

    assert identity_drift(
        {
            "provider": "scripted-provider",
            "model": "default",
            "configured_model_id": "default",
            "endpoint_identity": "http://identity.example/v1",
        },
        observed,
    ) is False
    assert observed["identity_sufficient"] is True


def test_c6b_external_identity_is_separate_and_preserves_limitation() -> None:
    run = _record([None, None], external_identity="Qwen-frozen-test")
    observed = run["evidence"]["observed_model_identity"]

    assert observed["available"] is True
    assert observed["provider_observation_available"] is False
    assert observed["identity_sufficient"] is True
    assert observed["source"] == "external_identity"
    assert observed["external_identity"] == "Qwen-frozen-test"
    assert observed["provider_observation_limitation"] == "backend_identity_unavailable"


def test_c6b_external_identity_reaches_generic_provider_projection() -> None:
    run = _record(["default", "default"], external_identity="frozen-provider-id")
    observed = run["evidence"]["observed_model_identity"]
    aggregate = _observed_identity_summary([run], {"model": "default"})

    assert observed["identity_sufficient"] is True
    assert observed["source"] == "external_identity"
    assert observed["provider_observation_limitation"] == "generic_provider_model_id"
    assert aggregate["identity_sufficient"] is True
    assert aggregate["source"] == "external_identity"


def test_c6b_campaign_summary_keeps_large_ordered_identity_lossless() -> None:
    runs = [_record(["model-A"]) for _ in range(65)]
    observed = _observed_identity_summary(runs, {"model": "default"})

    ordered = observed["observed_model_ids"]
    calls = observed["call_identities"]
    assert isinstance(ordered, dict)
    assert ordered["encoding"] == "per_run_ordered_identity_sequence"
    assert ordered["count"] == 65
    assert len(ordered["sequences"]) == 65
    assert isinstance(calls, dict)
    assert len(calls["sequences"]) == 65


def test_c6b_resume_compares_ordered_full_identity_even_when_last_id_matches(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    config = campaign_config(root, output_dir=tmp_path / "block7")
    previous = _record(["model-A", "model-B"])["evidence"]["observed_model_identity"]
    changed = _record(["model-C", "model-B"])["evidence"]["observed_model_identity"]
    existing = deepcopy(config)
    current = deepcopy(config)
    existing["observed_model_identity"] = previous
    current["observed_model_identity"] = changed

    assert not resume_compatible(existing, current)


def test_c6b_resume_rejects_changed_external_identity(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    config = campaign_config(root, output_dir=tmp_path / "block7")
    existing = deepcopy(config)
    current = deepcopy(config)
    existing["observed_model_identity"] = _record(
        [None], external_identity="Qwen-frozen-A"
    )["evidence"]["observed_model_identity"]
    current["observed_model_identity"] = _record(
        [None], external_identity="Qwen-frozen-B"
    )["evidence"]["observed_model_identity"]

    assert not resume_compatible(existing, current)


def test_c6b_frozen_external_identity_is_part_of_phase5_model_config_resume_identity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    existing_identity = model_config_identity(root, external_identity="frozen-provider-A")
    current_identity = model_config_identity(root, external_identity="frozen-provider-B")
    existing = {
        **campaign_config(root, output_dir=tmp_path / "block7"),
        "model_identity": existing_identity,
        "model_config_fingerprint": existing_identity["model_config_fingerprint"],
    }
    current = {
        **campaign_config(root, output_dir=tmp_path / "block7"),
        "model_identity": current_identity,
        "model_config_fingerprint": current_identity["model_config_fingerprint"],
    }

    assert existing_identity["external_identity"] == "frozen-provider-A"
    assert existing_identity["external_identity_source"] == "external_identity"
    assert not resume_compatible(existing, current)


def test_c6b_phase5_cli_freezes_external_identity_without_provider_probe(tmp_path: Path, monkeypatch) -> None:
    import scripts.run_block7 as run_block7
    from agent.llm.providers import openai_compatible

    class StubProvider:
        def __init__(self, profile: dict) -> None:
            self.profile = profile

    captured: dict[str, object] = {}

    def fake_real_campaign(_root: Path, **kwargs):
        captured["external_identity"] = kwargs["external_identity"]
        gateway = kwargs["gateway_factory"]("objective", tmp_path)
        captured["gateway_external_identity"] = gateway.external_identity
        return {}

    monkeypatch.setattr(openai_compatible, "OpenAICompatibleGateway", StubProvider)
    monkeypatch.setattr(run_block7, "run_real_model_campaign", fake_real_campaign)

    assert run_block7.main(
        [
            "--phase",
            "5",
            "--qwen-loaded",
            "--external-identity",
            "frozen-phase5-provider",
            "--output",
            str(tmp_path / "phase5.json"),
        ]
    ) == 0
    assert captured == {
        "external_identity": "frozen-phase5-provider",
        "gateway_external_identity": "frozen-phase5-provider",
    }
