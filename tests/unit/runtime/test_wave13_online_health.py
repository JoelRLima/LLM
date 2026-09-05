import json
from pathlib import Path

import pytest

from agent.health.online_model import (
    HEALTH_PROMPT,
    run_online_model_health_probe,
)
from agent.health.standalone import run_standalone_health_check
from agent.health_check import run_health_check
from agent.interfaces.cli import app as cli
from agent.interfaces.cli.parser import build_parser
from agent.llm.contracts import ModelResponse, StructuredOutputMode
from agent.llm.errors import ModelTimeoutError, UnsupportedModelCapability
from agent.llm.model_profile import resolve_model_profile
from agent.runtime.config_repository import ConfigRepository, packaged_config_defaults
from agent.runtime.paths import AppPaths


class _StubGateway:
    provider_name = "openai_compatible"

    def __init__(self, response=None, error=None, *, observed=None):
        self.timeout = 300.0
        self.response = response
        self.error = error
        self.observed = observed
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        metadata = {} if self.observed is None else {"observed_provider_model_id": self.observed}
        return ModelResponse(content=self.response, provider_metadata=metadata)


def _config() -> dict[str, object]:
    return packaged_config_defaults()


def _initialized_context(tmp_path: Path) -> tuple[AppPaths, Path, dict[str, object]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths, workspace, json.loads(paths.config_file.read_text(encoding="utf-8"))


def test_plain_doctor_stays_offline_and_keeps_legacy_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, workspace, _ = _initialized_context(tmp_path)
    monkeypatch.setattr(
        "agent.health.online_model.create_model_gateway",
        lambda profile: (_ for _ in ()).throw(AssertionError("plain doctor must stay offline")),
    )

    report = run_health_check(
        app_paths=paths,
        workspace=workspace,
        verbose=False,
    )

    assert "online" not in report
    assert report["readiness"]["backend_connectivity"] == "not_checked"
    assert "online_ready" not in report["readiness"]


def test_online_probe_is_one_request_with_declared_structured_contract_and_identity() -> None:
    config = _config()
    profile = resolve_model_profile(config)
    gateway = _StubGateway('{"sentinel":"W13_HEALTH_OK"}', observed="qwen2.5-local")

    report = run_online_model_health_probe(
        config,
        gateway_factory=lambda received: gateway,
    )

    assert report["state"] == "ready"
    assert report["online_ready"] is True
    assert report["reachable"] is True
    assert report["completion_ok"] is True
    assert report["request_count"] == 1
    assert len(gateway.requests) == 1
    assert gateway.requests[0].structured_output.mode is StructuredOutputMode.GBNF
    assert gateway.requests[0].messages[0].content == HEALTH_PROMPT
    assert report["runtime_profile_fingerprint"] == profile.fingerprint
    assert report["configured_profile_name"] == profile.name
    assert report["observed_model_identity"] == "qwen2.5-local"
    assert report["observed_model_identity_available"] is True
    assert report["token_count_probe_ok"] == "not_probed"
    assert report["streaming_probe"] == "not_probed"
    assert gateway.timeout == 300.0


def test_online_probe_structured_failure_is_degraded_without_fallback_requests() -> None:
    gateway = _StubGateway('{"sentinel":"WRONG"}')

    report = run_online_model_health_probe(
        _config(),
        gateway_factory=lambda profile: gateway,
    )

    assert report["state"] == "degraded"
    assert report["online_ready"] is False
    assert report["reachable"] is True
    assert report["completion_ok"] is True
    assert report["structured_probe_ok"] == "failed"
    assert report["request_count"] == 1
    assert len(gateway.requests) == 1


def test_online_probe_unsupported_structured_mode_is_degraded_without_mode_cycling() -> None:
    gateway = _StubGateway(error=UnsupportedModelCapability("unsupported"))

    report = run_online_model_health_probe(
        _config(),
        gateway_factory=lambda profile: gateway,
    )

    assert report["state"] == "degraded"
    assert report["online_ready"] is False
    assert report["structured_probe_ok"] == "unsupported"
    assert report["request_count"] == 1
    assert len(gateway.requests) == 1


def test_online_probe_timeout_is_capped_without_profile_identity_drift() -> None:
    config = _config()
    profile = resolve_model_profile(config)
    gateway = _StubGateway(error=ModelTimeoutError("timeout"))

    report = run_online_model_health_probe(
        config,
        gateway_factory=lambda received: gateway,
    )

    assert report["state"] == "unavailable"
    assert report["online_ready"] is False
    assert report["failure_reason"] == "PROBE_TIMEOUT"
    assert report["probe_timeout_seconds"] == 30.0
    assert report["transport_timeout_override_seconds"] == 30.0
    assert report["runtime_profile_fingerprint"] == profile.fingerprint


def test_online_probe_does_not_infer_observed_identity_from_declared_model() -> None:
    gateway = _StubGateway('{"sentinel":"W13_HEALTH_OK"}')

    report = run_online_model_health_probe(
        _config(),
        gateway_factory=lambda profile: gateway,
    )

    assert report["declared_model"] == "default"
    assert report["observed_model_identity"] is None
    assert report["observed_model_identity_available"] is False


def test_online_probe_prompt_excludes_workspace_memory_history_and_environment() -> None:
    config = _config()
    config["model"] = "SECRET-PROJECT-MODEL"
    config["task_history"] = "SECRET-TASK-HISTORY"
    gateway = _StubGateway('{"sentinel":"W13_HEALTH_OK"}')

    run_online_model_health_probe(
        config,
        gateway_factory=lambda profile: gateway,
    )

    request = gateway.requests[0]
    rendered = json.dumps(
        {
            "messages": [message.content for message in request.messages],
            "provider_options": request.provider_options,
        },
        ensure_ascii=False,
    )
    assert request.messages[0].content == HEALTH_PROMPT
    assert "SECRET-PROJECT-MODEL" not in rendered
    assert "SECRET-TASK-HISTORY" not in rendered


def test_online_doctor_exit_and_json_preserve_offline_truth(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "agent.health_check.run_health_check",
        lambda **kwargs: {
            "readiness": {
                "offline_ready": True,
                "online_ready": False,
            },
            "online": {"state": "degraded"},
        },
    )

    result = cli.main(
        [
            "doctor",
            "--online",
            "--json",
            "--home",
            str(home),
            "--workspace",
            str(workspace),
        ]
    )

    assert result == 1
    assert json.loads(capsys.readouterr().out)["readiness"]["offline_ready"] is True


def test_online_doctor_persists_bounded_sanitized_report(tmp_path: Path, monkeypatch) -> None:
    paths, workspace, _ = _initialized_context(tmp_path)
    gateway = _StubGateway('{"sentinel":"W13_HEALTH_OK"}')
    monkeypatch.setattr(
        "agent.health.online_model.create_model_gateway",
        lambda profile: gateway,
    )

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
        online=True,
        write_report=True,
    )
    persisted = json.loads(paths.health_report_file.read_text(encoding="utf-8"))

    assert report["readiness"]["offline_ready"] is True
    assert report["readiness"]["online_ready"] is True
    assert persisted == report
    assert "raw_response" not in json.dumps(persisted, ensure_ascii=False)
    assert "W13_HEALTH_OK" not in json.dumps(persisted, ensure_ascii=False)


def test_online_cli_surface_is_explicit_and_accepts_json() -> None:
    args = build_parser().parse_args(["doctor", "--online", "--json"])

    assert args.command == "doctor"
    assert args.online is True
    assert args.json_output is True
