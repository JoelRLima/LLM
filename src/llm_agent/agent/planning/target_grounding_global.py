"""Canonical mutation-time global uniqueness revalidation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .intent_admission import AuthorityEnvelope
from .target_grounding_discovery import SymbolDefinition, discover_symbol_definitions
from .target_grounding_model import GroundedTargetSet, GroundingError


def revalidate_global_symbol_uniqueness(
    targets: GroundedTargetSet,
    workspace_root: str | Path,
    envelope: AuthorityEnvelope,
    *,
    discovery_owner: Callable[..., tuple[SymbolDefinition, ...]] = discover_symbol_definitions,
) -> None:
    """Re-run bounded canonical discovery against current authority."""

    symbolic_targets = tuple(
        target
        for target in targets.targets
        if target.symbol is not None and target.selector_kind == "symbol"
    )
    if not symbolic_targets:
        return
    root = Path(workspace_root).expanduser().resolve()
    limits = {
        "max_files": targets.max_files,
        "max_source_bytes": targets.max_source_bytes,
        "max_total_source_bytes": targets.max_total_source_bytes,
        "max_enumerated_paths": targets.max_enumerated_paths,
        "max_candidates": targets.max_candidates,
    }
    checked: set[str] = set()
    for target in symbolic_targets:
        symbol = target.symbol
        if symbol is None or symbol in checked:
            continue
        checked.add(symbol)
        definitions = discovery_owner(root, envelope, symbol, **limits)
        candidates = [
            (definition.resource, location)
            for definition in definitions
            for location in definition.locations
        ]
        if not candidates:
            raise GroundingError("GROUNDING_STALE")
        if len(candidates) != 1:
            raise GroundingError("GROUNDING_AMBIGUOUS")
        resource, location = candidates[0]
        if resource != target.resource or location[:2] != (
            target.definition_line,
            target.definition_column,
        ):
            raise GroundingError("GROUNDING_STALE")


__all__ = ["revalidate_global_symbol_uniqueness"]
