"""Shared test-queue use cases for terminal, desktop and headless callers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from llm_agent.agent.observability.redaction import redact_text
from llm_agent.application import test_queue_runtime as runtime
from llm_agent.application.test_queue import Cancellation, ProgressObserver, TestQueue
from llm_agent.application.test_queue_inspection import case_details, export_report, inspect_queue

QueueAssessment = Literal["verified", "failed", "incomplete"]


def redact_queue_text(value: str) -> str:
    """Sanitize human queue output, including diagnostics captured by workers."""
    return cast(str, redact_text(value, limit=len(value)))


@dataclass(frozen=True)
class QueueExecutionRequest:
    config_path: Path
    profile: str | None = None
    live_authorized: bool = False
    automatic_approval: bool = False
    case_seconds: int = 300
    total_seconds: int = 3600
    skip_interrupted: bool = False
    expected_identity: str | None = None


@dataclass(frozen=True)
class QueueRunResult:
    document: dict[str, Any]
    assessment: QueueAssessment


def validate_execution_request(request: QueueExecutionRequest) -> None:
    if request.live_authorized is not True:
        raise PermissionError("Explicit live authorization is required before calling the configured model")
    for value in (request.automatic_approval, request.skip_interrupted):
        if type(value) is not bool:
            raise ValueError("Execution permissions must be booleans")
    if type(request.case_seconds) is not int or not 1 <= request.case_seconds <= 3600:
        raise ValueError("case_seconds must be 1..3600")
    if type(request.total_seconds) is not int or not 1 <= request.total_seconds <= 86400:
        raise ValueError("total_seconds must be 1..86400")


def assess_queue(document: dict[str, Any]) -> QueueAssessment:
    """Domain outcome, independent of exit codes or UI labels."""
    counts = document["counts"]
    if document["status"] != "completed" or counts.get("unverified") or counts.get("interrupted"):
        return "incomplete"
    return "verified" if counts.get("verified", 0) == len(document["cases"]) else "failed"


class TestQueueService:
    __test__ = False

    def __init__(self, root: Path) -> None:
        self._queue = TestQueue(root)

    @property
    def root(self) -> Path:
        return cast(Path, self._queue.root)

    def create(self, tasks: Path) -> dict[str, Any]:
        self._queue.create(tasks)
        return self.inspect()

    def inspect(self, *, include_details: bool = False) -> dict[str, Any]:
        return cast(dict[str, Any], inspect_queue(self._queue, include_details=include_details))

    def status(self) -> dict[str, Any]:
        """Return a polling summary; execution and explicit inspection validate the manifest."""
        return cast(dict[str, Any], inspect_queue(self._queue, validate_manifest=False))

    def details(self, case_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], case_details(self._queue, case_id))

    def prioritize(self, case_id: str) -> dict[str, Any]:
        self._queue.prioritize(case_id)
        return self.inspect()

    def control(self, action: str) -> dict[str, Any]:
        self._queue.control(action)
        return {"requested": action, "queue": str(self.root)}

    def check(self, *, config_path: Path | None = None, profile: str | None = None,
              automatic_approval: bool = False, case_seconds: int = 300) -> dict[str, Any]:
        if profile is not None and config_path is None:
            raise ValueError("--profile requires --config")
        document = self.inspect()
        if config_path is not None:
            document["configuration"] = runtime.configuration_preview(config_path, profile,
                automatic_approval=automatic_approval, case_seconds=case_seconds,
                previous_identity=document["identity"])
        return document

    def export(self, content: str, output: Path | None = None) -> Path:
        return cast(Path, export_report(self._queue, content, output))

    def start_detached(self, request: QueueExecutionRequest) -> dict[str, Any]:
        from llm_agent.application.test_queue_worker import start_queue_worker

        return cast(dict[str, Any], start_queue_worker(self, request))

    def run(self, request: QueueExecutionRequest, *, on_progress: ProgressObserver | None = None,
            cancellation: Cancellation | None = None, pause_requested: Cancellation | None = None) -> QueueRunResult:
        # Every caller, including future desktop adapters, crosses this authority gate.
        validate_execution_request(request)
        config = runtime.prepare_config(request.config_path, request.profile)
        identity = runtime.runtime_identity(config, automatic_approval=request.automatic_approval,
                                    case_seconds=request.case_seconds)
        if request.expected_identity is not None and request.expected_identity != identity:
            raise ValueError("A configuracao ou o codigo mudou apos a conferencia. Confira a preparacao novamente.")
        executor = runtime.RuntimeExecutor(
            config,
            automatic_approval=request.automatic_approval,
            expected_identity=identity,
            case_seconds=request.case_seconds,
        )
        self._queue.run(executor, identity=identity,
                        case_seconds=request.case_seconds, total_seconds=request.total_seconds,
                        skip_interrupted=request.skip_interrupted, on_progress=on_progress, cancellation=cancellation,
                        pause_requested=pause_requested, include_inline_details=False,
                        execution_options={"profile": request.profile, "automatic_approval": request.automatic_approval,
                                           "case_seconds": request.case_seconds, "total_seconds": request.total_seconds})
        document = self.inspect()
        return QueueRunResult(document, assess_queue(document))
