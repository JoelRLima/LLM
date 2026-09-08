"""Production-route doubles and shared report helpers for LONG_HORIZON_V1."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.capabilities import ALL_CAPABILITIES
from agent.llm.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    TokenUsage,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.planning.plan_model import Plan
from agent.planning.plan_step_types import ToolPlanStep
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.convergence_runtime import current_progress_receipt
from agent.runtime.paths import AppPaths
from agent.runtime.request_measurement import RequestInputMeasurement
from agent.tools.contracts import ToolResult, ToolStatus


def result(
    scenario_id: str,
    title: str,
    assertions: Mapping[str, bool],
    *,
    logical_units: int = 0,
    model_calls: int = 0,
    tool_calls: int = 0,
    context_pressure_decisions: Sequence[str] = (),
    compact_full_events: Sequence[str] = (),
    progress_advances: int = 0,
    no_progress_cycles: int = 0,
    cache_reuse: int = 0,
    rehydration: int = 0,
    plan_extensions: int = 0,
    terminal_reason: str | None = None,
    changed_files: Sequence[str] = (),
) -> dict[str, Any]:
    failures = [name for name, value in assertions.items() if not value]
    return {
        "scenario_id": scenario_id,
        "title": title,
        "passed": not failures,
        "unknown_failures": [],
        "failures": failures,
        "logical_units": logical_units,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "context_pressure_decisions": list(context_pressure_decisions),
        "compact_full_events": list(compact_full_events),
        "progress_advances": progress_advances,
        "no_progress_cycles": no_progress_cycles,
        "cache_reuse": cache_reuse,
        "rehydration": rehydration,
        "replans": plan_extensions,
        "terminal_reason": terminal_reason,
        "changed_files": list(changed_files),
        "candidate_workspace_integrity": not changed_files,
        "assertions": dict(assertions),
    }


class RecordingEventSink:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def emit(self, event: Any) -> None:
        self.records.append(event)

    @property
    def events(self) -> tuple[Any, ...]:
        return tuple(self.records)


class RecordingMetricsSink:
    def __init__(self) -> None:
        self.metrics: list[dict[str, Any]] = []

    def record(self, metric: dict[str, Any]) -> None:
        self.metrics.append(dict(metric))


class ProductionGateway:
    """Offline provider double that still crosses the canonical model boundary."""

    provider_name = "long-horizon-production-double"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output_modes=(),
        reasoning=False,
        token_counting=False,
        tool_calls=False,
    )

    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.response, usage=TokenUsage(available=False))

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        del request
        return iter(())

    def measure_request_input_tokens(
        self,
        request: ModelRequest,
    ) -> RequestInputMeasurement | None:
        del request
        return None

    def count_tokens(self, text: str) -> int | None:
        del text
        return None


def event_type(event: Any) -> str | None:
    if isinstance(event, Mapping):
        raw = event.get("type")
        return str(raw) if raw is not None else None
    kind = getattr(event, "kind", None)
    value = getattr(kind, "value", kind)
    return str(value) if value is not None else None


def event_types(events: Sequence[Any]) -> tuple[str, ...]:
    return tuple(value for value in (event_type(item) for item in events) if value)


def task_metrics(orchestrator: Any) -> tuple[dict[str, Any], ...]:
    reader = getattr(orchestrator, "_get_metrics_for_task", None)
    if not callable(reader):
        return ()
    return tuple(dict(item) for item in reader() if isinstance(item, Mapping))


def real_plan_run(*, file_count: int, seed_lossy_rehydration: bool, replay_completed: bool = False, no_summary_control: bool = False) -> dict[str, Any]:
    """Run the real application/PlanExecutor route in an isolated workspace."""

    with tempfile.TemporaryDirectory(prefix="lh-long-horizon-") as raw_root:
        root = Path(raw_root)
        workspace = root / "workspace"
        workspace.mkdir()
        hashes: dict[str, str] = {}
        for index in range(file_count):
            name = f"unit-{index:03d}.txt"
            content = f"long-horizon-unit-{index}\n"
            (workspace / name).write_text(content, encoding="utf-8")
            hashes[name] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        gateway = ProductionGateway()
        paths = AppPaths.discover(root / "app-home", env={})
        ConfigRepository(paths).initialize()
        application = AgentApplication.create(
            workspace=workspace,
            paths=paths,
            gateway=gateway,
            approval_policy=AutoApprove(),
            configure_logging=False,
            overrides={
                "max_task_steps": max(file_count + 4, 12),
                "max_task_tool_calls": max(file_count + 4, 12),
                "max_model_calls": max(file_count + 4, 12),
            },
        )
        try:
            orchestrator = application.orchestrator
            orchestrator.allowed_capabilities = frozenset(
                capability.value for capability in ALL_CAPABILITIES
            )
            orchestrator.active_skills = []
            orchestrator._ensure_run_correlation()
            state = orchestrator.agent_state
            summary_calls: list[str] = []
            durable_checks: list[bool] = []
            responses: list[Any] = []
            if no_summary_control:
                def poison_summary_hook() -> None:
                    summary_calls.append("summary")
                    raise AssertionError("legacy summary hook was called")
                orchestrator.context_manager._build_compression_request = poison_summary_hook
            cached_name = "unit-000.txt"
            cached_content = (workspace / cached_name).read_text(encoding="utf-8")
            state.memory.store_file_observation(
                cached_name, cached_content,
                {
                    "data": cached_content,
                    "source_hash": hashes[cached_name],
                    "source_identity": cached_name,
                    "source_extent": {"kind": "whole"},
                    "evidence_provenance": "EXACT_SOURCE",
                    "complete": True, "truncated": False,
                },
            )
            initial_usage: dict[str, int] = {}
            if seed_lossy_rehydration:
                rehydrated_name = "unit-002.txt"
                state.record_tool_result(
                    "file_reader", {"file_path": rehydrated_name},
                    ToolResult(
                        invocation_id="historical-lossy-observation",
                        status=ToolStatus.SUCCEEDED,
                        data=(workspace / rehydrated_name).read_text(encoding="utf-8"),
                        executed=True,
                        evidence_provenance="EXACT_SOURCE",
                        metadata={
                            "source_identity": rehydrated_name,
                            "source_hash": hashes[rehydrated_name],
                            "source_extent": {"kind": "whole"},
                            "complete": True, "truncated": False,
                        },
                    ),
                    step_id="historical-lossy",
                )
                # Reproduce the real whole-file anti-loop marker while the
                # bounded exact cache bytes are evicted.
                initial_usage[f"fully_read_{rehydrated_name}"] = 1
                entries = state.memory.state.get("file_cache_entries", {})
                if isinstance(entries, dict):
                    entries.pop(rehydrated_name, None)
            steps = tuple(
                ToolPlanStep(f"unit-{index:03d}", "file_reader", {"file_path": f"unit-{index:03d}.txt"})
                for index in range(file_count)
            )
            state.objective = "Read the long-horizon units."
            state.set_plan(Plan(steps))
            milestones: list[Any] = []
            original_finalize = orchestrator.plan_executor.step_executor.finalize_result

            def capture_finalize(*args: Any, **kwargs: Any) -> Any:
                before = current_progress_receipt(orchestrator)
                outcome = original_finalize(*args, **kwargs)
                milestones.append((before, current_progress_receipt(orchestrator)))
                if no_summary_control:
                    manager = orchestrator.context_manager
                    messages = tuple(dict(item) for item in manager.session.messages)
                    history = tuple(dict(item) for item in state.tool_history)
                    gateway.response = '{"answer":"deterministic response"}'
                    manager.maybe_compress_context()
                    responses.append(manager.ask_model(
                        "Return the deterministic answer for the completed unit.",
                        step_type="final", request_contract=ModelRequestContract.FINAL_GENERATION,
                        base_prompt="system", grammar=None, include_task_definition=False,
                    ))
                    durable_checks.append(messages == tuple(dict(item) for item in manager.session.messages)
                                          and history == tuple(dict(item) for item in state.tool_history))
                return outcome

            orchestrator.plan_executor.step_executor.finalize_result = capture_finalize
            if replay_completed:
                # Previously completed logical units are replayed after loss of
                # retained bytes; their completion credit is already baselined.
                for index in range(file_count):
                    state.mark_step_completed(index)
                state.convergence.bootstrap(current_progress_receipt(orchestrator))
            history_before, event_before = len(state.tool_history), len(state.events)
            answer = (
                orchestrator.plan_executor._execute_parallel_read_batch(
                    list(range(file_count)), state.objective, initial_usage
                ) if replay_completed else
                orchestrator.plan_executor.execute(state.objective, initial_usage)
            )
            history = tuple(state.tool_history[history_before:])
            events = tuple(state.events[event_before:])
            classifications = tuple(
                str(getattr(entry.get("result"), "metadata", {}).get("observation_classification", ""))
                for entry in history
                if isinstance(entry, Mapping)
            )
            return {
                "answer": answer,
                "plan": steps,
                "history": history,
                "events": events,
                "metrics": task_metrics(orchestrator),
                "budget": state.budget_ledger.snapshot(),
                "classifications": classifications,
                "event_names": event_types(events),
                "state": state,
                "gateway": gateway,
                "milestones": milestones,
                "summary_calls": summary_calls, "durable_checks": durable_checks,
                "responses": responses,
            }
        finally:
            application.close()


__all__ = ["ProductionGateway", "RecordingEventSink", "RecordingMetricsSink", "event_type", "event_types", "real_plan_run", "result"]
