import json
from unittest.mock import MagicMock

import pytest

from llm_agent.agent.llm.contracts import StreamEvent, StreamEventType
from llm_agent.agent.llm.errors import ModelConnectionError, ModelResponseError, ModelTimeoutError
from llm_agent.agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from llm_agent.agent.runtime.model_call import ModelCallService
from llm_agent.agent.runtime.model_call_stream import consume_events
from tests.unit.runtime.test_model_call_service import _context, _Gateway, _request


def _response(*frames):
    response = MagicMock()
    response.iter_lines.return_value = [
        b"data: [DONE]" if frame == "done" else ("data: " + json.dumps(frame)).encode()
        for frame in frames
    ]
    return response


CONTENT = {"choices": [{"delta": {"content": "partial"}}]}
USAGE = {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}}


@pytest.mark.parametrize("frames", [(), (CONTENT,), ({"choices": []},)])
def test_eof_without_provider_terminal_marker_is_not_normal_completion(frames):
    response = _response(*frames)
    events = list(OpenAICompatibleGateway.iter_stream(response))
    assert events[-1].type is StreamEventType.ERROR
    assert all(event.type is not StreamEventType.DONE for event in events)
    response.close.assert_called_once()


@pytest.mark.parametrize("reason", ["length", "content_filter"])
@pytest.mark.parametrize("with_done", [False, True])
def test_truncated_or_filtered_stream_preserves_partial_output_and_usage(reason, with_done):
    frames = [CONTENT, {"choices": [{"delta": {}, "finish_reason": reason}]}, USAGE]
    if with_done:
        frames.append("done")
    response = _response(*frames)
    done_callbacks = []
    with pytest.raises(ModelResponseError) as caught:
        consume_events(OpenAICompatibleGateway.iter_stream(response), {"on_done": done_callbacks.append})
    assert caught.value.partial_content == "partial"
    assert caught.value.stream_usage == USAGE["usage"]
    assert done_callbacks == []
    response.close.assert_called_once()


@pytest.mark.parametrize("frames", [
    (CONTENT, "done"),
    (CONTENT, {"choices": [{"delta": {}, "finish_reason": "stop"}]}, USAGE),
    (CONTENT, {"choices": [{"delta": {}, "finish_reason": "stop"}]}, USAGE, "done"),
])
def test_explicit_terminal_marker_keeps_legacy_done_and_finish_reason_only_servers(frames):
    response = _response(*frames)
    events = list(OpenAICompatibleGateway.iter_stream(response))
    assert events[-1].type is StreamEventType.DONE
    assert all(event.type is not StreamEventType.ERROR for event in events)
    response.close.assert_called_once()


@pytest.mark.parametrize("reason", [[], 42, ""])
def test_malformed_finish_reason_cannot_complete_a_stream(reason):
    response = _response(CONTENT, {"choices": [{"delta": {}, "finish_reason": reason}]}, "done")
    with pytest.raises(ModelResponseError) as caught:
        consume_events(OpenAICompatibleGateway.iter_stream(response), {})
    assert caught.value.partial_content == "partial"
    response.close.assert_called_once()


@pytest.mark.parametrize("error_type", [ModelResponseError, ModelConnectionError, ModelTimeoutError])
def test_iterator_failure_keeps_received_usage_and_partial_text_in_canonical_ledger(error_type):
    class BrokenGateway(_Gateway):
        def stream(self, request):
            yield StreamEvent(StreamEventType.CONTENT, text="partial")
            yield StreamEvent(StreamEventType.USAGE, data=USAGE["usage"])
            raise error_type("stream failed")

    context, sink = _context(BrokenGateway())
    with pytest.raises(error_type) as caught:
        ModelCallService.for_context(context).stream(_request(stream=True), {})
    assert caught.value.partial_content == "partial"
    assert caught.value.stream_usage == USAGE["usage"]
    assert len(sink.entries) == 1
    assert sink.entries[0]["success"] is False
    assert sink.entries[0]["total_tokens"] == 7
    assert context.budget_snapshot().model_calls == 1
    assert context.budget_snapshot().accounted_tokens == 7
    assert context.budget_snapshot().reserved_tokens == 0


def test_protocol_failure_after_usage_is_charged_once_without_returning_success():
    class ProtocolGateway(_Gateway):
        def stream(self, request):
            response = _response(CONTENT, USAGE)
            yield from OpenAICompatibleGateway.iter_stream(response)

    context, sink = _context(ProtocolGateway())
    with pytest.raises(ModelResponseError):
        ModelCallService.for_context(context).stream(_request(stream=True), {})
    assert sink.entries[0]["success"] is False
    assert context.budget_snapshot().accounted_tokens == 7
    assert context.budget_snapshot().model_calls == 1
