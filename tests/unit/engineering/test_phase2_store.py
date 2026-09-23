from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringPermission,
    EngineeringRequest,
    EngineeringRunResultV1,
    EngineeringStoreError,
    EngineeringWorkspaceContext,
    SourceRepositoryContext,
)
from agent.engineering.policy import EngineeringPreflight, preflight
from agent.engineering.registry import production_registry
from agent.engineering.store import (
    MAX_ENGINEERING_RUN_RECORD_BYTES,
    EngineeringRecordError,
    EngineeringRunStore,
    _bounded_document,
    canonical_document_text,
)
from agent.engineering.transactions import enforce_capacity
from agent.runtime.instance_lock import InstanceLock, InstanceLockError
from agent.runtime.paths import AppPaths
from agent.runtime.process_identity import OwnerStatus

CANDIDATE = "a" * 40 + ":" + "b" * 64 + ":" + "c" * 64


class Liveness:
    def __init__(self, status: object) -> None:
        self.status = status

    def check(self, pid: int, process_start_id: str | None) -> object:
        del pid, process_start_id
        return self.status


class SequencedLiveness:
    def __init__(self, statuses: list[OwnerStatus]) -> None:
        self.statuses = statuses

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        del pid, process_start_id
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


class CountingLiveness:
    def __init__(self, status: OwnerStatus) -> None:
        self.status = status
        self.calls = 0

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        del pid, process_start_id
        self.calls += 1
        return self.status


def ctx(tmp_path: Path) -> EngineeringExecutionContext:
    paths = AppPaths.discover(tmp_path / "home")
    paths.ensure_base_directories()
    return EngineeringExecutionContext(
        EngineeringCaller.AUTOMATION_HEADLESS,
        paths,
        None,
        SourceRepositoryContext(tmp_path, CANDIDATE),
        frozenset({EngineeringPermission.RUN_EXTERNAL, EngineeringPermission.USE_NETWORK}),
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        lambda: 4.0,
    )


def admission(context: EngineeringExecutionContext) -> EngineeringPreflight:
    value = preflight(
        EngineeringRequest("acceptance.installed-package", {}), context, production_registry(), backend_available=True
    )
    assert isinstance(value, EngineeringPreflight)
    return value


def workspace_context(paths: AppPaths, root: Path, workspace_id: str) -> EngineeringExecutionContext:
    return EngineeringExecutionContext(
        EngineeringCaller.MODEL_SAFE_AGENT,
        paths,
        EngineeringWorkspaceContext(workspace_id, root),
        None,
        frozenset(),
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        lambda: 4.0,
    )


def test_app_paths_exact_engineering_layout_and_hashed_resource(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "home")
    paths.ensure_base_directories()
    assert paths.engineering_runs_dir == paths.home_dir / "global" / "engineering" / "runs"
    assert paths.engineering_resource_lock_file("secret/path").name.startswith("resource-")
    assert "secret" not in paths.engineering_resource_lock_file("secret/path").name


def test_no_parent_creation_writer_and_lock_fail_closed(tmp_path: Path) -> None:
    from agent.memory.json_persistence import AtomicWriteError, write_text_atomic

    missing = tmp_path / "missing" / "value.json"
    with pytest.raises(AtomicWriteError):
        write_text_atomic(missing, "{}\n", create_parent=False)
    assert not missing.parent.exists()
    with pytest.raises(InstanceLockError):
        InstanceLock.create(missing, create_parent=False).acquire()
    assert not missing.parent.exists()


