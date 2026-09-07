"""Streaming, canonical enumeration for bounded target discovery."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id
from agent.runtime.path_safety import WorkspacePathError, assert_path_safe

from .intent_admission import AuthorityEnvelope
from .target_grounding_limits import EXCLUDED_DIRECTORY_NAMES
from .target_grounding_model import GroundingError


def _authorized_roots(root: Path, envelope: AuthorityEnvelope) -> tuple[Path, ...]:
    scopes = sorted(
        {
            normalize_resource_id(item.name)
            for item in envelope.read_resources
            if normalize_resource_id(item.name) != "memory"
        },
        key=lambda item: (len(item), item),
    )
    candidates: list[Path] = []
    for scope in scopes:
        candidate = root if scope == WORKSPACE_RESOURCE else root / scope
        try:
            assert_path_safe(candidate)
            canonical = candidate.resolve()
        except (RuntimeError, ValueError, WorkspacePathError):
            continue
        except OSError as exc:
            raise GroundingError("GROUNDING_DISCOVERY_FAILED") from exc
        candidates.append(canonical)
    roots: list[Path] = []
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), item.as_posix())):
        if any(candidate == existing or candidate.is_relative_to(existing) for existing in roots):
            continue
        roots.append(candidate)
    return tuple(roots)


class _EnumerationWork:
    """Own the traversal work counter used by one source inventory."""

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.consumed = 0

    def observe(self) -> None:
        self.consumed += 1
        if self.consumed > self.maximum:
            raise GroundingError("GROUNDING_DISCOVERY_LIMIT")


def _directory_entries(
    directory: Path,
    observe: Callable[[], None],
) -> list[tuple[str, Path, bool, bool]]:
    """Collect at most the remaining bounded entries of one directory."""

    entries: list[tuple[str, Path, bool, bool]] = []
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                    if (
                        is_directory
                        and entry.name.casefold() in EXCLUDED_DIRECTORY_NAMES
                    ):
                        continue
                    # Observe before retaining the entry.  If the next entry
                    # exceeds the limit, no unbounded directory list can be
                    # materialized first.
                    observe()
                    is_file = (
                        False
                        if is_directory
                        else entry.is_file(follow_symlinks=False)
                    )
                    entries.append(
                        (entry.name, Path(entry.path), is_directory, is_file)
                    )
                except GroundingError:
                    raise
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    raise GroundingError("GROUNDING_DISCOVERY_FAILED") from exc
    except GroundingError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise GroundingError("GROUNDING_DISCOVERY_FAILED") from exc
    entries.sort(key=lambda item: item[0])
    return entries


def _walk_files(
    start_path: Path,
    *,
    observe: Callable[[], None] | None = None,
) -> Iterator[Path]:
    """Yield regular files through deterministic, entry-bounded scandir traversal."""

    record = observe or (lambda: None)

    def visit(directory: Path, *, count_root: bool) -> Iterator[Path]:
        if count_root:
            record()
        for _name, path, is_directory, is_file in _directory_entries(directory, record):
            if is_directory:
                yield from visit(path, count_root=False)
            elif is_file:
                yield path

    yield from visit(start_path, count_root=True)


def _candidate_paths(
    start: Path,
    *,
    observe: Callable[[], None] | None = None,
) -> Iterator[Path]:
    try:
        mode = start.stat().st_mode
    except FileNotFoundError:
        return iter(())
    except (OSError, RuntimeError, ValueError) as exc:
        raise GroundingError("GROUNDING_DISCOVERY_FAILED") from exc
    if stat.S_ISREG(mode):
        if observe is not None:
            observe()
        return iter((start,))
    if stat.S_ISDIR(mode):
        return _walk_files(start, observe=observe)
    return iter(())


def _inventory_paths(
    root: Path,
    envelope: AuthorityEnvelope,
    *,
    max_enumerated_paths: int,
) -> Iterator[Path]:
    """Yield paths while enforcing enumeration work at the point of discovery."""

    if max_enumerated_paths <= 0:
        raise GroundingError("GROUNDING_LIMIT_INVALID")
    seen: set[Path] = set()
    work = _EnumerationWork(max_enumerated_paths)
    for start in _authorized_roots(root, envelope):
        for candidate in _candidate_paths(start, observe=work.observe):
            canonical_candidate = candidate.absolute()
            if canonical_candidate in seen:
                continue
            seen.add(canonical_candidate)
            yield canonical_candidate


__all__ = ["_authorized_roots", "_inventory_paths"]
