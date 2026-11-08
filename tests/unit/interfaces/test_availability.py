"""Deterministic availability tests without sockets or model loading."""

from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from llm_agent.agent.llm.errors import ModelConnectionError, ModelTimeoutError
from llm_agent.interactive.availability import ResourceState
from tests.unit.interfaces.test_shared_interactive_core import create_session, prepare_fake_runtime


def configure(monkeypatch):
    profile = SimpleNamespace(fingerprint="a", api_url="http://localhost/v1/chat/completions", model="test", timeout=2)
    monkeypatch.setattr(
        "llm_agent.interactive.availability.ConfigRepository.load",
        lambda *a, **kw: SimpleNamespace(model_profile=profile),
    )
    return profile


def test_missing_configuration_local_queries(tmp_path):
    session = create_session(tmp_path)
    assert session.availability_snapshot()["backend"]["state"] == "not_configured"
    assert session.submit("/ls").result(2)[0]["status"] == "succeeded"
    assert session.inspect("tasks").result(2)[0]["status"] == "succeeded"
    session.close().result(2)


@pytest.mark.parametrize(
    "error,state",
    [(ModelConnectionError("refused"), "unavailable"), (ModelTimeoutError("late"), "temporarily_unavailable")],
)
def test_failure_manual_recovery_no_repeat(tmp_path, monkeypatch, error, state):
    configure(monkeypatch)
    session = create_session(tmp_path)
    calls = []

    def execute(*a, **kw):
        calls.append(1)
        raise error

    prepare_fake_runtime(session, monkeypatch, execute)
    with pytest.raises(type(error)):
        session.submit("first").result(2)
    assert session.availability.value.state == state
    for _ in range(3):
        with pytest.raises(ValueError, match="BACKEND_UNAVAILABLE"):
            session.submit("repeat")
    assert calls == [1]
    assert session.submit("/ls").result(2)[0]["status"] == "succeeded"
    monkeypatch.setattr("llm_agent.interactive.availability.probe_catalog", lambda p: "listed")
    result = session.test_connection().result(2)
    assert result["backend"]["connectivity"] == "verified"
    assert result["backend"]["model_readiness"] == "listed"
    assert calls == [1]
    session.close().result(2)


def test_changed_profile_invalidates_and_task_failure_is_separate(tmp_path, monkeypatch):
    profile = configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    session.availability.test(lambda p: "listed")
    profile.fingerprint = "changed-profile-endpoint"
    state = session.availability_snapshot()["backend"]
    assert state["connectivity"] == "unverified"
    assert state["generation"] == 2
    session.availability.failure(ValueError("task failure"))
    assert session.availability.value.state == ResourceState.UNVERIFIED
    session.close().result(2)


@pytest.mark.parametrize("status", [401, 403, 404, 405, 501, 503])
def test_catalog_http_diagnostic_is_inconclusive(tmp_path, monkeypatch, status):

    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()

    def denied(*args, **kwargs):
        raise HTTPError("http://localhost/v1/models", status, "catalog response", {}, None)

    monkeypatch.setattr("llm_agent.interactive.availability.urlopen", denied)
    projection = session.availability.test()
    assert projection["backend"]["connectivity"] == "unverified"
    assert projection["backend"]["model_readiness"] == "unverified"
    assert projection["backend"]["state"] == "unverified"
    session.close().result(2)


def test_unconfigured_submit_does_not_start_runtime(tmp_path, monkeypatch):
    session = create_session(tmp_path)
    monkeypatch.setattr(
        "llm_agent.interactive.session.start_interactive_session",
        lambda *a, **kw: pytest.fail("runtime initialized without profile"),
    )
    with pytest.raises(ValueError, match="BACKEND_NOT_CONFIGURED"):
        session.submit("resuma o objetivo")
    assert session.availability.test()["backend"]["state"] == "not_configured"
    session.close().result(2)


def test_catalog_auth_does_not_clear_real_transport_lock(tmp_path, monkeypatch):


    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    session.availability.failure(ModelConnectionError("refused"))
    monkeypatch.setattr(
        "llm_agent.interactive.availability.probe_catalog",
        lambda profile: (_ for _ in ()).throw(PermissionError()),
    )
    result = session.availability.test()
    assert result["backend"]["state"] == "unavailable"
    assert result["backend"]["reason"] == "MODEL_CONNECTION_ERROR"
    session.close().result(2)


def test_catalog_success_only_proves_catalog_and_profile_change_invalidates(tmp_path, monkeypatch):
    profile = configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    monkeypatch.setattr("llm_agent.interactive.availability.probe_catalog", lambda _profile: "unverified")
    result = session.availability.test()["backend"]
    assert result["connectivity"] == "verified"
    assert result["model_readiness"] == "unverified"
    profile.fingerprint = "new-profile"
    assert session.availability_snapshot()["backend"]["connectivity"] == "unverified"
    session.close().result(2)


def test_http_200_catalog_does_not_claim_model_readiness(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"data":[{"id":"another-model"}]}'

    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    monkeypatch.setattr("llm_agent.interactive.availability.urlopen", lambda *_a, **_kw: Response())
    state = session.availability.test()["backend"]
    assert state["connectivity"] == "verified"
    assert state["model_readiness"] == "unverified"
    session.availability.failure(ModelConnectionError("prior generation refusal"))
    recovered = session.availability.test(lambda _profile: "unverified")["backend"]
    assert recovered["connectivity"] == "verified"
    assert recovered["model_readiness"] == "unverified"
    assert recovered["state"] == "available"
    session.close().result(2)


