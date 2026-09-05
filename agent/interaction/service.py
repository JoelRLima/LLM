"""The sole W12 orchestration owner above task and response boundaries."""

from __future__ import annotations

from typing import Any, Callable

from agent.cancellation import CancellationToken
from agent.interfaces.task_directives import (
    ParsedTaskRequest,
    TaskDirectiveParseError,
    TaskRequestAction,
    parse_task_request,
)
from agent.llm.errors import (
    ModelConnectionError,
    ModelProviderError,
    ModelResponseError,
    ModelTimeoutError,
    UnsupportedModelCapability,
)
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger

from .admission import admit_interaction
from .errors import (
    INTERACTION_CANCELLED,
    INTERACTION_EVIDENCE_MISMATCH,
    INTERACTION_INPUT_INVALID,
    INTERACTION_INPUT_REQUIRED,
    INTERACTION_INPUT_TOO_LARGE,
    INTERACTION_INTERNAL_FAILED,
    INTERACTION_REQUEST_CONTRACT_MISMATCH,
    INTERACTION_RESOLVER_INVALID,
    INTERACTION_RESOLVER_UNAVAILABLE,
    INTERACTION_RESPONSE_CONTEXT_TOO_LARGE,
    INTERACTION_RESPONSE_FAILED,
    INTERACTION_RESPONSE_UNAVAILABLE,
    INTERACTION_TRANSCRIPT_INVALID,
    InteractionAdmissionError,
    public_explanation,
)
from .resolver import (
    InteractionResolver,
    ResolverInvalid,
    ResolverUnavailable,
    build_interaction_context,
)
from .response import ResponseContextTooLarge, build_response_request_plan, complete_response
from .transcript import (
    commit_one_pair,
    restore_visible_messages,
    snapshot_visible_messages,
    validate_transcript_messages,
    visible_text_for_run_result,
)
from .types import (
    AgentInteractionResult,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionProvenance,
    InteractionResolution,
)

MAX_STRING_LENGTH = 8192


