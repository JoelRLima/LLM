import hashlib
from pathlib import Path

import pytest

from agent.code.changes import (
    ChangeConflictError,
    ChangeKind,
    ChangeSet,
    ChangeSetState,
    ChangeSetTransaction,
    FileChange,
    changeset_from_dict,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_changeset_applies_multiple_files_and_can_rollback(tmp_path: Path):
    original = "value = 1\n"
    (tmp_path / "existing.py").write_bytes(original.encode("utf-8"))
    change_set = ChangeSet(
        objective="alterar",
        changes=(
            FileChange("existing.py", ChangeKind.MODIFY, "value = 2\n", _hash(original)),
            FileChange("created.py", ChangeKind.CREATE, "created = True\n"),
        ),
    )
    transaction = ChangeSetTransaction(tmp_path, change_set)

    preview = transaction.prepare()
    transaction.commit()

    assert "-value = 1" in preview.diff
    assert "+value = 2" in preview.diff
    assert transaction.change_set.state == ChangeSetState.COMMITTED
    assert (tmp_path / "created.py").exists()

    transaction.rollback()
    assert (tmp_path / "existing.py").read_text(encoding="utf-8") == original
    assert not (tmp_path / "created.py").exists()


def test_changeset_rejects_stale_hash_and_path_escape(tmp_path: Path):
    (tmp_path / "file.py").write_text("current", encoding="utf-8")
    stale = ChangeSet(
        objective="stale",
        changes=(FileChange("file.py", ChangeKind.MODIFY, "new", "0" * 64),),
    )
    escaped = ChangeSet(
        objective="escape",
        changes=(FileChange("../outside.py", ChangeKind.CREATE, "bad"),),
    )

    with pytest.raises(ChangeConflictError, match="hash"):
        ChangeSetTransaction(tmp_path, stale).prepare()
    with pytest.raises(Exception, match="fora do projeto"):
        ChangeSetTransaction(tmp_path, escaped).prepare()


def test_changeset_parser_is_strict_about_duplicate_and_missing_content():
    with pytest.raises(Exception, match="exige conteúdo"):
        changeset_from_dict({"changes": [{"path": "x.py", "kind": "create"}]})
    with pytest.raises(Exception, match="repetido"):
        changeset_from_dict(
            {
                "changes": [
                    {"path": "x.py", "kind": "create", "content": "a"},
                    {"path": "x.py", "kind": "modify", "content": "b"},
                ]
            }
        )


def test_move_is_rolled_back(tmp_path: Path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    transaction = ChangeSetTransaction(
        tmp_path,
        ChangeSet(
            objective="move",
            changes=(FileChange("old.py", ChangeKind.MOVE, destination_path="pkg/new.py"),),
        ),
    )

    transaction.commit()
    assert (tmp_path / "pkg/new.py").exists()
    transaction.rollback()

    assert (tmp_path / "old.py").exists()
    assert not (tmp_path / "pkg/new.py").exists()


def test_changeset_rejects_aliases_and_reused_move_destination(tmp_path: Path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    aliased = ChangeSet(
        objective="alias",
        changes=(
            FileChange("new.py", ChangeKind.CREATE, "x = 1\n"),
            FileChange("folder/../new.py", ChangeKind.CREATE, "x = 2\n"),
        ),
    )
    reused_destination = ChangeSet(
        objective="destino",
        changes=(
            FileChange("new.py", ChangeKind.CREATE, "x = 1\n"),
            FileChange("old.py", ChangeKind.MOVE, destination_path="new.py"),
        ),
    )

    with pytest.raises(Exception, match="repetido"):
        ChangeSetTransaction(tmp_path, aliased).prepare()
    with pytest.raises(Exception, match="repetido"):
        ChangeSetTransaction(tmp_path, reused_destination).prepare()


def test_structured_edits_validate_anchors_and_apply_atomically(tmp_path: Path):
    original = "first\nsecond\nthird\n"
    (tmp_path / "module.py").write_bytes(original.encode("utf-8"))
    change_set = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "module.py",
                    "kind": "edit",
                    "base_hash": _hash(original),
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 2,
                            "end_line": 2,
                            "expected_text": "second\n",
                            "content": "changed\n",
                        },
                        {
                            "operation": "insert_after",
                            "start_line": 3,
                            "expected_text": "third\n",
                            "content": "fourth\n",
                        },
                    ],
                }
            ]
        }
    )

    transaction = ChangeSetTransaction(tmp_path, change_set)
    preview = transaction.prepare()
    transaction.commit()

    assert "+changed" in preview.diff
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == (
        "first\nchanged\nthird\nfourth\n"
    )


def test_structured_edit_rejects_stale_anchor(tmp_path: Path):
    (tmp_path / "module.py").write_text("current\n", encoding="utf-8")
    change_set = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "module.py",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "expected_text": "stale\n",
                            "content": "new\n",
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(ChangeConflictError, match="expected_text"):
        ChangeSetTransaction(tmp_path, change_set).prepare()


def test_commit_rechecks_snapshot_without_overwriting_external_change(tmp_path: Path):
    original = b"value = 0\n"
    external = b"value = 99\n"
    path = tmp_path / "module.py"
    path.write_bytes(original)
    transaction = ChangeSetTransaction(
        tmp_path,
        ChangeSet(
            objective="alterar",
            changes=(
                FileChange(
                    "module.py",
                    ChangeKind.MODIFY,
                    "value = 1\n",
                    hashlib.sha256(original).hexdigest(),
                ),
            ),
        ),
    )
    transaction.prepare()
    path.write_bytes(external)

    with pytest.raises(ChangeConflictError, match="mudou após o stage"):
        transaction.commit()

    assert path.read_bytes() == external
