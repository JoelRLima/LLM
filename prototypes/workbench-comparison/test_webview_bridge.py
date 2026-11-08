# ruff: noqa: E402
"""Deterministic WebView2 bridge tests; run directly with the repo Python."""

import sys
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from webview_offline_fixture import OfflineSession

from llm_agent.application.workbench.contracts import PanelContent, PanelReference
from llm_agent.interfaces.workbench import webview as candidate


class ControlledSession:
    def __init__(self):
        self.future = Future()
        self.cancels = 0

    def submit(self, text, *, stream_callback):
        self.callback = stream_callback
        self.future = Future()
        return self.future

    def cancel(self):
        self.cancels += 1

    def read(self, ref, offset=0):
        assert offset == 0
        future = Future()
        future.set_result(self.content)
        return future


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.session = ControlledSession()
        with patch.object(candidate, "create_session", return_value=(self.session, HERE)):
            self.bridge = candidate.WorkbenchBridge()

    def settle(self, *, reference=None, error=None):
        if error:
            self.session.future.set_exception(error)
        else:
            self.session.future.set_result(({"status": "failed", "reason_code": "INTERACTION_CANCELLED"}, reference))
        with self.bridge._condition:
            self.assertTrue(self.bridge._condition.wait_for(lambda: self.bridge._active_request_id is None, 2))

    def test_two_cancellations_keep_canonical_settlement(self):
        for _ in range(2):
            request = self.bridge.submit("cancel me")["request_id"]
            self.assertTrue(self.bridge.cancel(request)["accepted"])
            self.bridge.cancel(request)  # idempotent per request
            self.settle()
            events = self.bridge.wait_events(0)
            self.assertEqual([e["type"] for e in events], ["cancel_requested", "settled"])
            self.assertEqual(events[-1]["observation"], {"status": "failed", "reason_code": "INTERACTION_CANCELLED"})
        self.assertEqual(self.session.cancels, 2)

    def test_slow_consumer_bounded_and_essential_order(self):
        reference = PanelReference("w", "r", "a", "title", "fixture", "report", "final", "loc")
        self.bridge._references = candidate.OrderedDict(
            (str(i), reference) for i in range(candidate.MAX_REPORT_REFERENCES)
        )
        request = self.bridge.submit("stream")["request_id"]
        for _ in range(1000):
            self.session.callback("x" * 1000)
        self.bridge.cancel(request)
        for _ in range(1000):
            self.session.callback("y" * 1000)
        self.settle(reference=reference)
        for _ in range(1000):
            self.assertFalse(self.bridge.submit("paused UI")["accepted"])
        self.session.callback("late")
        events = self.bridge.wait_events(0)
        self.assertLessEqual(len(events), candidate.MAX_PENDING_EVENTS)
        self.assertEqual(
            [e["type"] for e in events], ["delta", "cancel_requested", "delta", "artifact_expired", "settled"]
        )
        self.assertTrue(all(e["request_id"] == request for e in events if "request_id" in e))
        for e in events:
            if e["type"] == "delta":
                self.assertEqual(len(e["text"]), candidate.MAX_EVENT_TEXT)
                self.assertTrue(e["truncated"])
        self.assertTrue(self.bridge.submit("resumed")["accepted"])
        self.settle()

    def test_error_is_retained_after_delta_and_cancel(self):
        request = self.bridge.submit("error")["request_id"]
        self.session.callback("preview")
        self.bridge.cancel(request)
        with self.assertLogs(candidate.logger, level="ERROR"):
            self.settle(error=RuntimeError("expected test error"))
        events = self.bridge.wait_events(0)
        self.assertEqual([e["type"] for e in events], ["delta", "cancel_requested", "error"])
        self.assertEqual(events[-1]["request_id"], request)

    def test_panel_content_metadata_transported_without_integrity_claim(self):
        ref = PanelReference("w", "r", "a", "title", "source", "report", "final", "loc")
        self.bridge._references["token"] = ref
        for digest, truncated, offset in [(None, True, 42), ("abc", False, None)]:
            self.session.content = PanelContent(ref, "preview", "text", digest, truncated, offset)
            data = self.bridge.read_report("token")
            self.assertEqual(data["reference"]["artifact_id"], "a")
            self.assertEqual(data["token"], "token")
            self.assertEqual((data["sha256"], data["truncated"], data["next_offset"]), (digest, truncated, offset))
            self.assertNotIn("verified", data)
        with self.assertRaises(ValueError):
            self.bridge.read_report("absent")

    def test_offline_fixture_stream_and_repeated_cancel(self):
        import threading

        session = OfflineSession(chunks=10000, interval=1)
        try:
            for index in range(2):
                chunk = threading.Event()
                future = session.submit("slow", stream_callback=lambda text, ready=chunk: ready.set())
                self.assertTrue(chunk.wait(2))
                self.assertFalse(future.done())
                session.cancel()
                observation, ref = future.result(2)
                self.assertEqual(observation["status"], "failed")
                self.assertEqual(observation["reason_code"], "INTERACTION_CANCELLED")
                self.assertEqual(ref.run_id, f"offline-{index + 1}")
        finally:
            session.close().result(2)

    def test_close_cancels_and_waits_for_active_execution(self):
        session = OfflineSession(chunks=10_000, interval=30)
        bridge = candidate.WorkbenchBridge(session=session, workspace=HERE)
        request = bridge.submit("keep working")
        self.assertTrue(request["accepted"])
        events = bridge.wait_events(1000)
        self.assertTrue(any(event["type"] == "delta" for event in events))

        self.assertEqual(bridge.close(), {"closed": True})
        self.assertTrue(session._busy.done())
        self.assertEqual(bridge.close(), {"closed": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