class InteractionService:
    """Serialize W12 work through the application's existing lock boundary."""
    def __init__(self, application: Any) -> None:
        self.application = application
        self.session = application.session
        self._active_model_cancellation: CancellationToken | None = None

    def _publish_active(self, token: CancellationToken) -> None:
        self._active_model_cancellation = token

    def _clear_active(self, token: CancellationToken) -> None:
        if self._active_model_cancellation is token:
            self._active_model_cancellation = None

    def cancel_active_model_call(self) -> bool:
        token = self._active_model_cancellation
        if token is None:
            return False
        token.cancel()
        return True

    def interact(
        self,
        text: str,
        *,
        boundary: InteractionBoundary | str = InteractionBoundary.NATURAL,
        visible_user_text: str | None = None,
        task_payload: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> AgentInteractionResult:
        return self.interact_locked(
            text,
            boundary=boundary,
            visible_user_text=visible_user_text,
            task_payload=task_payload,
            stream_callback=stream_callback,
        )

    def _usage(self, context: Any) -> dict[str, Any]:
        if context is None:
            return {"model_calls": 0, "accounted_tokens": 0, "token_usage_complete": True}
        try:
            snapshot = context.budget_snapshot()
        except Exception:
            return {"model_calls": 0, "accounted_tokens": 0, "token_usage_complete": True}
        return {
            "model_calls": snapshot.model_calls,
            "accounted_tokens": snapshot.accounted_tokens,
            "token_usage_complete": snapshot.token_usage_complete,
        }

    def _failure(
        self,
        *,
        status: str,
        reason_code: str,
        resolution: InteractionResolution | None = None,
        answer: str = "",
        error: str | None = None,
        context: Any = None,
    ) -> AgentInteractionResult:
        return AgentInteractionResult(
            status=status,
            answer=answer,
            resolution=resolution,
            error=error,
            reason_code=reason_code,
            interaction_usage=self._usage(context),
        )

    @staticmethod
    def _input_resolution(
        boundary: InteractionBoundary,
        reason_code: str,
    ) -> InteractionResolution:
        return InteractionResolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            deliberation_profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.NONE,
            subject=None,
            reason_code=reason_code,
        )

    def _input_failure(
        self,
        boundary: InteractionBoundary,
        reason_code: str,
    ) -> AgentInteractionResult:
        """Project bounded input failures before resolver or task dispatch."""
        resolution = self._input_resolution(boundary, reason_code)
        return self._failure(
            status="needs_input",
            reason_code=reason_code,
            resolution=resolution,
            answer=public_explanation(reason_code),
        )

    @staticmethod
    def _natural_input_reason(text: object, visible_user_text: object | None) -> str | None:
        """Validate NATURAL identity and its semantic subject before dispatch."""
        # NATURAL subject identity is checked before any resolver or task work.
        if type(text) is not str:
            return INTERACTION_INPUT_INVALID
        if visible_user_text is not None and (
            type(visible_user_text) is not str or visible_user_text != text
        ):
            return INTERACTION_INPUT_INVALID
        if not text.strip():
            return INTERACTION_INPUT_REQUIRED
        if len(text) > MAX_STRING_LENGTH:
            return INTERACTION_INPUT_TOO_LARGE
        return None

    @staticmethod
    def _visible_input_reason(visible: object) -> str | None:
        if type(visible) is not str:
            return INTERACTION_INPUT_INVALID
        if not visible.strip():
            return INTERACTION_INPUT_REQUIRED
        if len(visible) > MAX_STRING_LENGTH:
            return INTERACTION_INPUT_TOO_LARGE
        return None

    def _commit_clarify(
        self,
        visible_text: str,
        resolution: InteractionResolution,
        *,
        context: Any = None,
    ) -> AgentInteractionResult:
        answer = public_explanation(resolution.reason_code or INTERACTION_INTERNAL_FAILED)
        commit_one_pair(self.session, visible_text, answer)
        return AgentInteractionResult(
            status="needs_input",
            answer=answer,
            resolution=resolution,
            reason_code=resolution.reason_code,
            interaction_usage=self._usage(context),
        )

    def _commit_unavailable(
        self,
        visible_text: str,
        reason_code: str,
        *,
        context: Any = None,
    ) -> AgentInteractionResult:
        answer = public_explanation(reason_code)
        commit_one_pair(self.session, visible_text, answer)
        return self._failure(
            status="unavailable",
            reason_code=reason_code,
            answer=answer,
            context=context,
        )

    def _cancelled(self, context: Any = None) -> AgentInteractionResult:
        return self._failure(
            status="failed",
            reason_code=INTERACTION_CANCELLED,
            error=public_explanation(INTERACTION_CANCELLED),
            context=context,
        )

    def _response_call(
        self,
        context: Any,
        request: Any,
        *,
        callback: Callable[[str], None] | None,
    ) -> str:
        token = context.cancellation
        if token.cancelled:
            raise ResolverUnavailable(INTERACTION_CANCELLED)
        self._publish_active(token)
        try:
            content = complete_response(context, request, callback=callback)
            if token.cancelled:
                raise ResolverUnavailable(INTERACTION_CANCELLED)
            return content
        finally:
            self._clear_active(token)

    def _dispatch_task(
        self,
        resolution: InteractionResolution,
        visible_text: str,
        *,
        stream_callback: Callable[[str], None] | None,
        context: Any = None,
    ) -> AgentInteractionResult:
        snapshot = snapshot_visible_messages(self.session.messages)
        stable_result: Any = None
        try:
            if resolution.action is InteractionAction.CONTINUE:
                stable_result = self.application.resume(stream_callback=stream_callback)
            else:
                from agent.runtime.task_directives import TaskRunDirective
                if resolution.directive is None or resolution.subject is None or resolution.deliberation_profile is None:
                    raise InteractionAdmissionError(INTERACTION_INPUT_INVALID)
                directive = TaskRunDirective(
                    directive=resolution.directive,
                    deliberation_profile=resolution.deliberation_profile,
                    subject=resolution.subject,
                )
                run_locked = getattr(self.application, "_run_locked", None)
                if callable(run_locked):
                    stable_result = run_locked(
                        resolution.subject,
                        stream_callback=stream_callback,
                        task_run_directive=directive,
                    )
                else:
                    stable_result = self.application.run(
                        resolution.subject,
                        stream_callback=stream_callback,
                        task_run_directive=directive,
                    )
        finally:
            restore_visible_messages(self.session, snapshot)
        visible = visible_text_for_run_result(stable_result)
        commit_one_pair(self.session, visible_text, visible)
        status = str(getattr(stable_result, "status", "failed"))
        return AgentInteractionResult(
            status=status,
            answer=visible,
            resolution=resolution,
            run_result=stable_result,
            error=getattr(stable_result, "error", None),
            interaction_usage=self._usage(context),
        )

    def interact_locked(
        self,
        text: str,
        *,
        boundary: InteractionBoundary | str = InteractionBoundary.NATURAL,
        visible_user_text: str | None = None,
        task_payload: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> AgentInteractionResult:
        """Run one complete interaction; caller holds the application's lock."""
        try:
            selected_boundary = InteractionBoundary(boundary)
        except (TypeError, ValueError):
            return self._input_failure(InteractionBoundary.NATURAL, INTERACTION_INPUT_INVALID)
        visible = text if visible_user_text is None else visible_user_text
        try:
            validate_transcript_messages(self.session.messages)
        except Exception:
            return self._failure(
                status="failed",
                reason_code=INTERACTION_TRANSCRIPT_INVALID,
                error=public_explanation(INTERACTION_TRANSCRIPT_INVALID),
            )
        if selected_boundary is InteractionBoundary.NATURAL:
            natural_reason = self._natural_input_reason(text, visible_user_text)
            if natural_reason is not None:
                return self._input_failure(selected_boundary, natural_reason)
            visible = text
        visible_reason = self._visible_input_reason(visible)
        if visible_reason is not None:
            return self._input_failure(selected_boundary, visible_reason)
        subject = text if selected_boundary is InteractionBoundary.NATURAL else (task_payload if task_payload is not None else text)
        parsed_task: ParsedTaskRequest | None = None
        if selected_boundary is InteractionBoundary.TASK:
            try:
                parsed_task = parse_task_request(subject)
            except TaskDirectiveParseError as exc:
                reason = INTERACTION_INPUT_TOO_LARGE if exc.reason_code.endswith("TOO_LONG") else INTERACTION_INPUT_INVALID
                resolution = InteractionResolution(
                    action=InteractionAction.CLARIFY,
                    boundary=InteractionBoundary.TASK,
                    directive=None,
                    deliberation_profile=None,
                    provenance=InteractionProvenance.DETERMINISTIC,
                    ambiguity=InteractionAmbiguity.NONE,
                    subject=None,
                    reason_code=reason,
                )
                return self._commit_clarify(visible, resolution)
        snapshot = snapshot_visible_messages(self.session.messages)
        context: Any = None
        try:
            if parsed_task is not None and parsed_task.action is TaskRequestAction.CONTINUE:
                resolution = admit_interaction(
                    boundary=selected_boundary,
                    visible_user_text=visible,
                    subject=subject,
                    parsed_task=parsed_task,
                )
            elif parsed_task is not None and parsed_task.directive_explicit:
                resolution = admit_interaction(
                    boundary=selected_boundary,
                    visible_user_text=visible,
                    subject=parsed_task.subject or subject,
                    parsed_task=parsed_task,
                )
            else:
                resolver = InteractionResolver(
                    self.session,
                    active_setter=self._publish_active,
                    active_clearer=self._clear_active,
                )
                outcome = resolver.resolve(
                    boundary=selected_boundary,
                    subject=parsed_task.subject if parsed_task is not None and parsed_task.subject is not None else subject,
                    snapshot=snapshot,
                )
                context = outcome.context
                resolution = admit_interaction(
                    boundary=selected_boundary,
                    visible_user_text=visible,
                    subject=parsed_task.subject if parsed_task is not None and parsed_task.subject is not None else subject,
                    parsed_task=parsed_task,
                    model_decision=outcome.decision,
                )
        except ResolverUnavailable as exc:
            if str(exc) == INTERACTION_CANCELLED:
                return self._cancelled(context)
            return self._commit_unavailable(visible, INTERACTION_RESOLVER_UNAVAILABLE, context=context)
        except ResolverInvalid:
            return self._commit_unavailable(visible, INTERACTION_RESOLVER_INVALID, context=context)
        except (InteractionAdmissionError, ValueError) as exc:
            reason = getattr(exc, "reason_code", INTERACTION_REQUEST_CONTRACT_MISMATCH)
            if reason == INTERACTION_CANCELLED:
                return self._cancelled(context)
            if reason == INTERACTION_EVIDENCE_MISMATCH:
                return self._commit_unavailable(visible, reason, context=context)
            if reason == INTERACTION_REQUEST_CONTRACT_MISMATCH:
                return self._failure(
                    status="failed",
                    reason_code=reason,
                    error=public_explanation(reason),
                    context=context,
                )
            return self._failure(
                status="failed",
                reason_code=INTERACTION_INTERNAL_FAILED,
                error=public_explanation(INTERACTION_INTERNAL_FAILED),
                context=context,
            )
        except (ModelTimeoutError, ModelConnectionError, ModelProviderError, UnsupportedModelCapability, BudgetExhausted):
            return self._commit_unavailable(visible, INTERACTION_RESOLVER_UNAVAILABLE, context=context)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            logger.exception("Unexpected W12 interaction failure")
            return self._failure(
                status="failed",
                reason_code=INTERACTION_INTERNAL_FAILED,
                error=public_explanation(INTERACTION_INTERNAL_FAILED),
                context=context,
            )
        if resolution.action is InteractionAction.CLARIFY:
            return self._commit_clarify(visible, resolution, context=context)
        if resolution.action in {InteractionAction.RUN, InteractionAction.CONTINUE}:
            return self._dispatch_task(
                resolution,
                visible,
                stream_callback=stream_callback,
                context=context,
            )
        if resolution.action is InteractionAction.RESPOND:
            if context is None:
                context = build_interaction_context(self.session)
            try:
                plan = build_response_request_plan(
                    self.session,
                    context,
                    snapshot,
                    subject,
                    profile=resolution.deliberation_profile,
                    stream=stream_callback is not None,
                )
                response = self._response_call(context, plan.request, callback=stream_callback)
                if not isinstance(response, str) or not response.strip():
                    return self._failure(
                        status="failed",
                        reason_code=INTERACTION_RESPONSE_FAILED,
                        error=public_explanation(INTERACTION_RESPONSE_FAILED),
                        context=context,
                    )
                commit_one_pair(self.session, visible, response)
                return AgentInteractionResult(
                    status="succeeded",
                    answer=response,
                    resolution=resolution,
                    interaction_usage=self._usage(context),
                )
            except ResponseContextTooLarge:
                return self._failure(
                    status="unavailable",
                    reason_code=INTERACTION_RESPONSE_CONTEXT_TOO_LARGE,
                    error=public_explanation(INTERACTION_RESPONSE_CONTEXT_TOO_LARGE),
                    context=context,
                )
            except ResolverUnavailable as exc:
                if str(exc) == INTERACTION_CANCELLED:
                    return self._cancelled(context)
                return self._failure(
                    status="unavailable",
                    reason_code=INTERACTION_RESPONSE_UNAVAILABLE,
                    error=public_explanation(INTERACTION_RESPONSE_UNAVAILABLE),
                    context=context,
                )
            except (ModelTimeoutError, ModelConnectionError, ModelProviderError, UnsupportedModelCapability, BudgetExhausted):
                return self._failure(
                    status="unavailable",
                    reason_code=INTERACTION_RESPONSE_UNAVAILABLE,
                    error=public_explanation(INTERACTION_RESPONSE_UNAVAILABLE),
                    context=context,
                )
            except (ModelResponseError, ValueError):
                return self._failure(
                    status="failed",
                    reason_code=INTERACTION_RESPONSE_FAILED,
                    error=public_explanation(INTERACTION_RESPONSE_FAILED),
                    context=context,
                )
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except Exception:
                logger.exception("Unexpected W12 response failure")
                return self._failure(
                    status="failed",
                    reason_code=INTERACTION_INTERNAL_FAILED,
                    error=public_explanation(INTERACTION_INTERNAL_FAILED),
                    context=context,
                )
        return self._failure(
            status="failed",
            reason_code=INTERACTION_INTERNAL_FAILED,
            error=public_explanation(INTERACTION_INTERNAL_FAILED),
            context=context,
        )


__all__ = ["InteractionService"]
