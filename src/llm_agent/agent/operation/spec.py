"""Pure builtin operation metadata specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from llm_agent.agent.capabilities import Capability, canonical_capabilities
from llm_agent.agent.operation.contracts import CancellationSafetyMode
from llm_agent.agent.operation.provenance import ArgumentOrigin
from llm_agent.agent.operation.schema import freeze_result_data_schema, thaw_json_like
from llm_agent.agent.operation.usage_examples import normalize_usage_examples


@dataclass(frozen=True)
class SkillSpec:
    """Fonte canônica para construção, custo, risco e agendamento."""

    module: str
    class_name: str
    name: str
    kwargs: Dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[Capability] = frozenset()
    cost: int = 5
    cacheable: bool = False
    idempotent: bool = False
    timeout_seconds: Optional[int] = None
    category: str = "EXECUTE"
    public_invocation_fields: frozenset[str] = frozenset()
    argument_provenance: Mapping[str, frozenset[str | ArgumentOrigin]] = field(
        default_factory=dict
    )
    result_data_schema: Mapping[str, Any] | None = field(default=None, kw_only=True)
    cancellation_safety: CancellationSafetyMode = field(
        default=CancellationSafetyMode.UNSUPPORTED,
        kw_only=True,
    )
    usage_examples: tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_data_schema", freeze_result_data_schema(self.result_data_schema))
        object.__setattr__(
            self,
            "usage_examples",
            normalize_usage_examples(self.usage_examples),
        )
        if not isinstance(self.cancellation_safety, CancellationSafetyMode):
            object.__setattr__(
                self,
                "cancellation_safety",
                CancellationSafetyMode(str(self.cancellation_safety)),
            )

    def __getattribute__(self, name: str) -> Any:
        if name == "result_data_schema":
            snapshot = object.__getattribute__(self, "result_data_schema")
            return None if snapshot is None else thaw_json_like(snapshot)
        return object.__getattribute__(self, name)

    @property
    def side_effects(self) -> bool:
        return bool(
            canonical_capabilities(self.capabilities)
            & {
                Capability.WRITE,
                Capability.PROCESS,
                Capability.NETWORK,
                Capability.VCS_WRITE,
                Capability.PACKAGE_INSTALL,
                Capability.VALIDATE,
            }
        )


__all__ = ["SkillSpec"]
