"""Value objects shared by the context projection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context_projection import ModelContextProjection


@dataclass(frozen=True, slots=True)
class ContextRequestFit:
    """Request plus projection chosen before provider dispatch."""

    request: Any
    projection: ModelContextProjection
    mandatory_measurement: Any
    final_measurement: Any = None
    mandatory_overflow: bool = False


@dataclass(frozen=True, slots=True)
class ProjectGuidanceProjection:
    """Truthful bounded projection of applicable project guidance files."""

    records: tuple[Any, ...]
    applicable_count: int
    included_count: int
    omitted_count: int
    file_set_complete: bool
    content_complete: bool
    coverage_complete: bool
    rejected_count: int = 0

    @property
    def source_records(self) -> tuple[Any, ...]:
        return self.records


__all__ = ["ContextRequestFit", "ProjectGuidanceProjection"]
