"""Atomic staging, commit, validation, and rollback for ``ChangeSet``."""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from agent.code.change_models import (
    ChangeConflictError,
    ChangeKind,
    ChangePreview,
    ChangeSet,
    ChangeSetError,
    ChangeSetState,
    FileChange,
    content_hash,
)
from agent.code.change_parsing import apply_text_edits


class ChangeSetTransaction:
    """Apply managed file changes with atomic writes and best-effort rollback."""

    def __init__(self, root: str | Path, change_set: ChangeSet, max_backup_bytes: int = 10_000_000) -> None:
        self.root = Path(root).resolve()
        self.change_set = change_set
        self.max_backup_bytes = max_backup_bytes
        self._paths: Dict[str, Path] = {}
        self._backups: Dict[Path, Optional[bytes]] = {}
        self._staged_content: Dict[Path, bytes] = {}
        self._applied_paths: set[Path] = set()
        self._preview: Optional[ChangePreview] = None

    def _resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ChangeSetError(f"Caminho fora do projeto: {relative}") from exc
        return candidate

    @staticmethod
    def _reserve(path: Path, label: str, reserved: Dict[Path, str]) -> None:
        if path in reserved:
            raise ChangeSetError(f"Caminho repetido no ChangeSet: {label} (já usado por {reserved[path]}).")
        reserved[path] = label

    def _backup_source(self, change: FileChange, path: Path) -> bytes | None:
        exists = path.is_file()
        if change.kind == ChangeKind.CREATE and exists:
            raise ChangeConflictError(f"Arquivo já existe: {change.path}")
        if change.kind != ChangeKind.CREATE and not exists:
            raise ChangeConflictError(f"Arquivo não existe: {change.path}")
        before = path.read_bytes() if exists else None
        if change.base_hash and before is not None and content_hash(before) != change.base_hash:
            raise ChangeConflictError(f"Precondição de hash divergente: {change.path}")
        self._backups[path] = before
        return before

    def _stage_move(self, change: FileChange, reserved: Dict[Path, str], affected: list[str]) -> None:
        assert change.destination_path is not None
        destination = self._resolve(change.destination_path)
        self._reserve(destination, change.destination_path, reserved)
        if destination.exists():
            raise ChangeConflictError(f"Destino já existe: {change.destination_path}")
        self._paths[change.destination_path] = destination
        self._backups[destination] = None
        affected.append(change.destination_path)

    def _stage_content(self, change: FileChange, path: Path, before: bytes | None) -> tuple[str, str]:
        before_source = before.decode("utf-8", errors="replace") if before else ""
        if change.kind == ChangeKind.DELETE:
            after_source = ""
        elif change.kind == ChangeKind.EDIT:
            after_source = apply_text_edits(before_source, change.edits, change.path)
        else:
            after_source = change.content or ""
        if change.kind != ChangeKind.DELETE:
            self._staged_content[path] = after_source.encode("utf-8")
        return before_source, after_source

    @staticmethod
    def _diff(change: FileChange, before: str, after: str) -> list[str]:
        return list(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{change.path}",
            tofile=f"b/{change.path}",
        ))

    def prepare(self) -> ChangePreview:
        if self.change_set.state != ChangeSetState.PROPOSED:
            if self._preview is not None:
                return self._preview
            raise ChangeSetError(f"ChangeSet não pode ser preparado em estado {self.change_set.state}.")
        diffs: list[str] = []
        affected: list[str] = []
        reserved: Dict[Path, str] = {}
        backup_size = 0
        for change in self.change_set.changes:
            path = self._resolve(change.path)
            self._reserve(path, change.path, reserved)
            self._paths[change.path] = path
            before = self._backup_source(change, path)
            backup_size += len(before or b"")
            if backup_size > self.max_backup_bytes:
                raise ChangeSetError("ChangeSet excede o limite de backup transacional.")
            affected.append(change.path)
            if change.kind == ChangeKind.MOVE:
                self._stage_move(change, reserved, affected)
                continue
            before_source, after_source = self._stage_content(change, path, before)
            diffs.extend(self._diff(change, before_source, after_source))
        self.change_set = replace(self.change_set, state=ChangeSetState.STAGED)
        self._preview = ChangePreview(self.change_set.change_set_id, tuple(affected), "".join(diffs))
        return self._preview

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _validate_staged_snapshot(self) -> None:
        for change in self.change_set.changes:
            path = self._paths[change.path]
            expected = self._backups[path]
            changed = path.exists() if expected is None else not path.is_file() or path.read_bytes() != expected
            if changed:
                raise ChangeConflictError(f"Arquivo mudou após o stage: {change.path}")
            if change.kind == ChangeKind.MOVE:
                assert change.destination_path is not None
                if self._paths[change.destination_path].exists():
                    raise ChangeConflictError(f"Destino mudou após o stage: {change.destination_path}")

    def _apply_change(self, change: FileChange) -> None:
        path = self._paths[change.path]
        if change.kind in {ChangeKind.CREATE, ChangeKind.MODIFY, ChangeKind.EDIT}:
            self._atomic_write(path, self._staged_content[path])
            self._applied_paths.add(path)
        elif change.kind == ChangeKind.DELETE:
            path.unlink()
            self._applied_paths.add(path)
        else:
            assert change.destination_path is not None
            destination = self._paths[change.destination_path]
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, destination)
            self._applied_paths.update({path, destination})

    def commit(self) -> None:
        if self.change_set.state == ChangeSetState.PROPOSED:
            self.prepare()
        if self.change_set.state != ChangeSetState.STAGED:
            raise ChangeSetError(f"ChangeSet não pode ser aplicado em estado {self.change_set.state}.")
        self._validate_staged_snapshot()
        try:
            for change in self.change_set.changes:
                self._apply_change(change)
            self.change_set = replace(self.change_set, state=ChangeSetState.COMMITTED)
        except Exception as exc:
            self.rollback()
            raise ChangeSetError(f"Falha ao aplicar ChangeSet: {exc}") from exc

    def mark_validated(self) -> None:
        if self.change_set.state != ChangeSetState.COMMITTED:
            raise ChangeSetError("Somente ChangeSet aplicado pode ser validado.")
        self.change_set = replace(self.change_set, state=ChangeSetState.VALIDATED)

    def rollback(self) -> None:
        for path, content in reversed(tuple(self._backups.items())):
            if path not in self._applied_paths:
                continue
            if content is None and path.exists():
                path.unlink()
            elif content is not None:
                self._atomic_write(path, content)
        self._applied_paths.clear()
        self.change_set = replace(self.change_set, state=ChangeSetState.ROLLED_BACK)
