from types import SimpleNamespace

import pytest

from agent.code.change_models import ChangeKind, ChangePreview, ChangeSet, FileChange
from agent.code.mutation_binding import (
    MutationBindingError,
    assert_changeset_admitted,
    assert_resources_subset,
)


def _service(targets):
    intent = SimpleNamespace(mutation_targets=tuple(targets))
    return SimpleNamespace(
        root=".",
        context=SimpleNamespace(metadata={"admitted_intent": intent}),
    )


def _change(path):
    return ChangeSet(
        objective="change",
        changes=(FileChange(path, ChangeKind.MODIFY, content="value = 2\n"),),
    )


def test_proposal_outside_admitted_set_is_rejected_before_commit() -> None:
    with pytest.raises(MutationBindingError, match="MUTATION_TARGET_OUTSIDE_ADMITTED"):
        assert_changeset_admitted(_service(("a.py",)), _change("b.py"))


def test_preview_targets_are_checked_against_same_admitted_set() -> None:
    service = _service(("a.py",))
    change_set = _change("a.py")
    preview = ChangePreview("id", ("a.py", "b.py"), "", True)
    with pytest.raises(MutationBindingError, match="MUTATION_TARGET_OUTSIDE_ADMITTED"):
        assert_changeset_admitted(service, change_set, preview=preview)


def test_no_authority_projection_preserves_legacy_compatibility_boundary() -> None:
    service = SimpleNamespace(root=".", context=SimpleNamespace(metadata={}))
    assert assert_changeset_admitted(service, _change("b.py")) is None


@pytest.mark.parametrize(
    ("admitted", "required", "allowed"),
    [
        (("src",), ("src/settings.py",), True),
        (("src/settings.py",), ("src",), False),
        (("src/settings.py",), ("src/other.py",), False),
        (("*",), ("src/settings.py",), True),
        (("src/settings.py",), ("*",), False),
    ],
)
def test_resource_binding_is_directional_subset(
    admitted: tuple[str, ...], required: tuple[str, ...], allowed: bool
) -> None:
    if allowed:
        assert assert_resources_subset(required, admitted) == required
    else:
        with pytest.raises(MutationBindingError, match="MUTATION_TARGET_OUTSIDE_ADMITTED"):
            assert_resources_subset(required, admitted)
