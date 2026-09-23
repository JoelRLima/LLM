"""Optional semantic reranking over an already offered local candidate pool."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.cancellation import CancellationToken
from agent.discovery.contracts import (
    DISCOVERY_NO_MATCH,
    DISCOVERY_SEMANTIC_INVALID_RESPONSE,
    DISCOVERY_SEMANTIC_NOT_AUTHORIZED,
    DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE,
    DISCOVERY_SEMANTIC_UNAVAILABLE,
    DISCOVERY_SEMANTIC_USED,
    MAX_DISCOVERY_RESULTS,
    MAX_DISCOVERY_SEMANTIC_CANDIDATES,
    DiscoveryCandidateV1,
    DiscoveryResultV1,
)
from agent.discovery.ranking import normalize_query
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call import ModelCallService

COMMAND_DISCOVERY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "uniqueItems": True,
        }
    },
    "required": ["candidate_ids"],
    "additionalProperties": False,
}
COMMAND_DISCOVERY_GBNF = (
    'root ::= "{" ws "\"candidate_ids\"" ws ":" ws "[" ws (string (ws "," ws string){0,2})? ws "]" ws "}"\n'
    'string ::= "\"" ([^"\\] | "\\" .)* "\""\n'
    'ws ::= [ \t\n\r]*'
)
MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES = 65_536


def create_model_gateway(config: Any) -> Any:
    """Resolve the canonical model factory only for an actual semantic call."""

    module = importlib.import_module("agent.llm.providers")
    return module.create_model_gateway(config)


@dataclass(frozen=True, slots=True)
class CommandDiscoveryDecision:
    candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.candidate_ids) > 3 or len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("invalid command discovery candidate_ids")
        if any(not isinstance(item, str) or not item or len(item) > 192 for item in self.candidate_ids):
            raise ValueError("invalid command discovery candidate id")

    def to_dict(self) -> dict[str, object]:
        return {"candidate_ids": list(self.candidate_ids)}


def validate_command_discovery_response(value: object, offered_ids: Sequence[str]) -> CommandDiscoveryDecision:
    if not isinstance(value, Mapping) or set(value) != {"candidate_ids"}:
        raise ValueError("invalid command discovery response")
    raw = value["candidate_ids"]
    if not isinstance(raw, list) or len(raw) > 3:
        raise ValueError("invalid command discovery candidate list")
    if any(not isinstance(item, str) or not item or len(item) > 192 for item in raw):
        raise ValueError("invalid command discovery candidate id")
    if len(set(raw)) != len(raw) or any(item not in set(offered_ids) for item in raw):
        raise ValueError("command discovery returned an unoffered id")
    return CommandDiscoveryDecision(tuple(raw))


def _parse_json_response(response: object) -> object:
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise ValueError("semantic response is not text")
    return json.loads(content)


def _semantic_result(result: DiscoveryResultV1, reason: str) -> DiscoveryResultV1:
    return DiscoveryResultV1(result.query, result.candidates, (*result.reasons, reason), True, False)


def _model_request(gateway: Any, payload: bytes) -> Any:
    llm_contracts = importlib.import_module("agent.llm.contracts")
    decision_contract = importlib.import_module("agent.llm.decision_contract")
    return llm_contracts.ModelRequest(
        messages=(
            llm_contracts.ModelMessage(
                "system",
                "Select up to three command IDs only from the supplied candidates that best match the user's request. "
                "Do not invent IDs or commands. Do not execute anything. Return only the required structured response.",
            ),
            llm_contracts.ModelMessage("user", payload.decode("utf-8")),
        ),
        model=str(getattr(gateway, "model", "")),
        temperature=0.0,
        max_output_tokens=128,
        stream=False,
        structured_output=llm_contracts.StructuredOutputRequest(
            mode=llm_contracts.StructuredOutputMode.GBNF,
            schema=COMMAND_DISCOVERY_SCHEMA,
            grammar=COMMAND_DISCOVERY_GBNF,
        ),
        request_contract=decision_contract.ModelRequestContract.COMMAND_DISCOVERY,
    )


def _complete_model_call(gateway: Any, request: Any) -> object:
    """Execute one authorized discovery attempt through the canonical owner."""

    context = TaskExecutionContext(
        model_gateway=gateway,
        cancellation=CancellationToken(),
        limits=RuntimeLimits(
            max_model_calls=1,
            max_output_tokens=request.max_output_tokens,
        ),
    )
    return ModelCallService.for_context(context).complete(
        request,
        operation="command_discovery",
    ).response


def _ordered_candidates(
    result: DiscoveryResultV1,
    offered: Sequence[DiscoveryCandidateV1],
    decision: CommandDiscoveryDecision,
) -> tuple[DiscoveryCandidateV1, ...]:
    selected = set(decision.candidate_ids)
    ordered = [
        DiscoveryCandidateV1(
            candidate.entry,
            candidate.available,
            candidate.disabled_reason,
            candidate.match_kind,
            candidate.local_score,
            candidate.frecency_score,
            True,
        )
        for entry_id in decision.candidate_ids
        for candidate in offered
        if candidate.entry.entry_id == entry_id
    ]
    ordered.extend(candidate for candidate in result.candidates if candidate.entry.entry_id not in selected)
    return tuple(ordered[:MAX_DISCOVERY_RESULTS])


class SemanticCommandDiscovery:
    """Own the one-call semantic contract and local-only fallback."""

    def __init__(
        self,
        *,
        gateway_factory: Callable[[Any], Any] = create_model_gateway,
        gateway_config: Any | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        # Keep the canonical ResolvedModelProfile intact. Converting it to a
        # redacted dict here would discard provider credentials and profile
        # binding before the provider factory sees it.
        self.gateway_config = gateway_config if gateway_config is not None else {}

    @staticmethod
    def _payload(query: str, candidates: Sequence[DiscoveryCandidateV1]) -> tuple[bytes, tuple[DiscoveryCandidateV1, ...]]:
        offered = list(candidates[:MAX_DISCOVERY_SEMANTIC_CANDIDATES])
        tight = False
        while True:
            document = {
                "schema": "w20.command_discovery.v1",
                "query": query,
                "candidates": [
                    {
                        "entry_id": item.entry.entry_id,
                        "title": item.entry.title[:96 if tight else 128],
                        "description": item.entry.description[:192 if tight else 512],
                        "preferred_invocation": item.entry.preferred_invocation[:192 if tight else 512],
                        "aliases": [value[:96] for value in item.entry.aliases[:4 if tight else 16]],
                        "keywords": [value[:64] for value in item.entry.keywords[:8 if tight else 24]],
                        "examples": [value[:128] for value in item.entry.examples[:2 if tight else 8]],
                    }
                    for item in offered
                ],
            }
            encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) <= MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES:
                return encoded, tuple(offered)
            if not offered:
                return encoded, ()
            if len(offered) == 1 and not tight:
                tight = True
                continue
            if len(offered) == 1 and tight:
                return encoded, tuple(offered)
            offered.pop()

    def _gateway(self) -> Any | None:
        try:
            gateway = self.gateway_factory(self.gateway_config)
        except Exception:
            return None
        capabilities = getattr(gateway, "capabilities", None)
        supports = getattr(capabilities, "supports", None)
        if callable(supports):
            try:
                structured_output_mode = importlib.import_module("agent.llm.contracts").StructuredOutputMode.GBNF
                if not supports(structured_output_mode):
                    return None
            except Exception:
                return None
        return gateway

    def rerank(
        self,
        result: DiscoveryResultV1,
        *,
        semantic_allowed: bool,
        candidate_pool: Sequence[DiscoveryCandidateV1],
    ) -> DiscoveryResultV1:
        if not result.semantic_requested:
            return result
        if not candidate_pool:
            return _semantic_result(result, DISCOVERY_NO_MATCH)
        if not semantic_allowed:
            return _semantic_result(result, DISCOVERY_SEMANTIC_NOT_AUTHORIZED)
        payload, offered = self._payload(normalize_query(result.query), candidate_pool)
        if len(payload) > MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES or not offered:
            reason = DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE if len(payload) > MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES else DISCOVERY_SEMANTIC_UNAVAILABLE
            return _semantic_result(result, reason)
        gateway = self._gateway()
        if gateway is None:
            return _semantic_result(result, DISCOVERY_SEMANTIC_UNAVAILABLE)
        try:
            request = _model_request(gateway, payload)
            response = _complete_model_call(gateway, request)
        except Exception:
            return _semantic_result(result, DISCOVERY_SEMANTIC_UNAVAILABLE)
        try:
            decision = validate_command_discovery_response(
                _parse_json_response(response),
                [item.entry.entry_id for item in offered],
            )
        except Exception:
            return _semantic_result(result, DISCOVERY_SEMANTIC_INVALID_RESPONSE)
        return DiscoveryResultV1(
            result.query,
            _ordered_candidates(result, offered, decision),
            (*result.reasons, DISCOVERY_SEMANTIC_USED),
            True,
            True,
        )

    def search(
        self,
        result: DiscoveryResultV1,
        *,
        semantic_allowed: bool,
        candidate_pool: Sequence[DiscoveryCandidateV1],
    ) -> DiscoveryResultV1:
        return self.rerank(result, semantic_allowed=semantic_allowed, candidate_pool=candidate_pool)


__all__ = [
    "COMMAND_DISCOVERY_GBNF",
    "COMMAND_DISCOVERY_SCHEMA",
    "CommandDiscoveryDecision",
    "create_model_gateway",
    "MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES",
    "SemanticCommandDiscovery",
    "validate_command_discovery_response",
]
