"""Safe, bounded discovery of applicable project guidance."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agent.llm.context_projection import (
    MAX_GUIDANCE_FILE_CHARS,
    MAX_GUIDANCE_FILES,
    MAX_GUIDANCE_TOTAL_CHARS,
    UNTRUSTED_PROJECT_GUIDANCE,
    ProjectGuidanceProjection,
)
from agent.runtime.path_safety import (
    WorkspacePathError,
    assert_no_link_ancestors,
    assert_path_safe,
    resolve_workspace_path,
)

from .context_projection_codec import record_from_mapping


@dataclass(frozen=True, slots=True)
class _GuidanceCandidate:
    path: str
    scope_directory: str
    content: str
    content_hash: str
    target_lineages: tuple[str, ...]


def _scope_depth(scope_directory: str) -> int:
    return 0 if scope_directory == "." else len(scope_directory.split("/"))


def _target_directory(root: Path, target: str | Path) -> tuple[str, Path] | None:
    try:
        resolved = resolve_workspace_path(root, target)
        assert_no_link_ancestors(resolved)
        if resolved.is_file():
            directory = resolved.parent
        elif resolved.is_dir():
            directory = resolved
        else:
            return None
        relative = directory.relative_to(root).as_posix() or "."
        return relative, directory
    except (OSError, RuntimeError, ValueError, WorkspacePathError):
        return None


def _candidate_for_directory(
    root: Path,
    directory: Path,
    target_lineages: Sequence[str],
) -> _GuidanceCandidate | None:
    candidate = directory / "AGENTS.md"
    try:
        assert_no_link_ancestors(candidate)
        assert_path_safe(candidate, directory=False)
        resolved = resolve_workspace_path(root, candidate, require_file=True)
        if resolved != candidate:
            return None
        raw = candidate.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, RuntimeError, ValueError, WorkspacePathError):
        return None
    scope = directory.relative_to(root).as_posix() or "."
    return _GuidanceCandidate(
        path=candidate.relative_to(root).as_posix(),
        scope_directory=scope,
        content=content,
        content_hash=hashlib.sha256(raw).hexdigest(),
        target_lineages=tuple(sorted(set(target_lineages))),
    )


def _grounded_target_directories(
    workspace: Path,
    selected_targets: Sequence[str | Path],
) -> list[tuple[str, Path]]:
    target_directories = [
        grounded
        for target in selected_targets
        if (grounded := _target_directory(workspace, target)) is not None
    ]
    return target_directories or [(".", workspace)]


def _ancestor_lineage(directory: Path, workspace: Path) -> tuple[Path, ...]:
    current = directory
    lineage: list[Path] = []
    while True:
        lineage.append(current)
        if current == workspace:
            return tuple(lineage)
        current = current.parent


def _collect_guidance_candidates(
    workspace: Path,
    target_directories: Sequence[tuple[str, Path]],
) -> tuple[dict[str, _GuidanceCandidate], int]:
    by_path: dict[str, _GuidanceCandidate] = {}
    rejected_count = 0
    for target_relative, directory in sorted(
        set(target_directories), key=lambda item: item[0]
    ):
        for ancestor in _ancestor_lineage(directory, workspace):
            candidate_path = ancestor / "AGENTS.md"
            try:
                if candidate_path.exists() and not candidate_path.is_file():
                    rejected_count += 1
                    continue
            except OSError:
                rejected_count += 1
                continue
            candidate = _candidate_for_directory(workspace, ancestor, (target_relative,))
            if candidate is None:
                if candidate_path.exists():
                    rejected_count += 1
                continue
            previous = by_path.get(candidate.path)
            if previous is None:
                by_path[candidate.path] = candidate
            else:
                by_path[candidate.path] = replace(
                    previous,
                    target_lineages=tuple(
                        sorted(set(previous.target_lineages) | set(candidate.target_lineages))
                    ),
                )
    return by_path, rejected_count


def _nearest_for_target(
    candidates: Sequence[_GuidanceCandidate],
    target_relative: str,
) -> _GuidanceCandidate | None:
    matching = [
        item
        for item in candidates
        if item.scope_directory != "."
        and (
            target_relative == item.scope_directory
            or target_relative.startswith(item.scope_directory + "/")
        )
    ]
    return (
        sorted(
            matching,
            key=lambda item: (-_scope_depth(item.scope_directory), item.path),
        )[0]
        if matching
        else None
    )


def _choose_guidance_candidates(
    candidates: Sequence[_GuidanceCandidate],
    target_directories: Sequence[tuple[str, Path]],
) -> list[_GuidanceCandidate]:
    chosen: list[_GuidanceCandidate] = []
    root_candidate = next(
        (item for item in candidates if item.scope_directory == "."),
        None,
    )
    if root_candidate is not None:
        chosen.append(root_candidate)
    for target_relative, _ in sorted(set(target_directories), key=lambda item: item[0]):
        if len(chosen) >= MAX_GUIDANCE_FILES:
            break
        nearest = _nearest_for_target(candidates, target_relative)
        if nearest is not None and nearest not in chosen:
            chosen.append(nearest)
    for candidate in sorted(
        candidates,
        key=lambda item: (-_scope_depth(item.scope_directory), item.path),
    ):
        if len(chosen) >= MAX_GUIDANCE_FILES:
            break
        if candidate not in chosen:
            chosen.append(candidate)
    chosen.sort(key=lambda item: (_scope_depth(item.scope_directory), item.path))
    return chosen


def _guidance_records(
    chosen: Sequence[_GuidanceCandidate],
) -> list[Any]:
    remaining = MAX_GUIDANCE_TOTAL_CHARS
    records = []
    for index, candidate in enumerate(chosen):
        remaining_records = len(chosen) - index
        share = remaining // remaining_records if remaining_records else 0
        allowance = min(MAX_GUIDANCE_FILE_CHARS, share)
        content = candidate.content[:allowance]
        truncated = len(content) < len(candidate.content)
        remaining -= len(content)
        records.append(
            record_from_mapping(
                f"guidance:{candidate.path}",
                "project_guidance",
                {
                    "path": candidate.path,
                    "scope_directory": candidate.scope_directory,
                    "content": content,
                    "truncated": truncated,
                    "complete": not truncated,
                },
                trust_class=UNTRUSTED_PROJECT_GUIDANCE,
                reason="safe applicable ancestor AGENTS.md",
                identity=candidate.content_hash,
                freshness="CURRENT_WORKSPACE_BYTES",
                truncated=truncated,
                complete=not truncated,
            )
        )
    return records


def discover_project_guidance(
    root: str | Path,
    targets: Sequence[str | Path] = (),
    *,
    target_files: Sequence[str | Path] | None = None,
) -> ProjectGuidanceProjection:
    """Discover only safe ancestor AGENTS.md files for grounded targets."""

    workspace = Path(root).expanduser().resolve()
    selected_targets = tuple(targets if target_files is None else target_files)
    target_directories = _grounded_target_directories(workspace, selected_targets)
    by_path, rejected_count = _collect_guidance_candidates(
        workspace,
        target_directories,
    )
    candidates = sorted(
        by_path.values(),
        key=lambda item: (item.path, item.scope_directory),
    )
    chosen = _choose_guidance_candidates(candidates, target_directories)
    records = _guidance_records(chosen)
    omitted_count = max(0, len(candidates) - len(records))
    file_set_complete = omitted_count == 0
    content_complete = file_set_complete and all(record.complete for record in records)
    return ProjectGuidanceProjection(
        records=tuple(records),
        applicable_count=len(candidates),
        included_count=len(records),
        omitted_count=omitted_count,
        file_set_complete=file_set_complete,
        content_complete=content_complete,
        coverage_complete=content_complete,
        rejected_count=rejected_count,
    )

__all__ = ["discover_project_guidance"]
