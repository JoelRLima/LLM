"""Declarative visual contributions carry data selectors, never executable hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Completeness = Literal["partial", "final", "unknown"]
Renderer = Literal["text", "json", "diff"]


@dataclass(frozen=True, slots=True)
class VisualContribution:
    """A trusted composition maps existing artifact kinds to native renderers.

    This does not register or activate an execution extension. No imports,
    scripts, URLs, callbacks or extension entrypoints are accepted.
    """

    kind: str
    renderer: Renderer

    def __post_init__(self) -> None:
        if not isinstance(self.renderer, str) or self.renderer not in {"text", "json", "diff"}:
            raise ValueError("unsupported native renderer")
        if not isinstance(self.kind, str) or self.kind not in {"text", "file", "table", "search", "diff", "log", "test", "debug", "json", "report"}:
            raise ValueError("unsupported existing artifact kind")


_NATIVE_RENDERERS: tuple[tuple[str, Renderer], ...] = (
        ("report", "json"), ("json", "json"), ("diff", "diff"),
        ("text", "text"), ("file", "text"), ("table", "text"),
        ("search", "text"), ("log", "text"), ("test", "text"), ("debug", "text"),
)
NATIVE_CONTRIBUTIONS = tuple(VisualContribution(kind, renderer) for kind, renderer in _NATIVE_RENDERERS)


def parse_visual_contribution(value: object) -> VisualContribution:
    if not isinstance(value, dict) or set(value) != {"kind", "renderer"}:
        raise ValueError("a visual contribution accepts only kind and renderer")
    return VisualContribution(**value)


@dataclass(frozen=True, slots=True)
class PanelReference:
    workspace_id: str
    run_id: str | None
    artifact_id: str
    title: str
    source: str
    kind: str
    completeness: Completeness
    locator: str

    @property
    def key(self) -> tuple[str, str | None, str]:
        return self.workspace_id, self.run_id, self.artifact_id


@dataclass(frozen=True, slots=True)
class PanelContent:
    reference: PanelReference
    text: str
    renderer: Renderer
    sha256: str | None
    truncated: bool
    next_offset: int | None = None
