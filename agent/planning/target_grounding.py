"""Deterministic grounding of admitted target selectors."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agent.interaction.intent_claim import IntentClaimV1, bind_current_subject_evidence
from agent.runtime.path_safety import (
    WorkspacePathError,
    assert_path_safe,
    resolve_workspace_path,
)

from .intent_admission import (
    AdmittedIntent,
    AdmittedSelector,
    AuthorityEnvelope,
    IntentAdmissionError,
    admit_intent_claim,
)
from .target_grounding_ast import locations_for_source, symbol_parts
from .target_grounding_discovery import (
    DEFAULT_MAX_ENUMERATED_PATHS,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    discover_symbol_definitions,
)
from .target_grounding_model import (
    GroundedTarget,
    GroundedTargetSet,
    GroundingCandidate,
    GroundingError,
)
from .target_grounding_revalidation import (
    revalidate_grounded_target,
    revalidate_grounded_targets,
)


def _root_identity(root: Path) -> str:
    return hashlib.sha256(root.as_posix().encode("utf-8")).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _symbol_parts(value: str) -> tuple[str, ...]:
    return symbol_parts(value)


def _locations_for_source(
    source: bytes, symbol: str, resource: str
) -> tuple[tuple[int, int, int, int], ...]:
    return locations_for_source(source, symbol, resource)


def _freshness_token(
    *,
    resource: str,
    source_sha256: str | None,
    definition_line: int | None,
    definition_column: int | None,
) -> str:
    material = "|".join(
        (
            resource,
            source_sha256 or "<missing>",
            str(definition_line or 0),
            str(definition_column or 0),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _literal_candidate(
    selector: AdmittedSelector,
    envelope: AuthorityEnvelope,
    path: Path,
    resource: str,
    root_identity: str,
    max_source_bytes: int,
) -> tuple[GroundedTarget, ...]:
    # Confinement and read authority are separate checks.  The content read
    # must not happen until the concrete resource is inside trusted read scope.
    if not envelope.allows_read(resource):
        raise GroundingError("GROUNDING_READ_SCOPE_DENIED")
    try:
        source = path.read_bytes() if path.is_file() else None
    except OSError as exc:
        raise GroundingError("GROUNDING_LITERAL_READ_FAILED") from exc
    if source is not None and len(source) > max_source_bytes:
        raise GroundingError("GROUNDING_SOURCE_LIMIT")
    digest = _bytes_sha256(source) if source is not None else None
    return (
        GroundedTarget(
            selector.selector_id,
            resource,
            selector.kind,
            None,
            "literal-user-resource",
            tuple(item.name for item in envelope.read_resources),
            None,
            None,
            digest,
            root_identity,
            _freshness_token(
                resource=resource,
                source_sha256=digest,
                definition_line=None,
                definition_column=None,
            ),
            envelope.allows_write(resource),
        ),
    )


def _symbol_candidates(
    selector: AdmittedSelector,
    envelope: AuthorityEnvelope,
    root: Path,
    root_identity: str,
    max_files: int,
    max_source_bytes: int,
    max_total_source_bytes: int,
    max_enumerated_paths: int,
    max_candidates: int,
) -> list[GroundedTarget]:
    _symbol_parts(selector.value)
    candidates: list[GroundedTarget] = []
    definitions = discover_symbol_definitions(
        root,
        envelope,
        selector.value,
        max_files=max_files,
        max_source_bytes=max_source_bytes,
        max_total_source_bytes=max_total_source_bytes,
        max_enumerated_paths=max_enumerated_paths,
        max_candidates=max_candidates,
    )
    for definition in definitions:
        for line, column, _end_line, _end_column in definition.locations:
            candidates.append(
                GroundedTarget(
                    selector.selector_id,
                    definition.resource,
                    selector.kind,
                    selector.value,
                    "python-ast-unique-definition",
                    tuple(item.name for item in envelope.read_resources),
                    line,
                    column,
                    definition.source_sha256,
                    root_identity,
                    _freshness_token(
                        resource=definition.resource,
                        source_sha256=definition.source_sha256,
                        definition_line=line,
                        definition_column=column,
                    ),
                    envelope.allows_write(definition.resource),
                )
            )
            if len(candidates) > max_candidates:
                raise GroundingError("GROUNDING_CANDIDATE_LIMIT")
    return candidates


def _candidate_for_selector(
    selector: AdmittedSelector,
    *,
    envelope: AuthorityEnvelope,
    root: Path,
    root_identity: str,
    max_files: int,
    max_source_bytes: int,
    max_total_source_bytes: int,
    max_enumerated_paths: int,
    max_candidates: int,
) -> tuple[GroundedTarget, ...]:
    if selector.literal_resource is not None:
        resource = selector.literal_resource
        try:
            path = resolve_workspace_path(root, resource)
            assert_path_safe(path, directory=False)
        except (OSError, RuntimeError, ValueError, WorkspacePathError) as exc:
            raise GroundingError("GROUNDING_LITERAL_PATH_INVALID") from exc
        if selector.symbolic:
            raise GroundingError("GROUNDING_SELECTOR_KIND_INVALID")
        return _literal_candidate(
            selector,
            envelope,
            path,
            resource,
            root_identity,
            max_source_bytes,
        )

    if selector.kind != "symbol":
        raise GroundingError("GROUNDING_SELECTOR_KIND_INVALID")
    candidates = _symbol_candidates(
        selector,
        envelope,
        root,
        root_identity,
        max_files,
        max_source_bytes,
        max_total_source_bytes,
        max_enumerated_paths,
        max_candidates,
    )
    if not candidates:
        raise GroundingError("GROUNDING_NOT_FOUND")
    if len(candidates) != 1:
        raise GroundingError("GROUNDING_AMBIGUOUS")
    selected = candidates[0]
    return (selected,)


def ground_admitted_targets(
    intent: AdmittedIntent,
    envelope: AuthorityEnvelope,
    workspace_root: str | Path | None = None,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_source_bytes: int = 2_000_000,
    max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    max_enumerated_paths: int = DEFAULT_MAX_ENUMERATED_PATHS,
    max_candidates: int = 512,
) -> GroundedTargetSet:
    from .target_grounding_workflow import ground_admitted_targets as _ground

    return _ground(
        intent,
        envelope,
        workspace_root,
        max_files=max_files,
        max_source_bytes=max_source_bytes,
        max_total_source_bytes=max_total_source_bytes,
        max_enumerated_paths=max_enumerated_paths,
        max_candidates=max_candidates,
        candidate_for_selector=_candidate_for_selector,
        root_identity=_root_identity,
        freshness_token=_freshness_token,
    )


def ground_intent_claim(
    claim: IntentClaimV1,
    envelope: AuthorityEnvelope,
    *,
    current_subject: str,
    workspace_root: str | Path | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_source_bytes: int = 2_000_000,
    max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    max_enumerated_paths: int = DEFAULT_MAX_ENUMERATED_PATHS,
    max_candidates: int = 512,
) -> tuple[AdmittedIntent, GroundedTargetSet]:
    """Admit and ground one claim without allowing model path selection."""

    try:
        bind_current_subject_evidence(claim, current_subject)
        intent = admit_intent_claim(
            claim,
            envelope,
            current_subject=current_subject,
        )
    except IntentAdmissionError as exc:
        raise GroundingError("GROUNDING_INTENT_NOT_ADMITTED", str(exc)) from exc
    return intent, ground_admitted_targets(
        intent,
        envelope,
        workspace_root,
        max_files=max_files,
        max_source_bytes=max_source_bytes,
        max_total_source_bytes=max_total_source_bytes,
        max_enumerated_paths=max_enumerated_paths,
        max_candidates=max_candidates,
    )


__all__ = [
    "GroundedTarget",
    "GroundedTargetSet",
    "GroundingCandidate",
    "GroundingError",
    "ground_admitted_targets",
    "ground_intent_claim",
    "revalidate_grounded_target",
    "revalidate_grounded_targets",
]
