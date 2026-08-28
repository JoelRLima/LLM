"""Safe, bounded presentations of a canonical planning context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from agent.planning.planning_context import PlanningContextError, PlanningTool
from agent.planning.presentation_models import (
    PlanningPresentationBudget,
    PlanningPresentationError,
    PlanningToolIndexEntry,
)
from agent.planning.presentation_rendering import (
    render_detailed,
    render_index,
    tool_index_entry,
    tool_metadata,
)
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


@dataclass(frozen=True, slots=True)
class PlanningPresentationSnapshot:
    """Immutable planner-specific view derived only from a planning snapshot."""

    planning_context_id: str
    planner_kind: str
    tools: tuple[PlanningTool, ...] = ()
    presented_names: frozenset[str] = field(default_factory=frozenset)
    runtime_identity: RuntimeSnapshotIdentity | None = None

    def __post_init__(self) -> None:
        """Normalize and validate the immutable snapshot projection."""

        if not self.planning_context_id.strip() or not self.planner_kind.strip():
            raise PlanningPresentationError("view de planning requer identidade e tipo")
        if not isinstance(self.runtime_identity, RuntimeSnapshotIdentity):
            raise PlanningPresentationError("view de planning requer runtime identity")
        ordered = tuple(sorted(self.tools, key=lambda tool: tool.name))
        names = frozenset(self.presented_names)
        if len({tool.name for tool in ordered}) != len(ordered):
            raise PlanningPresentationError("view de planning contém tools duplicadas")
        if names != {tool.name for tool in ordered}:
            raise PlanningPresentationError("presented_names diverge das tools renderizadas")
        object.__setattr__(self, "tools", ordered)
        object.__setattr__(self, "presented_names", names)

    @property
    def workspace_id(self) -> str:
        identity = self.runtime_identity
        if identity is None:
            raise PlanningPresentationError("view sem runtime identity")
        return cast(str, identity.workspace_id)

    def metadata_dict(self) -> dict[str, Any]:
        """Return optimizer metadata copied from the safe planning model."""

        return {tool.name: tool_metadata(tool) for tool in self.tools}

    @property
    def tool_index(self) -> tuple[PlanningToolIndexEntry, ...]:
        """Derive the Level-0 index from this exact immutable view."""

        return tuple(tool_index_entry(tool) for tool in self.tools)

    def render_index(self, *, context_limit: int = 8_192) -> str:
        """Render only names and bounded purpose metadata, never schemas."""

        return render_index(
            self.tools,
            PlanningPresentationBudget.for_context_limit(context_limit),
        )

    def render_detailed(self, *, context_limit: int = 8_192) -> str:
        """Render full cards for exactly the tools in this snapshot."""

        return render_detailed(
            self.tools,
            PlanningPresentationBudget.for_context_limit(context_limit),
        )

    @staticmethod
    def _validate_planning_view_binding(
        context: Any,
        planning_view: "PlanningPresentationSnapshot",
        planner_kind: str | None = None,
    ) -> None:
        """Ensure a planner view belongs to the exact context it presents."""

        if planning_view.planning_context_id != context.snapshot_id:
            raise PlanningContextError("planning context e view divergem")
        if planning_view.runtime_identity != context.runtime_identity:
            raise PlanningContextError("runtime identity do context e view divergem")
        if planning_view.workspace_id != context.workspace_id:
            raise PlanningContextError("workspace do context e view divergem")
        if planner_kind is not None and planning_view.planner_kind != planner_kind:
            raise PlanningContextError("planning view planner kind diverge")
        eligible_names = getattr(context, "eligible_names", None)
        if isinstance(eligible_names, (set, frozenset, tuple, list)) and not planning_view.presented_names.issubset(
            frozenset(eligible_names)
        ):
            raise PlanningContextError("planning view excede as tools elegiveis do contexto")
        present = getattr(context, "present", None)
        if callable(present):
            try:
                expected = present(planning_view.planner_kind, planning_view.presented_names)
            except (TypeError, ValueError) as exc:
                raise PlanningContextError("planning view nao pode ser derivada do contexto") from exc
            if expected.tools != planning_view.tools:
                raise PlanningContextError("planning view diverge da projecao canonica")

    def render(self, *, compact: bool = False, context_limit: int = 8_192) -> str:
        """Render the Level-0 index or detailed cards with fail-closed budgets."""

        if compact:
            return self.render_index(context_limit=context_limit)
        return self.render_detailed(context_limit=context_limit)


def validate_planning_view_binding(
    context: Any,
    planning_view: PlanningPresentationSnapshot,
    planner_kind: str | None = None,
) -> None:
    PlanningPresentationSnapshot._validate_planning_view_binding(context, planning_view, planner_kind)


__all__ = [
    "PlanningPresentationBudget",
    "PlanningPresentationError",
    "PlanningPresentationSnapshot",
    "PlanningToolIndexEntry",
]
