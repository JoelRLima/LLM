"""Private task submission orchestration over the retained runtime owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_Settlement = TypeVar("_Settlement")


def _execute_early_resume(
    owner: Any, entry: str, stream_callback: Callable[[str], None] | None,
    *, settle: Callable[..., _Settlement],
) -> _Settlement | None:
    if entry == "headless-resume":
        resume = getattr(owner, "resume", None)
        return settle(resume() if callable(resume) else owner.run(None, explicit_resume=True))
    if entry == "retry" and stream_callback is not None:
        resume = getattr(owner, "resume", None)
        if callable(resume):
            return settle(resume(stream_callback=stream_callback))
    return None


def _execute_headless_run(
    owner: Any, interact: Any, text: str, visible: str, dispatch: Any,
    *, settle: Callable[..., _Settlement], prepare_dispatch: Callable[[str], Any],
) -> _Settlement:
    request = (dispatch or prepare_dispatch(text))._request
    if request.action.value == "continue" or request.directive is None or request.subject is None:
        raise ValueError("RUN requires a TaskRunDirective")
    if callable(interact):
        return settle(interact(
            request.subject, boundary="task", visible_user_text=visible or request.subject,
            task_payload=visible or request.subject,
        ))
    return settle(owner.run(request.subject, task_run_directive=request.directive), legacy=True)


def _execute_submission(
    owner: Any, text: str, *, entry: str,
    visible_text: str | None = None, dispatch: Any = None,
    stream_callback: Callable[[str], None] | None,
    settle: Callable[..., _Settlement], prepare_dispatch: Callable[[str], Any],
) -> _Settlement:
    interact = getattr(owner, "interact", None)
    visible = text if visible_text is None else visible_text
    early_resume = _execute_early_resume(owner, entry, stream_callback, settle=settle)
    if early_resume is not None:
        return early_resume
    if entry == "headless-run":
        return _execute_headless_run(
            owner, interact, text, visible, dispatch, settle=settle, prepare_dispatch=prepare_dispatch,
        )
    if entry not in {"natural", "agent", "retry"}:
        raise ValueError("unsupported task entry")
    payload = "/continue" if entry == "retry" else text
    if callable(interact):
        kwargs: dict[str, Any] = {"boundary": "natural" if entry == "natural" else "task"}
        if entry != "natural" or visible_text is not None:
            kwargs.update(visible_user_text=visible, task_payload=payload)
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        return settle(interact(payload, **kwargs))
    if entry == "natural":
        value = owner.run(text)
    else:
        request = (dispatch or prepare_dispatch(payload))._request
        value = owner.resume() if request.action.value == "continue" else owner.run(
            request.subject, task_run_directive=request.directive,
        )
    return settle(value, legacy=True)
