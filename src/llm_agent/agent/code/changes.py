"""Canonical public aggregation for transactional source changes."""

from llm_agent.agent.code.change_models import (
    ChangeConflictError,
    ChangeKind,
    ChangePreview,
    ChangeSet,
    ChangeSetError,
    ChangeSetState,
    FileChange,
    TextEdit,
    TextEditKind,
    content_hash,
)
from llm_agent.agent.code.change_parsing import apply_text_edits, changeset_from_dict
from llm_agent.agent.code.change_transaction import ChangeSetTransaction

__all__ = [
    "ChangeConflictError",
    "ChangeKind",
    "ChangePreview",
    "ChangeSet",
    "ChangeSetError",
    "ChangeSetState",
    "ChangeSetTransaction",
    "FileChange",
    "TextEdit",
    "TextEditKind",
    "apply_text_edits",
    "changeset_from_dict",
    "content_hash",
]
