from time import monotonic
from types import SimpleNamespace

from llm_agent.interfaces.workbench.webview import WorkbenchBridge


def test_long_poll_drains_worker_channel_without_waiting_one_second():
    calls = []
    bridge = None

    def pump():
        calls.append(True)
        if len(calls) == 2:
            bridge._append_delta("request", "token")

    bridge = WorkbenchBridge(session=SimpleNamespace(pump_stream=pump))
    bridge._active_request_id = "request"
    bridge._stream_open = True
    started = monotonic()
    result = bridge.wait_events(1000)
    assert result[0]["text"] == "token"
    assert monotonic() - started < 0.8
    assert len(calls) == 2


def test_zero_timeout_still_pumps_once_and_closed_bridge_does_not_wait():
    calls = []
    bridge = WorkbenchBridge(session=SimpleNamespace(pump_stream=lambda: calls.append(True)))
    assert bridge.wait_events(0) == []
    assert calls == [True]
    bridge._closed = True
    started = monotonic()
    assert bridge.wait_events(1000) == []
    assert monotonic() - started < 0.3
