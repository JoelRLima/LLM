"""Private primitive-only settlement and inspection projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

from llm_agent.agent.application_result import AgentRunResult as _AgentRunResult
from llm_agent.agent.interaction.types import AgentInteractionResult as _AgentInteractionResult
from llm_agent.agent.llm.errors import ModelConnectionError as _ModelConnectionError
from llm_agent.agent.llm.errors import ModelTimeoutError as _ModelTimeoutError
from llm_agent.agent.observability.trace_store import TraceCorruptError as _TraceCorruptError
from llm_agent.agent.observability.trace_store import TraceUnavailableError as _TraceUnavailableError
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError
from llm_agent.application.task_results import (
    HeadlessTaskObservation,
    InteractiveTaskObservation,
    PrimitiveDocument,
    PrimitiveScalar,
    PrimitiveValue,
    TaskObservation,
)


class _Settlement(Protocol):
    _value: _AgentRunResult | _AgentInteractionResult
    _legacy: bool


def _primitive(value: object) -> PrimitiveValue:
    """Copy only primitives; exact scalar types reject str/int Agent enums."""
    if value is None or type(value) in (bool, int, float, str):
        return cast(PrimitiveScalar, value)
    if any(base.__module__.startswith("llm_agent.agent") for base in type(value).__mro__):
        raise TypeError("public observation contains an Agent owner")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("public observation keys must be strings")
        return {key: _primitive(item) for key, item in value.items()}
    if type(value) is list:
        return [_primitive(item) for item in value]
    if type(value) is tuple:
        return tuple(_primitive(item) for item in value)
    raise TypeError("public observation contains a non-primitive value")


def _receipt(value: _AgentRunResult) -> PrimitiveDocument:
    receipt = value.receipt
    if isinstance(receipt, Mapping) and receipt:
        return cast(PrimitiveDocument, _primitive(receipt))
    snapshot = value.canonical_snapshot
    if snapshot is None:
        return {}
    data = snapshot.to_dict()
    facts = data.get("projection_facts") or {}
    outcome = data.get("operational_outcome") or {}
    result = {key: facts[key] for key in ("tools", "validation", "rollback") if key in facts}
    if "files_affected" in outcome:
        result["files_affected"] = outcome["files_affected"]
    if "validation_status" in outcome and "validation" not in result:
        status = outcome["validation_status"]
        result["validation"] = {"ran": status is not None, "outcome": status}
    count = facts.get("replan_count")
    if type(count) is int:
        result["replan"] = {"occurred": count > 0, "count": count}
    if data.get("status") is not None:
        result["status"] = data["status"]
    if data.get("failure_fact"):
        result["error"] = data["failure_fact"]
    if value.report_path:
        result["report_path"] = value.report_path
    return cast(PrimitiveDocument, _primitive(result))


def _run_report_fields(value: _AgentRunResult, result: InteractiveTaskObservation) -> None:
    result["receipt"] = _receipt(value)
    result["report_path"] = value.report_path
    snapshot = value.canonical_snapshot
    if snapshot is not None and type(snapshot.run_id) is str:
        result["run_id"] = snapshot.run_id
        result["report_completeness"] = "final"


def _interaction_fields(value: _AgentInteractionResult, result: InteractiveTaskObservation) -> None:
    result["interaction_usage"] = cast(PrimitiveDocument, _primitive(value.interaction_usage))
    if value.reason_code is not None:
        result["reason_code"] = value.reason_code
    resolution = value.resolution
    if resolution is not None:
        result["resolution"] = {
            "action": resolution.action.value,
            "boundary": resolution.boundary.value,
            "provenance": resolution.provenance.value,
            "ambiguity": resolution.ambiguity.value,
            "directive": resolution.directive.value if resolution.directive is not None else None,
            "deliberation_profile": resolution.deliberation_profile.value if resolution.deliberation_profile is not None else None,
            "reason_code": resolution.reason_code,
        }
    if value.run_result is not None:
        _run_report_fields(value.run_result, result)
    if isinstance(value.transport_error, _ModelConnectionError):
        result["model_transport_failure"] = "MODEL_CONNECTION_ERROR"
    elif isinstance(value.transport_error, _ModelTimeoutError):
        result["model_transport_failure"] = "MODEL_TIMEOUT"


def _observe_task_settlement(
    settlement: _Settlement, *, channel: Literal["headless", "interactive"],
) -> TaskObservation:
    value = settlement._value
    if channel == "headless":
        return cast(HeadlessTaskObservation, _primitive(value.to_dict()))
    if channel != "interactive":
        raise ValueError("unsupported settlement channel")
    result: InteractiveTaskObservation = {
        "answer": value.answer, "status": value.status,
        "success": value.success, "legacy_transcript": settlement._legacy,
    }
    if value.error is not None:
        result["error"] = value.error
    if isinstance(value, _AgentInteractionResult):
        _interaction_fields(value, result)
    return cast(InteractiveTaskObservation, _primitive(result))


def _read_runtime_inspection(owner: Any) -> dict[str, Any]:
    """Only the fields already rendered by interactive /inspect; no query/reader API."""
    try:
        snapshot = owner.inspection_service().snapshot(limit=100)
        data = snapshot.to_dict()
    except _TraceCorruptError as exc:
        raise InspectionCorruptDataError(str(exc)) from exc
    except _TraceUnavailableError as exc:
        raise InspectionUnavailableError(str(exc)) from exc
    fields = ("run", "heartbeat", "timeline", "current", "plan_steps", "model_calls", "tools",
              "validation", "recovery", "changes", "metrics", "convergence", "warnings", "selected_detail", "issues")
    observation = {key: data[key] for key in fields}
    observation["run"] = {key: data["run"][key] for key in ("run_id", "status", "completeness", "liveness", "mode")}
    observation["timeline"] = [{key: item[key] for key in ("sequence", "timestamp", "source", "category", "title", "status")} for item in data["timeline"]]
    observation["warnings"] = [{key: item[key] for key in ("sequence", "title", "summary")} for item in data["warnings"]]
    observation["heartbeat"] = {key: data["heartbeat"].get(key) for key in ("observer_heartbeat", "semantic_activity", "silence")}
    return cast(dict[str, Any], _primitive(observation))
