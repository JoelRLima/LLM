"""Bounded serial execution and recovery for durable test queues."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from llm_agent.application.test_queue_contracts import (
    Cancellation,
    Executor,
    ProgressObserver,
    QueueProgress,
    Verifier,
    notify,
)
from llm_agent.application.test_queue_evidence import read_case_evidence
from llm_agent.application.test_queue_manifest import TestCase, local_path
from llm_agent.application.test_queue_verification import verdict, verify
from llm_agent.storage.json_persistence import write_json_atomic


def _begin_run(
    queue: Any,
    identity: str,
    skip_interrupted: bool,
    execution_options: dict[str, str | int | bool | None] | None,
) -> tuple[dict[str, Any], dict[str, TestCase], str]:
    state = queue.snapshot()
    cases = {case.case_id: case for case in queue._cases_for_state(state)}
    if state["identity"] is not None and state["identity"] != identity:
        raise ValueError("Runtime/configuration changed; create a new queue for this experiment")
    statuses = {"pending", "running", "interrupted", "verified", "unverified", "failed", "timeout", "error"}
    if any(row.get("status") not in statuses for row in state["results"].values()):
        raise ValueError("Invalid queue result status")
    for row in state["results"].values():
        if row["status"] == "running":
            row["status"] = "interrupted"
    state["status"] = "paused"
    queue.save(state)
    if any(row["status"] == "interrupted" for row in state["results"].values()) and not skip_interrupted:
        raise ValueError("Interrupted task requires review; use --skip-interrupted to keep its evidence and continue")
    generation = uuid.uuid4().hex
    state.update(identity=identity, generation=generation, status="running")
    if execution_options is not None:
        state["execution_options"] = dict(execution_options)
    queue.save(state)
    return state, cases, generation


def _stop_status(
    queue: Any,
    cancellation: Cancellation,
    pause_requested: Cancellation | None,
    generation: str,
) -> str | None:
    if cancellation.is_set():
        return "stopped"
    if queue.requested("pause", generation) or (pause_requested is not None and pause_requested.is_set()):
        return "paused"
    return None


def _write_case_workspace(queue: Any, case: TestCase, case_id: str) -> Path:
    workspace = local_path(queue.root, f"cases/{case_id}/workspace")
    workspace.mkdir(parents=True, exist_ok=False)
    for name, text in case.files:
        path = local_path(workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return workspace


def _execute_case(
    queue: Any,
    state: dict[str, Any],
    case: TestCase,
    case_id: str,
    cancellation: Cancellation,
    execute: Executor,
    verifier: Verifier,
    case_seconds: int,
    remaining: int,
) -> str:
    started = time.monotonic()
    try:
        workspace = _write_case_workspace(queue, case, case_id)
        execution = execute(case, workspace, cancellation, min(case_seconds, remaining))
        verification = verifier(
            workspace,
            case.checks,
            cancellation=cancellation,
            timeout_seconds=min(30, remaining - (time.monotonic() - started)),
        )
        if cancellation.is_set():
            execution = {**execution, "status": "interrupted", "success": False}
        result = {
            "status": verdict(execution, verification),
            "execution": execution,
            "verification": verification,
            "workspace": str(workspace),
        }
        evidence_path = workspace.parent / "result.json"
        write_json_atomic(evidence_path, result)
        state["results"][case_id] = {
            "status": result["status"],
            "workspace": str(workspace),
            "evidence_path": str(evidence_path),
            "execution": {"status": execution.get("status"), "success": execution.get("success")},
            "verification": {"status": verification["status"], "checks_total": len(verification["checks"])},
        }
    except Exception as exc:
        state["results"][case_id] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:1000]}
    except BaseException:
        state["results"][case_id] = {"status": "interrupted"}
        state["status"] = "stopped"
        raise
    finally:
        state["elapsed_seconds"] += time.monotonic() - started
        queue.save(state)
    return str(state["results"][case_id]["status"])


def _execute_pending_cases(
    queue: Any,
    state: dict[str, Any],
    cases: dict[str, TestCase],
    execute: Executor,
    verifier: Verifier,
    generation: str,
    case_seconds: int,
    total_seconds: int,
    cancellation: Cancellation,
    pause_requested: Cancellation | None,
    on_progress: ProgressObserver | None,
) -> None:
    for position, case_id in enumerate(state["order"], 1):
        if state["results"][case_id]["status"] != "pending":
            continue
        stop_status = _stop_status(queue, cancellation, pause_requested, generation)
        if stop_status is not None:
            state["status"] = stop_status
            break
        remaining = int(total_seconds - state["elapsed_seconds"])
        if remaining < 1:
            state["status"] = "budget_exhausted"
            break
        case = cases[case_id]
        state["results"][case_id] = {"status": "running"}
        queue.save(state)  # Durable claim before the first effect.
        notify(on_progress, QueueProgress(case_id, "running", position, len(cases), state["elapsed_seconds"]))
        result_status = _execute_case(
            queue,
            state,
            case,
            case_id,
            cancellation,
            execute,
            verifier,
            case_seconds,
            remaining,
        )
        notify(on_progress, QueueProgress(case_id, result_status, position, len(cases), state["elapsed_seconds"]))
        if result_status in {"interrupted", "error"}:
            state["status"] = "stopped" if result_status == "interrupted" else "paused"
            break
    else:
        state["status"] = "completed"


def _inline_evidence(queue: Any, state: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "results": {
            case_id: read_case_evidence(queue.root, case_id, row, inline_limit_bytes=64 * 1024)
            for case_id, row in state["results"].items()
        },
    }


def run_queue(
    queue: Any,
    execute: Executor,
    *,
    identity: str,
    case_seconds: int = 300,
    total_seconds: int = 3600,
    skip_interrupted: bool = False,
    verifier: Verifier = verify,
    on_progress: ProgressObserver | None = None,
    cancellation: Cancellation | None = None,
    pause_requested: Cancellation | None = None,
    include_inline_details: bool = True,
    execution_options: dict[str, str | int | bool | None] | None = None,
) -> dict[str, Any]:
    if type(case_seconds) is not int or not 1 <= case_seconds <= 3600:
        raise ValueError("case_seconds must be 1..3600")
    if type(total_seconds) is not int or not 1 <= total_seconds <= 86400:
        raise ValueError("total_seconds must be 1..86400")
    with queue.lease():
        state, cases, generation = _begin_run(queue, identity, skip_interrupted, execution_options)
        run_cancellation = _QueueCancellation(queue, generation, cancellation)
        try:
            _execute_pending_cases(
                queue,
                state,
                cases,
                execute,
                verifier,
                generation,
                case_seconds,
                total_seconds,
                run_cancellation,
                pause_requested,
                on_progress,
            )
        finally:
            if state["status"] == "running":
                state["status"] = "stopped"
            queue.save(state)
        return _inline_evidence(queue, state) if include_inline_details else state


class _QueueCancellation:
    def __init__(self, queue: Any, generation: str, external: Cancellation | None = None) -> None:
        self.queue, self.generation = queue, generation
        self.external = external
        self._next_file_check = 0.0
        self._stop_latched = False
        self._lock = Lock()

    def is_set(self) -> bool:
        if self.external is not None and self.external.is_set():
            return True
        if self.queue._local_stop_generation == self.generation:
            with self._lock:
                self._stop_latched = True
            return True
        now = time.monotonic()
        with self._lock:
            if self._stop_latched:
                return True
            if now < self._next_file_check:
                return False
            self._next_file_check = now + 0.25
        if self.queue.requested("stop", self.generation):
            with self._lock:
                self._stop_latched = True
            return True
        return False

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            now = time.monotonic()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                return False
            with self._lock:
                until_file_check = max(0.0, self._next_file_check - now)
            delay = until_file_check if remaining is None else min(until_file_check, remaining)
            if delay <= 0:
                continue
            wait = getattr(self.external, "wait", None)
            if callable(wait):
                wait(delay)
            else:
                time.sleep(delay)
        return True
