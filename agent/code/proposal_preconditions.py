"""Bind model-proposed edits to exact runtime observations."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from agent.code.change_models import ChangeKind, ChangeSet, ChangeSetError
from agent.code.change_parsing import observed_text_for_edit
from agent.code.context_selection import SelectedFile


def bind_observed_preconditions(
    change_set: ChangeSet,
    observed_files: Iterable[SelectedFile],
) -> ChangeSet:
    """Derive stale-write guards for the LLM proposal boundary only."""

    snapshots = {item.path: item for item in observed_files}
    changes = []
    for change in change_set.changes:
        # expected_text belongs only to structured text edits. Keep the
        # established contracts for whole-file modify/delete/move unchanged.
        if change.kind is not ChangeKind.EDIT:
            changes.append(change)
            continue
        observed = snapshots.get(change.path)
        if observed is None:
            raise ChangeSetError(
                f"Mudanca de '{change.path}' sem snapshot observado."
            )
        if observed.truncated:
            raise ChangeSetError(
                f"Snapshot observado de '{change.path}' truncado."
            )
        edits = tuple(
            replace(
                edit,
                expected_text=observed_text_for_edit(
                    observed.observed_text, edit, change.path
                ),
            )
            for edit in change.edits
        )
        changes.append(
            replace(
                change,
                base_hash=observed.content_hash,
                edits=edits,
            )
        )
    return replace(change_set, changes=tuple(changes))
