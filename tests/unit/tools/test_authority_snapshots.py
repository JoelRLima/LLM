import pytest

from agent.tools.authority import (
    ApplicationAuthoritySnapshot,
    TaskAuthoritySnapshot,
    derive_effective_task_authority,
)
from agent.tools.workspace_extensions_resolver import ResolvedWorkspaceExtension, ResolvedWorkspaceExtensions


def _resolved(*entries: tuple[str, tuple[str, ...]]) -> ResolvedWorkspaceExtensions:
    return ResolvedWorkspaceExtensions(
        tuple(
            ResolvedWorkspaceExtension(
                extension_id=extension_id,
                enabled=True,
                configured_grants=grants,
                catalog_entry=None,
                catalog_presence="present",
                manifest_status="unchanged",
                required_capabilities=(),
                effective_grants=(),
                missing_grants=(),
                unused_grants=(),
                activation_status="ready",
            )
            for extension_id, grants in entries
        )
    )


def test_application_authority_captures_grants_by_extension_without_aliasing() -> None:
    snapshot = ApplicationAuthoritySnapshot.from_resolved(
        "workspace-id", _resolved(("alpha.extension", ("read",)), ("beta.extension", ()))
    )

    public = snapshot.extension_grants
    public["new.extension"] = frozenset({"write"})
    assert snapshot.extension_grants == {"alpha.extension": frozenset({"read"}), "beta.extension": frozenset()}
    assert snapshot.snapshot_id


def test_task_authority_distinguishes_absence_from_explicit_empty() -> None:
    assert derive_effective_task_authority(None, frozenset({"read"})) is None
    explicit = TaskAuthoritySnapshot()
    effective = derive_effective_task_authority(explicit, frozenset({"read"}))
    assert effective is not None
    assert effective.allowed_capabilities == frozenset()


def test_persona_only_restricts_explicit_task_authority() -> None:
    task = TaskAuthoritySnapshot(frozenset({"read", "write"}))
    effective = derive_effective_task_authority(task, frozenset({"read"}))
    assert effective is not None
    assert effective.allowed_capabilities == frozenset({"read"})


@pytest.mark.parametrize("bad", ["", "../workspace", "C:\\workspace"])
def test_workspace_identity_rejects_paths(bad: str) -> None:
    with pytest.raises(ValueError):
        ApplicationAuthoritySnapshot(workspace_id=bad)
