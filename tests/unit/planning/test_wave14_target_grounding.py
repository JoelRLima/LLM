from pathlib import Path

import pytest

from agent.planning.intent_admission import AuthorityEnvelope
from agent.planning.target_grounding import (
    GroundingError,
    ground_intent_claim,
    revalidate_grounded_targets,
)
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance
from tests.unit.interaction.test_wave14_intent_admission import _claim, _memory_claim


def _envelope(root: Path, *, read=("src",), write=("src",)) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        parent_permissions=frozenset({"read", "write", "validate"}),
        granted_effects=frozenset({"write"}),
        read_resources=tuple(
            ResourceAccess(item, ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED)
            for item in read
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write
        ),
        workspace_root=str(root),
    )


def test_unique_python_definition_is_grounded_and_revalidated(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 10\n", encoding="utf-8")

    admitted, grounded = ground_intent_claim(
        _claim(),
        _envelope(tmp_path),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )

    assert admitted.requires_grounding is True
    assert grounded.resources == ("src/settings.py",)
    assert grounded.mutation_targets == ("src/settings.py",)
    assert grounded.targets[0].provenance == "python-ast-unique-definition"
    assert revalidate_grounded_targets(grounded, tmp_path) is grounded


def test_multiple_definitions_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("TIMEOUT = 10\n", encoding="utf-8")
    (source / "b.py").write_text("TIMEOUT = 20\n", encoding="utf-8")

    with pytest.raises(GroundingError, match="GROUNDING_AMBIGUOUS"):
        ground_intent_claim(
            _claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
        )


def test_read_discovery_cannot_expand_narrow_write_scope(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 10\n", encoding="utf-8")

    with pytest.raises(GroundingError, match="GROUNDING_OUTSIDE_WRITE_SCOPE"):
        ground_intent_claim(
            _claim(),
            _envelope(tmp_path, read=("src",), write=("src/other",)),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
        )


def test_comments_and_strings_are_not_definition_candidates(tmp_path: Path) -> None:
    source = tmp_path / "src" / "only_text.py"
    source.parent.mkdir()
    source.write_text('# TIMEOUT = 10\ntext = "TIMEOUT = 20"\n', encoding="utf-8")

    with pytest.raises(GroundingError, match="GROUNDING_NOT_FOUND"):
        ground_intent_claim(
            _claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
        )


def test_changed_source_invalidates_grounding_before_write(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 10\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        _claim(),
        _envelope(tmp_path),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )

    source.write_text("TIMEOUT = 11\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_STALE"):
        revalidate_grounded_targets(grounded, tmp_path)


def test_symlink_escape_is_not_a_discovery_candidate(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    outside = tmp_path.parent / "wave14-outside-symbol.py"
    outside.write_text("TIMEOUT = 99\n", encoding="utf-8")
    link = source / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this environment")
    try:
        with pytest.raises(GroundingError, match="GROUNDING_NOT_FOUND"):
            ground_intent_claim(
                _claim(),
                _envelope(tmp_path),
                current_subject="change TIMEOUT",
                workspace_root=tmp_path,
            )
    finally:
        outside.unlink(missing_ok=True)


def test_memory_grounding_authorization_is_bound_to_explicit_write_scope(tmp_path: Path) -> None:
    envelope = AuthorityEnvelope(
        parent_permissions=frozenset({"read", "memory"}),
        granted_effects=frozenset({"memory_write"}),
        read_resources=(
            ResourceAccess("memory", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        ),
        write_resources=(
            ResourceAccess("memory", ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED),
        ),
        workspace_root=str(tmp_path),
    )
    admitted, grounded = ground_intent_claim(
        _memory_claim(),
        envelope,
        current_subject="remember this in memory",
        workspace_root=tmp_path,
    )

    assert admitted.mutation_targets == ("memory",)
    assert grounded.mutation_targets == ("memory",)
    assert grounded.targets[0].mutation_authorized is True
