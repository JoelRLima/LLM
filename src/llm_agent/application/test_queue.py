"""Durable serial test scheduling through an injected executor, without UI dependencies."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, cast

from llm_agent.application.test_queue_contracts import (
    Cancellation as Cancellation,
)
from llm_agent.application.test_queue_contracts import (
    Executor as Executor,
)
from llm_agent.application.test_queue_contracts import (
    ProgressObserver as ProgressObserver,
)
from llm_agent.application.test_queue_contracts import (
    QueueProgress as QueueProgress,
)
from llm_agent.application.test_queue_contracts import (
    Verifier as Verifier,
)
from llm_agent.application.test_queue_manifest import (
    TestCase,
    digest,
    discover_cases,
    parse_case,
    read_document,
    serialized_size,
)
from llm_agent.application.test_queue_scheduler import (
    _QueueCancellation as _QueueCancellation,
)
from llm_agent.application.test_queue_scheduler import (
    run_queue as _run_queue,
)
from llm_agent.application.test_queue_verification import verify
from llm_agent.filesystem.lock_filesystem import open_verified
from llm_agent.filesystem.path_safety import assert_no_link_ancestors
from llm_agent.process.file_lock import try_lock_descriptor, unlock_descriptor
from llm_agent.storage.json_persistence import write_json_atomic


class TestQueue:
    __test__ = False

    def __init__(self, root: Path) -> None:
        assert_no_link_ancestors(root)
        self.root = root.resolve()
        self._status_cases_cache: tuple[TestCase, ...] | None = None
        self._status_manifest_signature: tuple[int, int, int, int] | None = None
        self._status_cache_lock = Lock()
        self._local_stop_generation: str | None = None

    @contextmanager
    def lease(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = open_verified(self.root / "runner.lock", os.O_CREAT | os.O_RDWR)
        acquired = False
        try:
            acquired = try_lock_descriptor(descriptor)
            if not acquired:
                raise RuntimeError("A runner already owns this test queue")
            yield
        finally:
            if acquired:
                unlock_descriptor(descriptor)
            os.close(descriptor)

    def create(self, directory: Path) -> dict[str, Any]:
        cases = discover_cases(directory)
        if self.root.is_relative_to(directory.resolve()) or directory.resolve().is_relative_to(self.root):
            raise ValueError("Queue and task directory must be separate")
        with self.lease():
            if (self.root / "state.json").exists():
                raise ValueError("Queue already exists; choose another output directory")
            manifest = {"version": 1, "cases": [case.document() for case in cases]}
            serialized_size(manifest, max_bytes=32 * 1024 * 1024)
            write_json_atomic(self.root / "manifest.json", manifest)
            state = {"version": 1, "manifest_hash": digest(manifest), "identity": None,
                     "not_imported": sorted(folder.name for folder in directory.iterdir()
                                            if folder.is_dir() and folder.name not in {case.case_id for case in cases}),
                     "case_summaries": {
                         case.case_id: {
                             "initial_files": len(case.files),
                             "checks": len(case.checks),
                             "python_verifiers": sum(check["kind"] == "python" for check in case.checks),
                         }
                         for case in cases
                     },
                     "status": "ready", "generation": None, "elapsed_seconds": 0.0,
                     "order": [case.case_id for case in cases],
                     "results": {case.case_id: {"status": "pending"} for case in cases}}
            self.save(state)
            return state

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], read_document(self.root / "state.json", max_bytes=64 * 1024 * 1024))

    def cases(self) -> tuple[TestCase, ...]:
        """Read validated frozen inputs without starting a worker or model."""
        return self._cases_for_state(self.snapshot())

    def _cases_for_state(self, state: dict[str, Any]) -> tuple[TestCase, ...]:
        """Validate the manifest against an already-read state snapshot."""
        manifest = read_document(self.root / "manifest.json", max_bytes=64 * 1024 * 1024)
        if manifest.get("version") != 1 or state.get("version") != 1 or digest(manifest) != state["manifest_hash"]:
            raise ValueError("Changed or unsupported queue manifest")
        cases = tuple(map(parse_case, manifest["cases"]))
        ids = {case.case_id for case in cases}
        if len(ids) != len(cases) or len(ids) != len(state["order"]) or ids != set(state["order"]) or ids != set(state["results"]):
            raise ValueError("Invalid queue case identities")
        return cases

    def status_cases(self, state: dict[str, Any]) -> tuple[tuple[TestCase, ...], bool]:
        """Use an in-memory status projection; execution still validates current bytes."""
        with self._status_cache_lock:
            manifest_path = self.root / "manifest.json"
            assert_no_link_ancestors(manifest_path)
            metadata = manifest_path.stat()
            signature = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            if signature == self._status_manifest_signature and self._status_cases_cache is not None:
                return self._status_cases_cache, False
            cases = self._cases_for_state(state)
            self._status_manifest_signature = signature
            self._status_cases_cache = cases
            return cases, True

    def status(self) -> dict[str, Any]:
        """Report stale durable claims after a crash without replaying effects."""
        descriptor = open_verified(self.root / "runner.lock", os.O_RDWR)
        acquired = False
        try:
            acquired = try_lock_descriptor(descriptor)
            state = self.snapshot()
            state["runner_active"] = not acquired
            if acquired and state["status"] == "running":
                state["status"] = "interrupted"
                for row in state["results"].values():
                    if row["status"] == "running":
                        row["status"] = "interrupted"
            return state
        finally:
            if acquired:
                unlock_descriptor(descriptor)
            os.close(descriptor)

    def save(self, state: dict[str, Any]) -> None:
        write_json_atomic(self.root / "state.json", state, compact=True)

    def prioritize(self, case_id: str) -> None:
        with self.lease():
            state = self.snapshot()
            if state["results"].get(case_id, {}).get("status") != "pending":
                raise ValueError("Only a pending task can be prioritized")
            state["order"] = [case_id, *(item for item in state["order"] if item != case_id)]
            self.save(state)

    def control(self, action: str) -> None:
        if action not in {"pause", "stop"}:
            raise ValueError("Expected pause or stop")
        state = self.snapshot()
        if state["status"] != "running":
            raise ValueError("Queue is not running")
        # Separate files make stop monotonic: a later pause cannot undo it.
        write_json_atomic(self.root / f"{action}.json", {"generation": state["generation"]})
        if action == "stop":
            self._local_stop_generation = state["generation"]

    def run(
        self,
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
        return _run_queue(
            self,
            execute,
            identity=identity,
            case_seconds=case_seconds,
            total_seconds=total_seconds,
            skip_interrupted=skip_interrupted,
            verifier=verifier,
            on_progress=on_progress,
            cancellation=cancellation,
            pause_requested=pause_requested,
            include_inline_details=include_inline_details,
            execution_options=execution_options,
        )

    def requested(self, action: str, generation: str) -> bool:
        path = self.root / f"{action}.json"
        if not path.exists():
            return False
        return bool(read_document(path).get("generation") == generation)