def test_canonical_round_trip_and_noncanonical_rejected(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    document = {"b": 2, "a": 1}
    path.write_text(canonical_document_text(document), encoding="utf-8", newline="")
    assert _bounded_document(path, 100)[0] == document
    path.write_text('{"b": 2, "a": 1}\n', encoding="utf-8", newline="")
    with pytest.raises(EngineeringRecordError):
        _bounded_document(path, 100)


def test_bounded_reader_rejects_over_limit_before_parse(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b"{" + b"x" * MAX_ENGINEERING_RUN_RECORD_BYTES)
    with pytest.raises(EngineeringRecordError):
        _bounded_document(path, MAX_ENGINEERING_RUN_RECORD_BYTES)


def test_foreign_run_entry_blocks_admission(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    (context.app_paths.engineering_runs_dir / "leftover.tmp").write_text("x", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        EngineeringRunStore().begin(admission(context), context)
    assert getattr(caught.value, "code", None) == "ENGINEERING_STORE_STATE_UNSAFE"


def test_active_publication_and_managed_guard_precede_result(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    store = EngineeringRunStore()
    active = store.begin(admission(context), context)
    assert context.app_paths.engineering_run_file(active.run_id).exists()
    store.publish_managed_guard(active, admission(context), context)
    assert context.app_paths.engineering_transition_file(active.run_id).exists()


def test_store_history_fails_closed_on_corrupt_canonical_run(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    bad = context.app_paths.engineering_run_file("engr-" + "1" * 32)
    bad.write_text("{}\n", encoding="utf-8")
    result = EngineeringRunStore().history(context)
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_RUN_RECORD_CORRUPT"


def test_store_end_to_end_terminal_removes_marker(tmp_path: Path) -> None:
    from agent.engineering.contracts import EngineeringTerminalStatus
    from agent.engineering.service import EngineeringTerminalIntent

    context = ctx(tmp_path)
    store = EngineeringRunStore()
    admitted = admission(context)
    active = store.begin(admitted, context)
    store.publish_managed_guard(active, admitted, context)
    result = store.finish(
        active, admitted, EngineeringTerminalIntent(EngineeringTerminalStatus.SUCCEEDED, (), {"ok": True}, ()), context
    )
    assert isinstance(result, EngineeringRunResultV1)
    assert result.persisted is True
    assert not context.app_paths.engineering_transition_file(active.run_id).exists()


def test_timestamp_shape_is_exact_and_lexically_ordered() -> None:
    from agent.engineering.contracts import canonical_utc

    first = canonical_utc(datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc))
    second = canonical_utc(datetime(2026, 1, 1, 0, 0, 0, 2, tzinfo=timezone.utc))
    assert len(first) == 27 and first < second


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (OwnerStatus.ALIVE, "ENGINEERING_RUN_ACTIVE"),
        (OwnerStatus.INDETERMINATE, "ENGINEERING_OWNER_LIVENESS_INDETERMINATE"),
    ],
)
def test_active_alive_and_indeterminate_remain_protected(tmp_path: Path, status: OwnerStatus, code: str) -> None:
    context = ctx(tmp_path)
    creator = EngineeringRunStore()
    active = creator.begin(admission(context), context)
    result = EngineeringRunStore(Liveness(status)).result(active.run_id, context)
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == code
    assert result.run is not None and result.run.phase.value == "active"


def test_dead_active_query_uses_transient_second_pass_and_duration_null(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    active = EngineeringRunStore().begin(admission(context), context)
    result = EngineeringRunStore(SequencedLiveness([OwnerStatus.DEAD, OwnerStatus.DEAD])).result(active.run_id, context)
    assert isinstance(result, EngineeringRunResultV1)
    assert result.status.value == "unverified"
    assert result.reason_codes == ("ENGINEERING_OWNER_DEAD",)
    assert result.duration_ms is None


@pytest.mark.parametrize(
    ("phase", "owner_status"),
    [
        ("terminal", OwnerStatus.ALIVE),
        ("active", OwnerStatus.ALIVE),
        ("active", OwnerStatus.INDETERMINATE),
        ("active", OwnerStatus.DEAD),
    ],
)
def test_model_safe_foreign_result_is_not_found_without_liveness_or_recovery(
    tmp_path: Path,
    phase: str,
    owner_status: OwnerStatus,
) -> None:
    from agent.engineering.contracts import EngineeringTerminalStatus
    from agent.engineering.registry import EngineeringTerminalIntent

    paths = AppPaths.discover(tmp_path / "home")
    paths.ensure_base_directories()
    foreign_context = workspace_context(paths, tmp_path / "foreign", "workspace-foreign")
    trusted_context = workspace_context(paths, tmp_path / "trusted", "workspace-trusted")
    foreign_admission = preflight(
        EngineeringRequest("health.offline", {}),
        foreign_context,
        production_registry(),
        backend_available=True,
    )
    assert isinstance(foreign_admission, EngineeringPreflight)

    creator = EngineeringRunStore()
    active = creator.begin(foreign_admission, foreign_context)
    if phase == "terminal":
        creator.finish(
            active,
            foreign_admission,
            EngineeringTerminalIntent(EngineeringTerminalStatus.SUCCEEDED, (), {}, ()),
            foreign_context,
        )
    run_path = paths.engineering_run_file(active.run_id)
    before = run_path.read_bytes()
    liveness = CountingLiveness(owner_status)

    result = EngineeringRunStore(liveness).result_for_workspace(active.run_id, trusted_context)

    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_RUN_NOT_FOUND"
    assert result.query_status.value == "blocked"
    assert liveness.calls == 0
    assert run_path.read_bytes() == before
    assert not paths.engineering_transition_file(active.run_id).exists()


@pytest.mark.parametrize("corruption", [b"not-json", b' {"run_id":"foreign"}\n'])
def test_scoped_corrupt_run_is_not_found_without_liveness_or_recovery(tmp_path: Path, corruption: bytes) -> None:
    paths = AppPaths.discover(tmp_path / "home")
    paths.ensure_base_directories()
    foreign_context = workspace_context(paths, tmp_path / "foreign", "workspace-foreign")
    trusted_context = workspace_context(paths, tmp_path / "trusted", "workspace-trusted")
    foreign_admission = preflight(
        EngineeringRequest("health.offline", {}),
        foreign_context,
        production_registry(),
        backend_available=True,
    )
    assert isinstance(foreign_admission, EngineeringPreflight)
    active = EngineeringRunStore().begin(foreign_admission, foreign_context)
    run_path = paths.engineering_run_file(active.run_id)
    run_path.write_bytes(corruption)
    liveness = CountingLiveness(OwnerStatus.DEAD)

    result = EngineeringRunStore(liveness).result_for_workspace(active.run_id, trusted_context)

    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_RUN_NOT_FOUND"
    assert liveness.calls == 0
    assert run_path.read_bytes() == corruption
    assert not paths.engineering_transition_file(active.run_id).exists()
    administrative = EngineeringRunStore().result(active.run_id, trusted_context)
    assert isinstance(administrative, EngineeringErrorV1)
    assert administrative.code == "ENGINEERING_RUN_RECORD_CORRUPT"


def test_liveness_change_dead_to_indeterminate_between_query_passes_stays_active(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    active = EngineeringRunStore().begin(admission(context), context)
    result = EngineeringRunStore(
        SequencedLiveness([OwnerStatus.DEAD, OwnerStatus.INDETERMINATE, OwnerStatus.INDETERMINATE])
    ).result(active.run_id, context)
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_OWNER_LIVENESS_INDETERMINATE"


def test_backend_indeterminate_guard_quarantines_matching_admission(tmp_path: Path) -> None:
    from agent.engineering.contracts import EngineeringTerminalStatus
    from agent.engineering.service import EngineeringStoreError, EngineeringTerminalIntent

    context = ctx(tmp_path)
    admitted = admission(context)
    store = EngineeringRunStore()
    active = store.begin(admitted, context)
    store.publish_managed_guard(active, admitted, context)
    store.finish(
        active,
        admitted,
        EngineeringTerminalIntent(
            EngineeringTerminalStatus.UNVERIFIED,
            ("ENGINEERING_BACKEND_STATE_INDETERMINATE",),
            {},
            (),
        ),
        context,
    )
    with pytest.raises(EngineeringStoreError) as caught:
        EngineeringRunStore().begin(admitted, context)
    assert caught.value.code == "ENGINEERING_BACKEND_STATE_INDETERMINATE"


def test_dead_recovery_lifecycle_failure_is_unverified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    context = ctx(tmp_path)
    active = EngineeringRunStore().begin(admission(context), context)
    monkeypatch.setattr(
        "agent.engineering.store.HomeLifecycleLease.begin_transient",
        lambda home: (_ for _ in ()).throw(RuntimeError("lease failed")),
    )
    result = EngineeringRunStore(Liveness(OwnerStatus.DEAD)).result(active.run_id, context)
    assert isinstance(result, EngineeringErrorV1)
    assert result.query_status.value == "unverified"
    assert result.code == "ENGINEERING_DURABLE_STATE_INDETERMINATE"


def test_corrupt_transition_marker_fails_closed_as_store_unsafe(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    store = EngineeringRunStore()
    active = store.begin(admission(context), context)
    marker = context.app_paths.engineering_transition_file(active.run_id)
    marker.write_text("{}\n", encoding="utf-8")
    result = EngineeringRunStore().history(context)
    assert isinstance(result, EngineeringErrorV1)
    assert result.query_status.value == "protocol_error"
    assert result.code == "ENGINEERING_STORE_STATE_UNSAFE"


def test_run_filename_content_mismatch_blocks_administrative_reads(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    active = EngineeringRunStore().begin(admission(context), context)
    path = context.app_paths.engineering_run_file(active.run_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["run_id"] = "engr-" + "f" * 32
    path.write_text(canonical_document_text(document), encoding="utf-8", newline="")
    before = path.read_bytes()

    for result in (EngineeringRunStore().result(active.run_id, context), EngineeringRunStore().history(context)):
        assert isinstance(result, EngineeringErrorV1)
        assert result.code == "ENGINEERING_RUN_RECORD_CORRUPT"
    assert path.read_bytes() == before


def test_run_filename_content_mismatch_is_hidden_from_foreign_workspace(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "home")
    paths.ensure_base_directories()
    foreign = workspace_context(paths, tmp_path / "foreign", "workspace-foreign")
    trusted = workspace_context(paths, tmp_path / "trusted", "workspace-trusted")
    admitted = preflight(EngineeringRequest("health.offline", {}), foreign, production_registry(), backend_available=True)
    assert isinstance(admitted, EngineeringPreflight)
    active = EngineeringRunStore().begin(admitted, foreign)
    path = paths.engineering_run_file(active.run_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["run_id"] = "engr-" + "f" * 32
    path.write_text(canonical_document_text(document), encoding="utf-8", newline="")
    before = path.read_bytes()
    liveness = CountingLiveness(OwnerStatus.DEAD)

    result = EngineeringRunStore(liveness).result_for_workspace(active.run_id, trusted)

    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_RUN_NOT_FOUND"
    assert liveness.calls == 0
    assert path.read_bytes() == before
    assert not paths.engineering_transition_file(active.run_id).exists()


def test_capacity_rejects_mismatched_run_before_eviction(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    store = EngineeringRunStore()
    admitted = admission(context)
    active = store.begin(admitted, context)
    store.publish_managed_guard(active, admitted, context)
    from agent.engineering.contracts import EngineeringTerminalStatus
    from agent.engineering.registry import EngineeringTerminalIntent

    store.finish(active, admitted, EngineeringTerminalIntent(EngineeringTerminalStatus.SUCCEEDED, (), {}, ()), context)
    path = context.app_paths.engineering_run_file(active.run_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["run_id"] = "engr-" + "f" * 32
    path.write_text(canonical_document_text(document), encoding="utf-8", newline="")
    before = path.read_bytes()
    with InstanceLock.create(context.app_paths.engineering_store_lock_file, create_parent=False):
        with pytest.raises(EngineeringStoreError) as caught:
            enforce_capacity(context.app_paths, Liveness(OwnerStatus.ALIVE))
    assert caught.value.code == "ENGINEERING_RUN_RECORD_CORRUPT"
    assert path.read_bytes() == before
    assert not context.app_paths.engineering_run_file(document["run_id"]).exists()


def test_transition_filename_content_mismatch_blocks_recovery(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    store = EngineeringRunStore()
    admitted = admission(context)
    active = store.begin(admitted, context)
    store.publish_managed_guard(active, admitted, context)
    run_path = context.app_paths.engineering_run_file(active.run_id)
    before_run = run_path.read_bytes()
    marker_path = context.app_paths.engineering_transition_file(active.run_id)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["run_id"] = "engr-" + "f" * 32
    marker_path.write_text(canonical_document_text(marker), encoding="utf-8", newline="")
    before_marker = marker_path.read_bytes()
    liveness = CountingLiveness(OwnerStatus.DEAD)

    result = EngineeringRunStore(liveness).history(context)

    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_STORE_STATE_UNSAFE"
    assert liveness.calls == 0
    assert run_path.read_bytes() == before_run
    assert marker_path.read_bytes() == before_marker
    assert not context.app_paths.engineering_run_file(marker["run_id"]).exists()
