# ruff: noqa: E402
"""WebView2-only offline protocol fixture; no model, runtime, network or temp ACLs.

Settlements are simulated at the session boundary, not application validation.
"""

from __future__ import annotations

import hashlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from llm_agent.application.workbench.contracts import PanelContent, PanelReference


class OfflineSession:
    def __init__(self, *, chunks: int = 150, interval: float = 0.2):
        self.chunks = chunks
        self.interval = interval
        self._cancel = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="offline-webview")
        self._counter = 0
        self._contents = {}
        self._busy = None

    def submit(self, text, *, stream_callback=None):
        if self._busy is not None and not self._busy.done():
            raise RuntimeError("fixture busy")
        self._cancel.clear()
        self._counter += 1
        run = f"offline-{self._counter}"

        def execute():
            for index in range(1 if "/fast" in text else self.chunks):
                if stream_callback:
                    stream_callback(f"[{run}] chunk {index + 1}\n")
                if self._cancel.wait(0 if "/fast" in text else self.interval):
                    break
            cancelled = self._cancel.is_set()
            answer = f"FIXTURE OFFLINE {run}: " + ("cancelada" if cancelled else "concluída")
            reference = PanelReference(
                "offline-workspace",
                run,
                f"report-{run}",
                f"Relatório simulado {run}",
                "Fixture offline WebView2",
                "report",
                "final",
                run,
            )
            truncated = "/truncate" in text
            content = answer + "\n" + text
            digest = None if truncated else hashlib.sha256(content.encode()).hexdigest()
            self._contents[run] = (PanelContent(reference, content, "text", digest, truncated), text)
            return {
                "status": "failed" if cancelled else "succeeded",
                "reason_code": "INTERACTION_CANCELLED" if cancelled else None,
                "answer": answer,
                "run_id": run,
            }, reference

        self._busy = self._executor.submit(execute)
        return self._busy

    def cancel(self):
        self._cancel.set()

    def read(self, reference):
        content, command = self._contents[reference.locator]

        def load():
            if "/slow-read" in command:
                threading.Event().wait(2)
            if "/read-error" in command:
                raise OSError("Falha de leitura simulada")
            return content

        return self._executor.submit(load)

    def close(self):
        self.cancel()
        result = self._executor.submit(lambda: None)
        self._executor.shutdown(wait=False)
        return result


def create_offline_session():
    # No filesystem writes needed, including tempfile child directories.
    return OfflineSession(), ROOT, ROOT
