"""Immutable contracts and errors for transactional source changes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4


class ChangeKind(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"


class TextEditKind(str, Enum):
    REPLACE = "replace"
    INSERT_BEFORE = "insert_before"
    INSERT_AFTER = "insert_after"
    DELETE = "delete"


@dataclass(frozen=True)
class TextEdit:
    operation: TextEditKind
    start_line: int
    end_line: Optional[int] = None
    content: str = ""
    expected_text: Optional[str] = None


class ChangeSetState(str, Enum):
    PROPOSED = "proposed"
    STAGED = "staged"
    COMMITTED = "committed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class FileChange:
    path: str
    kind: ChangeKind
    content: Optional[str] = None
    base_hash: Optional[str] = None
    destination_path: Optional[str] = None
    edits: tuple[TextEdit, ...] = ()


@dataclass(frozen=True)
class ChangeSet:
    objective: str
    changes: tuple[FileChange, ...]
    base_snapshot: Optional[str] = None
    rationale: str = ""
    change_set_id: str = field(default_factory=lambda: uuid4().hex)
    state: ChangeSetState = ChangeSetState.PROPOSED


@dataclass(frozen=True)
class ChangePreview:
    change_set_id: str
    affected_files: tuple[str, ...]
    diff: str


class ChangeSetError(RuntimeError):
    pass


class ChangeConflictError(ChangeSetError):
    pass


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
