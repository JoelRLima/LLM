import hashlib

import pytest

from agent.code.change_models import ChangeConflictError, ChangeSetError
from agent.code.change_parsing import changeset_from_dict
from agent.code.change_transaction import ChangeSetTransaction
from agent.code.context_selection import SelectedFile
from agent.code.proposal_preconditions import bind_observed_preconditions


def _snapshot(path: str, source: str, *, truncated: bool = False) -> SelectedFile:
    return SelectedFile(
        path=path,
        score=100,
        reasons=("target explícito",),
        content_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        observed_text=source,
        truncated=truncated,
    )


def _edit(path: str, start: int, end: int, *, expected: str = "inventado"):
    return changeset_from_dict(
        {
            "changes": [
                {
                    "path": path,
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": start,
                            "end_line": end,
                            "content": "novo",
                            "expected_text": expected,
                        }
                    ],
                }
            ]
        }
    )


@pytest.mark.parametrize(
    ("source", "start", "end", "expected"),
    [
        ("original", 1, 1, "original"),
        ("original\n", 1, 1, "original\n"),
        ("primeira\r\nsegunda\r\n", 1, 1, "primeira\r\n"),
        ("primeira\nsegunda\nterceira", 1, 2, "primeira\nsegunda\n"),
    ],
)
def test_edit_preconditions_are_derived_from_exact_observed_snapshot(
    source: str,
    start: int,
    end: int,
    expected: str,
) -> None:
    snapshot = _snapshot("controle.txt", source)

    bound = bind_observed_preconditions(
        _edit("controle.txt", start, end),
        (snapshot,),
    )

    change = bound.changes[0]
    assert change.base_hash == snapshot.content_hash
    assert change.edits[0].expected_text == expected


def test_binding_preserves_model_range_instead_of_correcting_semantic_intent() -> None:
    snapshot = _snapshot("controle.txt", "primeira\nsegunda\n")

    bound = bind_observed_preconditions(
        _edit("controle.txt", 2, 2),
        (snapshot,),
    )

    edit = bound.changes[0].edits[0]
    assert (edit.start_line, edit.end_line) == (2, 2)
    assert edit.expected_text == "segunda\n"


def test_binding_fails_closed_for_invalid_unselected_or_truncated_ranges() -> None:
    snapshot = _snapshot("controle.txt", "original")
    with pytest.raises(ChangeConflictError, match="fora do arquivo"):
        bind_observed_preconditions(_edit("controle.txt", 1, 2), (snapshot,))

    with pytest.raises(ChangeSetError, match="sem snapshot"):
        bind_observed_preconditions(_edit("outro.txt", 1, 1), (snapshot,))

    with pytest.raises(ChangeSetError, match="truncado"):
        bind_observed_preconditions(
            _edit("controle.txt", 1, 1),
            (_snapshot("controle.txt", "original", truncated=True),),
        )


def test_multiple_edits_receive_independent_exact_anchors() -> None:
    snapshot = _snapshot("controle.txt", "um\ndois\ntres\n")
    proposal = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "controle.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "UM\n",
                        },
                        {
                            "operation": "delete",
                            "start_line": 3,
                            "end_line": 3,
                        },
                    ],
                }
            ]
        }
    )

    bound = bind_observed_preconditions(proposal, (snapshot,))

    assert [item.expected_text for item in bound.changes[0].edits] == [
        "um\n",
        "tres\n",
    ]


def test_change_after_observation_is_rejected_before_staging(tmp_path) -> None:
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")
    snapshot = _snapshot("controle.txt", "original")
    bound = bind_observed_preconditions(_edit("controle.txt", 1, 1), (snapshot,))
    target.write_text("mudança externa", encoding="utf-8")

    with pytest.raises(ChangeConflictError, match="hash divergente"):
        ChangeSetTransaction(tmp_path, bound).prepare()

    assert target.read_text(encoding="utf-8") == "mudança externa"


@pytest.mark.parametrize(
    ("source", "operation", "expected"),
    [
        ("primeira\nsegunda", "insert_before", "primeira\n"),
        ("primeira\nsegunda", "insert_after", "primeira\n"),
        ("", "insert_before", ""),
    ],
)
def test_insert_anchors_preserve_existing_bounds(
    source: str,
    operation: str,
    expected: str,
) -> None:
    snapshot = _snapshot("controle.txt", source)
    proposal = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "controle.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": operation,
                            "start_line": 1,
                            "content": "novo\n",
                        }
                    ],
                }
            ]
        }
    )

    bound = bind_observed_preconditions(proposal, (snapshot,))

    assert bound.changes[0].edits[0].expected_text == expected


def test_multiple_files_bind_to_their_own_snapshots() -> None:
    first = _snapshot("a.txt", "A\n")
    second = _snapshot("b.txt", "B")
    proposal = changeset_from_dict(
        {
            "changes": [
                {
                    "path": "a.txt",
                    "kind": "edit",
                    "edits": [
                        {"operation": "delete", "start_line": 1, "end_line": 1}
                    ],
                },
                {
                    "path": "b.txt",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "novo",
                        }
                    ],
                },
            ]
        }
    )

    bound = bind_observed_preconditions(proposal, (first, second))

    assert bound.changes[0].edits[0].expected_text == "A\n"
    assert bound.changes[1].edits[0].expected_text == "B"
    assert bound.changes[0].base_hash == first.content_hash
    assert bound.changes[1].base_hash == second.content_hash