def test_gui_availability_copy_uses_accented_labels():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "prototypes/workbench-comparison/pywebview/workbench.js").read_text(encoding="utf-8")
    assert r"\u00edvel" in source
    assert r"\u00e3o configurado" in source
    assert "\ufffd" not in source


@pytest.mark.parametrize(
    "diagnostic",
    [
        HTTPError("http://localhost/v1/models", status, "catalog response", {}, None)
        for status in (401, 403, 404, 405, 501, 503)
    ]
    + [ValueError("invalid catalog"), LookupError("catalog GET unsupported"), TimeoutError("catalog timeout"), OSError("catalog read failed")],
)
@pytest.mark.parametrize("prior_transport_failure", [False, True])
def test_inconclusive_catalog_matrix_preserves_prior_transport_lock(
    tmp_path, monkeypatch, diagnostic, prior_transport_failure
):
    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    if prior_transport_failure:
        session.availability.failure(ModelConnectionError("generation endpoint refused"))
        locked = session.availability.value

    def inconclusive(*_args, **_kwargs):
        raise diagnostic

    monkeypatch.setattr("llm_agent.interactive.availability.urlopen", inconclusive)
    result = session.availability.test()["backend"]
    if prior_transport_failure:
        assert result["state"] == "unavailable"
        assert result["reason"] == "MODEL_CONNECTION_ERROR"
        assert session.availability.value == locked
        with pytest.raises(ValueError, match="BACKEND_UNAVAILABLE"):
            session.submit("natural request")
    else:
        assert result["state"] == "unverified"
        assert result["connectivity"] == "unverified"
        assert result["model_readiness"] == "unverified"
        calls = []
        prepare_fake_runtime(session, monkeypatch, lambda *a, **kw: calls.append("inference") or {"status": "succeeded"})
        assert session.submit("natural request").result(2)[0]["status"] == "succeeded"
        assert calls == ["inference"]
    session.close().result(2)


@pytest.mark.parametrize("payload", [b"{}", b'{"error":"bad"}', b'{"data":[{}]}', b"not-json"])
def test_invalid_catalog_payload_is_inconclusive(tmp_path, monkeypatch, payload):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return payload

    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    monkeypatch.setattr("llm_agent.interactive.availability.urlopen", lambda *_a, **_kw: Response())
    assert session.availability.test()["backend"]["state"] == "unverified"
    session.close().result(2)


@pytest.mark.parametrize("reason", ["INTERACTION_RESOLVER_UNAVAILABLE", "INTERACTION_RESPONSE_UNAVAILABLE"])
def test_generic_unavailable_reason_code_is_not_transport_evidence(tmp_path, monkeypatch, reason):
    configure(monkeypatch)
    session = create_session(tmp_path)
    session.availability_snapshot()
    before = session.availability.value
    session.availability.observe({"reason_code": reason, "status": "failed"})
    assert session.availability.value == before
    session.availability.failure(ModelConnectionError("causal transport evidence"))
    locked = session.availability.value
    session.availability.observe({"reason_code": reason, "status": "failed"})
    assert session.availability.value == locked
    session.close().result(2)


def test_natural_gateway_connection_failure_blocks_second_request(tmp_path, monkeypatch):
    from llm_agent.agent.application import AgentApplication
    from llm_agent.agent.llm.contracts import ProviderCapabilities
    from llm_agent.agent.tools.authority import OperationalMode
    from llm_agent.application.approval import _bind_approval_presenter
    from llm_agent.application.task_execution import _retain_runtime
    from llm_agent.interactive import session as shared_session

    session = create_session(tmp_path)
    from llm_agent.agent.runtime.config_repository import ConfigRepository

    ConfigRepository(session.start.app_paths).initialize()

    class RefusingGateway:
        provider_name = "offline-refusing"
        model = "offline-refusing"
        capabilities = ProviderCapabilities(streaming=False)

        def __init__(self):
            self.calls = 0

        def complete(self, _request):
            self.calls += 1
            raise ModelConnectionError("deterministic offline connection refusal")

    gateway = RefusingGateway()

    def start(request, approval):
        return _retain_runtime(
            AgentApplication.create(
                paths=request.app_paths,
                workspace=request.workspace,
                gateway=gateway,
                approval_policy=_bind_approval_presenter(approval),
                operational_mode=OperationalMode.READ_ONLY,
                configure_logging=False,
            )
        )

    monkeypatch.setattr(shared_session, "start_interactive_session", start)
    try:
        observation, _ = session.submit("Resuma o objetivo offline.").result(20)
        assert observation["reason_code"] == "INTERACTION_RESOLVER_UNAVAILABLE"
        assert observation["model_transport_failure"] == "MODEL_CONNECTION_ERROR"
        assert session.availability.value.state == "unavailable"
        assert gateway.calls == 1
        with pytest.raises(ValueError, match="BACKEND_UNAVAILABLE"):
            session.submit("Tente novamente automaticamente.")
        assert gateway.calls == 1

        class CatalogResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"data":[{"id":"different-model"}]}'

        monkeypatch.setattr(
            "llm_agent.interactive.availability.urlopen",
            lambda *_args, **_kwargs: CatalogResponse(),
        )
        manual = session.availability.test()["backend"]
        assert manual["connectivity"] == "verified"
        assert manual["model_readiness"] == "unverified"
        assert manual["state"] == "available"
        assert gateway.calls == 1
    finally:
        session.close().result(10)
