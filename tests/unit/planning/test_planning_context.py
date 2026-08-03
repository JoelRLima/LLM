import pytest

from agent.planning.planning_context import PlanningContextError, build_planning_context
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import ToolDescriptor, ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


class _Registry:
    def __init__(
        self,
        descriptors: tuple[ToolDescriptor, ...],
        identity: RuntimeSnapshotIdentity | None,
        *,
        frozen: bool = True,
    ) -> None:
        self._descriptors = descriptors
        self.frozen = frozen
        self.runtime_identity = identity

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors


def _builtin() -> ToolDescriptor:
    return ToolDescriptor("builtin", "builtin", schema={"type": "object"})


def _extension(name: str = "external") -> ToolDescriptor:
    return ToolDescriptor(
        name,
        "external data",
        schema={"properties": {"value": {"type": "string"}}},
        capabilities=frozenset({"read"}),
        origin_kind=ToolOriginKind.EXTENSION,
        extension_id="scanner.extension",
        adapter_id="scanner.extension",
    )


def test_builtin_remains_visible_without_task_authority() -> None:
    identity = RuntimeSnapshotIdentity.create("workspace")
    context = build_planning_context(
        _Registry((_builtin(),), identity),
        ApplicationAuthoritySnapshot(runtime_identity=identity),
        None,
        None,
    )
    assert context.eligible_names == frozenset({"builtin"})


def test_extension_requires_explicit_task_authority_and_matching_grant() -> None:
    identity = RuntimeSnapshotIdentity.create("workspace")
    registry = _Registry((_builtin(), _extension()), identity)
    authority = ApplicationAuthoritySnapshot(
        runtime_identity=identity, extension_grants={"scanner.extension": frozenset({"read"})}
    )
    without_task = build_planning_context(registry, authority, None, frozenset({"read"}))
    assert without_task.eligible_names == frozenset({"builtin"})
    with_task = build_planning_context(
        registry, authority, TaskAuthoritySnapshot(frozenset({"read"})), frozenset({"read"})
    )
    assert with_task.eligible_names == frozenset({"builtin", "external"})


def test_zero_capability_extension_requires_explicit_task_snapshot() -> None:
    identity = RuntimeSnapshotIdentity.create("workspace")
    descriptor = ToolDescriptor(
        "zero", "zero", origin_kind=ToolOriginKind.EXTENSION, extension_id="zero.extension", adapter_id="zero.extension"
    )
    authority = ApplicationAuthoritySnapshot(runtime_identity=identity, extension_grants={"zero.extension": ()})
    registry = _Registry((descriptor,), identity)
    assert build_planning_context(registry, authority, None, frozenset()).eligible_names == frozenset()
    explicit = build_planning_context(registry, authority, TaskAuthoritySnapshot(), frozenset())
    assert explicit.eligible_names == frozenset({"zero"})


def test_schema_is_defensively_copied_and_names_are_consistent() -> None:
    identity = RuntimeSnapshotIdentity.create("workspace")
    registry = _Registry((_builtin(),), identity)
    authority = ApplicationAuthoritySnapshot(runtime_identity=identity)
    context = build_planning_context(registry, authority, None, None)
    first = context.tools[0].input_schema
    first["injected"] = True
    assert "injected" not in context.tools[0].input_schema
    assert context.eligible_names == frozenset(tool.name for tool in context.tools)


def test_extension_without_canonical_origin_information_fails_closed() -> None:
    descriptor = ToolDescriptor("bad", "bad", adapter_id="external")
    identity = RuntimeSnapshotIdentity.create("workspace")
    with pytest.raises(PlanningContextError):
        build_planning_context(_Registry((descriptor,), identity), ApplicationAuthoritySnapshot(runtime_identity=identity), None, None)


def test_authority_workspace_mismatch_fails_closed() -> None:
    registry_identity = RuntimeSnapshotIdentity.create("workspace-a")
    authority_identity = RuntimeSnapshotIdentity.create("workspace-b")
    with pytest.raises(PlanningContextError):
        build_planning_context(
            _Registry((_builtin(),), registry_identity),
            ApplicationAuthoritySnapshot(runtime_identity=authority_identity),
            None,
            None,
        )


def test_unfrozen_registry_is_rejected_before_projection() -> None:
    identity = RuntimeSnapshotIdentity.create("workspace")
    registry = _Registry((_builtin(),), identity, frozen=False)
    with pytest.raises(PlanningContextError):
        build_planning_context(registry, ApplicationAuthoritySnapshot(runtime_identity=identity), None, None)


def test_registry_without_runtime_identity_is_rejected() -> None:
    registry = _Registry((_builtin(),), None)
    with pytest.raises(PlanningContextError):
        build_planning_context(registry, ApplicationAuthoritySnapshot(workspace_id="workspace"), None, None)
