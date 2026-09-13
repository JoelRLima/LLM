"""Bounded, profile-driven diagnostics for model compatibility geometry."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StreamEvent,
    StreamEventType,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from agent.llm.model_profile import ResolvedModelProfile
from agent.llm.request_geometry import resolve_effective_request_geometry
from agent.llm.structured_output import parse_structured_response

CANARY_SCHEMA_VERSION = "MODEL-COMPATIBILITY-CANARY-V1"
CANARY_CASE_IDS = tuple(f"MC16-{index:02d}" for index in range(1, 9))
CANARY_SENTINEL = "W16-CANARY-SENTINEL"
CANARY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"sentinel": {"type": "string", "enum": [CANARY_SENTINEL]}}, "required": ["sentinel"], "additionalProperties": False}
CANARY_GBNF_GRAMMAR = r'''root ::= "{\"sentinel\":\"W16-CANARY-SENTINEL\"}"'''
MAX_LIVE_CALLS = 10


@dataclass(frozen=True, slots=True)
class CanaryCase:
    case_id: str
    status: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            **dict(self.details),
        }


class DeterministicCanaryGateway:
    """Scripted conforming gateway; it never opens a transport endpoint."""

    provider_name = "deterministic-canary"
    model = "deterministic-canary"

    def __init__(self, profile: ResolvedModelProfile, *, malformed: bool = False) -> None:
        self.resolved_profile = profile
        self.capabilities = profile.capabilities
        self.malformed = malformed

    def complete(self, request: ModelRequest) -> ModelResponse:
        content = "{malformed" if self.malformed else '{"sentinel":"' + CANARY_SENTINEL + '"}'
        return ModelResponse(
            content=content,
            provider_metadata={"observed_provider_model_id": self.model},
        )

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        yield StreamEvent(StreamEventType.REASONING, text="private-reasoning")
        yield StreamEvent(StreamEventType.CONTENT, text=CANARY_SENTINEL)
        yield StreamEvent(StreamEventType.DONE)

    def measure_request_input_tokens(self, request: ModelRequest) -> None:
        return None

    def count_tokens(self, text: str) -> int:
        return len(text)


def _mode(profile: ResolvedModelProfile) -> StructuredOutputMode | None:
    for mode in profile.capabilities.structured_output_modes:
        if mode in {StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA}:
            return mode
    return None


def _structured_request(mode: StructuredOutputMode | None) -> StructuredOutputRequest | None:
    if mode is None or mode == StructuredOutputMode.NONE:
        return None
    if mode == StructuredOutputMode.GBNF:
        return StructuredOutputRequest(mode=mode, grammar=CANARY_GBNF_GRAMMAR)
    if mode == StructuredOutputMode.JSON_SCHEMA:
        return StructuredOutputRequest(mode=mode, schema=CANARY_SCHEMA)
    if mode == StructuredOutputMode.JSON_PROMPT:
        return StructuredOutputRequest(mode=mode, schema=CANARY_SCHEMA, instruction=f'Return exactly the JSON object {{"sentinel":"{CANARY_SENTINEL}"}} and nothing else.')
    return StructuredOutputRequest(mode=mode)


def _request(
    profile: ResolvedModelProfile,
    gateway: Any,
    *,
    requested: int,
    mode: StructuredOutputMode | None,
    stream: bool = False,
) -> tuple[ModelRequest, Any]:
    geometry = resolve_effective_request_geometry(
        requested,
        profile.max_output_tokens,
        profile.capabilities.reasoning,
        mode,
        profile.compatibility,
        capabilities=profile.capabilities,
    )
    structured = _structured_request(mode)
    request = ModelRequest(
        messages=(ModelMessage("system", "canary"), ModelMessage("user", CANARY_SENTINEL)),
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        stream=stream,
        reasoning_budget=geometry.effective_reasoning_budget,
        requested_reasoning_budget=geometry.requested_reasoning_budget,
        compatibility_reason_code=geometry.compatibility_reason_code,
        structured_output=structured,
    )
    return request, geometry


def _passed(case_id: str, **details: Any) -> CanaryCase:
    return CanaryCase(case_id, "passed", details)


def _not_applicable(case_id: str, reason: str) -> CanaryCase:
    return CanaryCase(case_id, "not_applicable", {"reason": reason})


def _failed(case_id: str, reason: str, **details: Any) -> CanaryCase:
    return CanaryCase(case_id, "failed", {"reason": reason, **details})


def _case_plain_off(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    request, geometry = _request(profile, gateway, requested=0, mode=None)
    response = _invoke(gateway, request, "complete")
    valid = bool(response.content) and geometry.requested_reasoning_budget == 0 and geometry.effective_reasoning_budget == 0 and request.structured_output is None
    return _passed(case_id="MC16-01", requested=0, effective=0) if valid else _failed("MC16-01", "plain reasoning-off geometry mismatch")


def _case_plain_on(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    reasoning = profile.capabilities.reasoning
    if not reasoning:
        return _not_applicable("MC16-02", "reasoning_not_declared")
    request, geometry = _request(profile, gateway, requested=512, mode=None)
    _invoke(gateway, request, "complete")
    valid = geometry.effective_reasoning_budget > 0 and geometry.compatibility_reason_code is None
    return _passed("MC16-02", requested=512, effective=geometry.effective_reasoning_budget) if valid else _failed("MC16-02", "plain reasoning was suppressed")


def _case_structured_off(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    if mode is None:
        return _not_applicable("MC16-03", "constrained_output_not_declared")
    request, geometry = _request(profile, gateway, requested=0, mode=mode)
    response = _invoke(gateway, request, "complete")
    valid = (
        geometry.effective_reasoning_budget == 0
        and request.structured_output is not None
        and _is_exact_sentinel(response.content)
    )
    return _passed("MC16-03", mode=mode.value) if valid else _failed("MC16-03", "structured reasoning-off geometry mismatch")


def _case_structured_policy(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    if not profile.capabilities.reasoning or mode is None:
        return _not_applicable("MC16-04", "reasoning_or_constrained_output_not_declared")
    request, geometry = _request(profile, gateway, requested=512, mode=mode)
    _invoke(gateway, request, "complete")
    disabled = profile.compatibility.structured_reasoning.value == "disable_reasoning"
    if disabled:
        valid = (
            geometry.effective_reasoning_budget == 0
            and geometry.compatibility_reason_code == "STRUCTURED_REASONING_DISABLED_BY_PROFILE"
        )
    else:
        valid = geometry.compatibility_reason_code is None
    return _passed("MC16-04", mode=mode.value, effective=geometry.effective_reasoning_budget) if valid else _failed("MC16-04", "compatibility geometry mismatch")


def _case_streaming(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    if not profile.capabilities.streaming:
        return _not_applicable("MC16-05", "streaming_not_declared")
    request, _geometry = _request(profile, gateway, requested=0, mode=None, stream=True)
    visible = "".join(event.text for event in _invoke(gateway, request, "stream") if event.type is StreamEventType.CONTENT)
    return _passed("MC16-05", visible=visible) if visible == CANARY_SENTINEL else _failed("MC16-05", "reasoning leaked into visible stream")


def _case_repeated_structured(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    if mode is None:
        return _not_applicable("MC16-06", "constrained_output_not_declared")
    before = len(gateway.export_evidence()["model_calls"])
    for _ in range(3):
        request, _geometry = _request(profile, gateway, requested=0, mode=mode)
        response = _invoke(gateway, request, "complete")
        if not _is_exact_sentinel(response.content):
            return _failed("MC16-06", "structured sentinel missing")
    calls = len(gateway.export_evidence()["model_calls"]) - before
    return _passed("MC16-06", calls=calls) if calls == 3 else _failed("MC16-06", "structured call count mismatch", calls=calls)


def _case_malformed(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    if mode is None:
        return _not_applicable("MC16-07", "constrained_output_not_declared")
    malformed_gateway = RecordingGateway(DeterministicCanaryGateway(profile, malformed=True))
    request, _geometry = _request(profile, malformed_gateway, requested=0, mode=mode)
    response = _invoke(malformed_gateway, request, "complete")
    try:
        parse_structured_response(response.content, {"type": "object"})
    except Exception:
        return _passed("MC16-07", effects=0, salvaged=False)
    return _failed("MC16-07", "malformed structured response was accepted")


def _case_alternate(profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    alternate = RecordingGateway(DeterministicCanaryGateway(profile))
    requested = 512 if profile.capabilities.reasoning else 0
    request, geometry = _request(profile, alternate, requested=requested, mode=mode)
    response = _invoke(alternate, request, "complete")
    return _passed("MC16-08", provider=alternate.provider_name, effective=geometry.effective_reasoning_budget) if response.content else _failed("MC16-08", "alternate gateway produced no response")


_CANARY_HANDLERS = {
    "MC16-01": _case_plain_off,
    "MC16-02": _case_plain_on,
    "MC16-03": _case_structured_off,
    "MC16-04": _case_structured_policy,
    "MC16-05": _case_streaming,
    "MC16-06": _case_repeated_structured,
    "MC16-07": _case_malformed,
    "MC16-08": _case_alternate,
}


def _run_case(case_id: str, profile: ResolvedModelProfile, gateway: Any, mode: StructuredOutputMode | None) -> CanaryCase:
    handler = _CANARY_HANDLERS.get(case_id)
    return handler(profile, gateway, mode) if handler is not None else _failed(case_id, "unknown canary case")


def _invoke(recorder: Any, request: ModelRequest, operation: str) -> Any:
    return getattr(recorder, operation)(request)


def _is_exact_sentinel(content: str) -> bool:
    try:
        return bool(parse_structured_response(content, CANARY_SCHEMA) == {"sentinel": CANARY_SENTINEL})
    except Exception:
        return False


def run_model_canaries(
    profile: ResolvedModelProfile,
    gateway: Any,
    *,
    live_model_used: bool,
) -> dict[str, Any]:
    """Run each bounded case once and return a diagnostic-only report."""

    recorder = RecordingGateway(gateway)
    mode = _mode(profile)
    cases: list[CanaryCase] = []
    for case_id in CANARY_CASE_IDS:
        cases.append(_run_case(case_id, profile, recorder, mode))
    evidence = recorder.export_evidence()
    calls = len(evidence["model_calls"])
    if live_model_used and calls > MAX_LIVE_CALLS:
        for index, case in enumerate(cases):
            if case.case_id == "MC16-08":
                cases[index] = _failed(
                    "MC16-08",
                    "live call ceiling exceeded",
                    calls=calls,
                )
                break
    counts = {status: sum(case.status == status for case in cases) for status in ("passed", "failed", "not_applicable", "blocked")}
    return {
        "schema_version": CANARY_SCHEMA_VERSION,
        "profile_name": profile.name,
        "model_config_fingerprint": profile.fingerprint,
        "declared_model_identity": {"profile": profile.name, "provider": profile.provider, "model": profile.model, "fingerprint": profile.fingerprint},
        "observed_model_identity": evidence["observed_provider_identity"],
        "live_model_used": bool(live_model_used),
        "cases": [case.to_dict() for case in cases],
        "summary": {
            "total": len(cases),
            **counts,
        },
    }


__all__ = [
    "CANARY_CASE_IDS",
    "CANARY_GBNF_GRAMMAR", "CANARY_SCHEMA",
    "CANARY_SCHEMA_VERSION",
    "CANARY_SENTINEL",
    "DeterministicCanaryGateway",
    "run_model_canaries",
]
