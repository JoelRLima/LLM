"""Adversarial rehearsal of selector-last installation in owned fixtures only.

This is not a live activator. A marker and absence of .git are mandatory, and
all installed proposal bytes still deny every grant. History is never edited.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Callable

from scripts.w23_authority import AuthorityError, _bindings, _object, _regular, _semantics, digest

MARKER = b"W23_DISPOSABLE_AUTHORITY_REHEARSAL_V1\n"
SELECTOR = ".agent-local/AUTHORITY_MANIFEST.json"


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise AuthorityError("reparse/symlink fixture rejected")
    return info.st_dev, info.st_ino


def _fixture(root: Path) -> tuple[int, int]:
    identity = _identity(root)
    if (root / ".git").exists() or (root / ".git").is_symlink():
        raise AuthorityError("refusing a live Git worktree")
    if _regular(root, ".w23-disposable-authority").read_bytes() != MARKER:
        raise AuthorityError("missing exclusive disposable fixture marker")
    return identity


def _destination(root: Path, relative: str) -> Path:
    current = root
    for component in relative.split("/"):
        current /= component
        if current.exists() or current.is_symlink():
            _identity(current)
    return current


def _rollback(created: list[tuple[Path, tuple[int, int], str]]) -> bool:
    confirmed = True
    for path, identity, expected in reversed(created):
        try:
            if _identity(path) != identity or digest(path.read_bytes()) != expected:
                confirmed = False
            else:
                path.unlink()
        except (OSError, AuthorityError):
            confirmed = False
    return confirmed


def _contents(bundle: Path, rows: list[dict[str, str]]) -> list[tuple[dict[str, str], bytes]]:
    result = []
    for row in rows:
        data = _regular(bundle, row["source"]).read_bytes()
        if digest(data) != row["sha256"]:
            raise AuthorityError("source changed before transaction")
        result.append((row, data))
    return result


def rehearse(root: Path, bundle: Path, raw: bytes, expected_sha256: str, *,
             after_write: Callable[[Path, int], None] | None = None) -> dict[str, Any]:
    """Exercise additive writes and rollback; only an owned fixture selector changes."""
    root_identity = _fixture(root)
    proposal = _object(raw, expected_sha256)
    _semantics(proposal)
    rows = _bindings(bundle, proposal)
    old = _regular(root, SELECTOR).read_bytes()
    if digest(old) != proposal["predecessor"]["sha256"]:
        raise AuthorityError("selector predecessor precondition differs")
    # Authenticate all inputs and absence of all destinations before the first write.
    contents = _contents(bundle, rows)
    for row, _data in contents:
        target = _destination(root, row["destination"])
        if target.exists() or target.is_symlink():
            raise AuthorityError("destination already exists")
    created: list[tuple[Path, tuple[int, int], str]] = []
    try:
        for index, (row, data) in enumerate(contents):
            target = _destination(root, row["destination"])
            target.parent.mkdir(parents=True, exist_ok=True)
            _destination(root, row["destination"])
            with target.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            created.append((target, _identity(target), row["sha256"]))
            if after_write is not None:
                after_write(target, index)
        _precommit(root, root_identity, old, created)
        # Fixture-only replacement is the final namespace operation. The contents
        # remain PROPOSED_INACTIVE; this is never an owner selection or activation.
        stage = root / ".agent-local/.w23-owned-selector-stage"
        with stage.open("xb") as stream:
            stream.write(raw)
        created.append((stage, _identity(stage), digest(raw)))
        _precommit(root, root_identity, old, created)
        os.replace(stage, root / SELECTOR)
    except BaseException as exc:
        if not _rollback(created):
            raise AuthorityError("ROLLBACK_UNCONFIRMED; foreign/modified bytes retained") from exc
        raise
    return {"state": "SIMULATED", "artifacts": len(rows), "selector_operation": "LAST",
            "activation_authorized": False, "selection_authorized": False, "live_activation_tested": False}


def _precommit(root: Path, identity: tuple[int, int], old: bytes,
               created: list[tuple[Path, tuple[int, int], str]]) -> None:
    if _fixture(root) != identity or _regular(root, SELECTOR).read_bytes() != old:
        raise AuthorityError("fixture or predecessor selector changed")
    for path, expected_identity, expected_digest in created:
        if _identity(path) != expected_identity or digest(path.read_bytes()) != expected_digest:
            raise AuthorityError("installed artifact identity changed before selector")
