from __future__ import annotations

from agent.interaction.service import InteractionService
from agent.interaction.types import InteractionAction

from ._helpers import application


def test_explicit_read_has_zero_resolver_calls_and_one_visible_pair() -> None:
    app = application([])
    app.run = lambda *_args, **_kwargs: __import__("types").SimpleNamespace(
        status="succeeded", success=True, answer="read answer", error=None
    )
    result = InteractionService(app).interact(
        "/read Analyze parser.py",
        boundary="task",
        visible_user_text="/agent /read Analyze parser.py",
        task_payload="/read Analyze parser.py",
    )
    assert result.resolution is not None
    assert result.resolution.action is InteractionAction.RUN
    assert app.gateway.calls == []
    assert len(app.session.messages) == 3


def test_provider_failure_is_bounded_and_does_not_store_raw_exception() -> None:
    app = application([])

    class FailingGateway(type(app.gateway)):
        def complete(self, request):
            del request
            raise RuntimeError("raw provider secret")

    app.session.gateway = FailingGateway([])
    result = InteractionService(app).interact("hello")
    assert result.status == "failed"
    assert result.answer == ""
    assert result.error is not None
    assert "raw provider secret" not in result.error
    assert app.session.messages == [{"role": "system", "content": "test system"}]


def test_natural_visible_surface_cannot_diverge_from_semantic_subject() -> None:
    app = application([])
    result = InteractionService(app).interact(
        "semantic subject",
        boundary="natural",
        visible_user_text="different visible text",
    )
    assert result.status == "needs_input"
    assert result.reason_code == "INTERACTION_INPUT_INVALID"
    assert app.gateway.calls == []
    assert app.session.messages == [{"role": "system", "content": "test system"}]


def test_natural_semantic_subject_limit_is_checked_before_resolution() -> None:
    app = application([])
    subject = "x" * 8193
    result = InteractionService(app).interact(subject, boundary="natural")
    assert result.status == "needs_input"
    assert result.reason_code == "INTERACTION_INPUT_TOO_LARGE"
    assert app.gateway.calls == []
    assert app.session.messages == [{"role": "system", "content": "test system"}]


def test_task_exception_restores_transcript_and_re_raises() -> None:
    app = application([])
    original = [dict(item) for item in app.session.messages]

    def run(*_args, **_kwargs):
        app.session.messages = [{"role": "system", "content": "compressed"}]
        raise RuntimeError("downstream task")

    app.run = run
    try:
        InteractionService(app).interact(
            "/read Analyze parser.py",
            boundary="task",
            visible_user_text="/agent /read Analyze parser.py",
            task_payload="/read Analyze parser.py",
        )
    except RuntimeError as exc:
        assert str(exc) == "downstream task"
    else:
        raise AssertionError("downstream exception was swallowed")
    assert app.session.messages == original
