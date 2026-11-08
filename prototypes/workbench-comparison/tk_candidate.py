# ruff: noqa: E402
"""Run the existing Tk Workbench with the shared deterministic Agent fixture."""

from __future__ import annotations

import time as _time

PROCESS_STARTED = _time.perf_counter()

import json
import os
import platform
import sys
import tkinter as tk
from pathlib import Path
from time import perf_counter

from fixture import OBJECTIVE, create_session

from llm_agent.interfaces.workbench.window import WorkbenchWindow

EVIDENCE_DIR = Path(os.environ.get(
    "WORKBENCH_EVIDENCE_DIR",
    Path(__file__).with_name("evidence") / "com-investigation-20261021" / "runs",
))
METRICS = EVIDENCE_DIR / "tk-startup.json"


def main() -> None:
    session, _, _ = create_session()
    root = tk.Tk()
    window = WorkbenchWindow(root, session)
    if "--demo" in sys.argv:
        settled = window.settled
        def capture(result):
            settled(result)
            observation, reference = result
            EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            (EVIDENCE_DIR / "tk-demo-result.json").write_text(json.dumps({
                "elapsed_to_report_ms": round((_time.perf_counter() - PROCESS_STARTED) * 1000, 1),
                "status": observation.get("status"),
                "run_id": observation.get("run_id"),
                "completeness": reference.completeness if reference else "unknown",
            }), encoding="utf-8")
        window.settled = capture
        root.after(500, lambda: (window.prompt.insert("1.0", OBJECTIVE), window.submit()))
    def ready() -> None:
        root.update_idletasks()
        METRICS.write_text(json.dumps({
            "time_to_idle_ui_ms": round((perf_counter() - PROCESS_STARTED) * 1000, 1),
            "python": platform.python_version(),
            "pid": __import__("os").getpid(),
        }), encoding="utf-8")
    root.after_idle(ready)
    root.mainloop()


if __name__ == "__main__":
    main()
