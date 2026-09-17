"""Focused tests for post-audit W18 provenance sealing invariants."""

from __future__ import annotations

from typing import Any, cast

import pytest

from distribution.provenance import (
    ProvenanceError,
    assert_commit_tree_matches,
    seal_manifest_for_commit,
)
from distribution.release_manifest import candidate_id, make_manifest

BASE_COMMIT = "a" * 40
AUDITED_TREE = "b" * 40


def _manifest() -> dict[str, Any]:
    return make_manifest(
        source_base_commit=BASE_COMMIT,
        source_tree=AUDITED_TREE,
        wheel_sha256="1" * 64,
        runtime_lock_sha256="2" * 64,
        bootstrap_pip_lock_sha256="3" * 64,
    )


def test_sealing_requires_equal_real_commit_tree_and_preserves_candidate_id() -> None:
    document = _manifest()
    sealed = seal_manifest_for_commit(
        document,
        commit="c" * 40,
        commit_tree=AUDITED_TREE,
        audited_tree=AUDITED_TREE,
    )

    assert sealed["candidate_id"] == document["candidate_id"] == candidate_id(document)
    source = cast(dict[str, Any], sealed["source"])
    assert source == {
        "status": "committed_release",
        "base_commit": BASE_COMMIT,
        "commit": "c" * 40,
        "tree": AUDITED_TREE,
    }


def test_sealing_rejects_tree_mismatch_or_non_candidate_source() -> None:
    document = _manifest()

    with pytest.raises(ProvenanceError, match="differs"):
        assert_commit_tree_matches(AUDITED_TREE, "d" * 40)
    with pytest.raises(ProvenanceError, match="only an uncommitted"):
        seal_manifest_for_commit(
            {**document, "source": {**cast(dict[str, Any], document["source"]), "commit": "c" * 40}},
            commit="c" * 40,
            commit_tree=AUDITED_TREE,
            audited_tree=AUDITED_TREE,
        )
