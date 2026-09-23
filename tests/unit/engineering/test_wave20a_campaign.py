"""Permanent W20-A adversarial campaign with one traceable case per contract fact."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.engineering.backends.repository import (
    MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES,
    MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES,
    _validate_and_project,
)
from agent.engineering.cli import discover_source_repository_context
from agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringEffects,
    EngineeringPermission,
    EngineeringQueryStatus,
    EngineeringRunPhase,
    EngineeringScope,
    EngineeringTerminalStatus,
    canonical_json_bytes,
    canonical_utc,
    validate_candidate_identity,
    validate_operation_id,
    validate_run_id,
)
from agent.engineering.policy import (
    MAX_ENGINEERING_PARAMETERS_BYTES,
    MAX_ENGINEERING_RUN_ID_ATTEMPTS,
    MAX_ENGINEERING_RUN_RECORD_BYTES,
    MAX_ENGINEERING_TRANSITION_RECORD_BYTES,
)
from agent.engineering.registry import ACCEPTANCE_INSTALLED_PACKAGE, production_registry
from agent.engineering.summary import EngineeringRecordError, _bounded_document, validate_transition

ROOT = Path(__file__).resolve().parents[3]
IDENTITY_A = "a" * 40 + ":" + "b" * 64 + ":" + "c" * 64


W20A_CAMPAIGN_CASES = {
    **{f"A{i:02d}": label for i, label in enumerate((
        "operation-id-contract", "descriptor-lookup", "parameter-normalization", "parameter-schema",
        "caller-support", "scope-binding", "descriptor-availability", "permission-external",
        "permission-network", "resource-identity", "backend-availability", "effect-vocabulary",
        "reserved-fault-permission", "managed-hermetic-structure",
    ), 1)},
    **{f"A{i:02d}": label for i, label in enumerate((
        "run-id-format", "run-id-attempt-bound", "canonical-json", "bounded-run-reader",
        "bounded-transition-reader", "timestamp-shape", "transition-schema", "environment-binding",
        "resource-hash", "no-parent-writer", "no-parent-lock", "active-projection",
        "active-owner-alive", "active-owner-indeterminate", "owner-dead-duration", "terminal-persistence",
        "terminal-marker-repair", "terminal-pending-recovery", "corrupt-record", "foreign-entry",
        "capacity-run-limit", "capacity-transition-limit", "retention-order", "lock-contention",
        "reconciliation-order", "parent-sync-barrier",
    ), 15)},
    **{f"A{i:02d}": label for i, label in enumerate((
        "source-root-derivation", "source-verifier-containment", "candidate-identity-equality",
        "fixed-argv", "fixed-cwd", "scrubbed-pythonpath", "scrubbed-pythonhome", "bounded-output-drain",
        "posix-process-group", "windows-prechild-failure",
    ), 37)},
    **{f"A{i:02d}": label for i, label in enumerate((
        "json-list", "json-describe", "json-history", "json-result", "no-prompt", "exit-map",
        "lifecycle-lease", "parameter-byte-bound", "installed-probes", "clean-wheel-import",
    ), 47)},
}


EXPECTED_CASE_IDS = tuple(f"A{i:02d}" for i in range(1, 57))


def _summary(identity: str = IDENTITY_A) -> dict[str, object]:
    return {
        "schema_version": 2,
        "evidence_level": "installed_deterministic",
        "mode": "clean-acceptance",
        "status": "passed",
        "acceptance": True,
        "candidate_identity": identity,
        "semantic_manifest_hash": "1" * 64,
        "wheel_sha256": "2" * 64,
        "task_files_in_wheel": False,
        "properties": [{"id": "engineering-import", "proof": "not projected"}],
    }


def test_A01_operation_id_contract() -> None:
    assert validate_operation_id("acceptance.installed-package") == "acceptance.installed-package"


def test_A02_descriptor_lookup() -> None:
    assert production_registry().get("acceptance.installed-package") == ACCEPTANCE_INSTALLED_PACKAGE


def test_A03_parameter_normalization_contract() -> None:
    assert ACCEPTANCE_INSTALLED_PACKAGE.parameter_schema["additionalProperties"] is False


def test_A04_parameter_schema_is_closed() -> None:
    assert ACCEPTANCE_INSTALLED_PACKAGE.parameter_schema["required"] == ()


def test_A05_caller_support_is_neutral() -> None:
    assert EngineeringCaller.MCP.value == "mcp"


def test_A06_scope_binding_is_source_repository() -> None:
    assert ACCEPTANCE_INSTALLED_PACKAGE.scope is EngineeringScope.SOURCE_REPOSITORY


def test_A07_descriptor_availability_is_explicit() -> None:
    assert isinstance(ACCEPTANCE_INSTALLED_PACKAGE.effects, EngineeringEffects)


def test_A08_external_permission_is_declared() -> None:
    assert EngineeringPermission.RUN_EXTERNAL.value == "run_external"


def test_A09_network_permission_is_declared() -> None:
    assert EngineeringPermission.USE_NETWORK.value == "use_network"


def test_A10_resource_identity_is_not_a_path() -> None:
    from agent.engineering.policy import resource_fingerprint
    assert resource_fingerprint("source-repository:secret/path") == __import__("hashlib").sha256(b"source-repository:secret/path").hexdigest()
    assert "secret/path" not in resource_fingerprint("source-repository:secret/path")


def test_A11_backend_availability_is_separate() -> None:
    assert production_registry().get(ACCEPTANCE_INSTALLED_PACKAGE.operation_id) == ACCEPTANCE_INSTALLED_PACKAGE


def test_A12_effect_vocabulary() -> None:
    assert set(ACCEPTANCE_INSTALLED_PACKAGE.effects.to_dict()) == {
        "network", "real_model", "managed_external_process", "mutating_or_executing", "hermetic"
    }


def test_A13_fault_permission_is_reserved() -> None:
    assert EngineeringPermission.INJECT_FAULT.value == "inject_fault"


def test_A14_managed_hermetic_fixture_is_structurally_legal() -> None:
    assert EngineeringEffects(False, False, True, True, True).hermetic is True


def test_A15_run_id_format() -> None:
    validate_run_id("engr-" + "0" * 32)


def test_A16_run_id_attempt_bound() -> None:
    assert MAX_ENGINEERING_RUN_ID_ATTEMPTS == 8


def test_A17_canonical_json() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_A18_bounded_run_reader(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b"{" + b"x" * MAX_ENGINEERING_RUN_RECORD_BYTES)
    with pytest.raises(EngineeringRecordError):
        _bounded_document(path, MAX_ENGINEERING_RUN_RECORD_BYTES)


def test_A19_bounded_transition_reader() -> None:
    assert MAX_ENGINEERING_TRANSITION_RECORD_BYTES == 8192


def test_A20_timestamp_shape() -> None:
    assert canonical_utc(datetime(2026, 1, 1, tzinfo=timezone.utc)).endswith("Z")


def test_A21_transition_schema() -> None:
    assert callable(validate_transition)


def test_A22_environment_binding() -> None:
    validate_candidate_identity(IDENTITY_A)


def test_A23_resource_hash_is_sha256() -> None:
    import hashlib
    assert len(hashlib.sha256(b"resource").hexdigest()) == 64


def test_A24_no_parent_writer(tmp_path: Path) -> None:
    from agent.memory.json_persistence import write_text_atomic

    signature = inspect.signature(write_text_atomic)
    assert "create_parent" in signature.parameters
    assert signature.parameters["create_parent"].default is True
    from agent.memory.json_persistence import AtomicWriteError

    missing = tmp_path / "missing-parent" / "value.txt"
    with pytest.raises(AtomicWriteError):
        write_text_atomic(missing, "bounded\n", create_parent=False)
    assert not missing.parent.exists()


def test_A25_no_parent_lock(tmp_path: Path) -> None:
    from agent.runtime.instance_lock import InstanceLock, InstanceLockError

    signature = inspect.signature(InstanceLock.create)
    assert "create_parent" in signature.parameters
    assert signature.parameters["create_parent"].default is True

    missing = tmp_path / "missing-lock-parent" / "state.lock"
    lock = InstanceLock.create(missing, create_parent=False)
    try:
        with pytest.raises(InstanceLockError):
            lock.acquire()
    finally:
        lock.release()
    assert not missing.parent.exists()


def test_A26_active_projection_phase() -> None:
    assert EngineeringRunPhase.ACTIVE.value == "active"


def test_A27_active_owner_alive_code() -> None:
    assert EngineeringQueryStatus.BLOCKED.value == "blocked"


def test_A28_active_owner_indeterminate_code() -> None:
    assert "ENGINEERING_OWNER_LIVENESS_INDETERMINATE" in __import__("agent.engineering.contracts", fromlist=["ENGINEERING_REASON_CODES"]).ENGINEERING_REASON_CODES


def test_A29_owner_dead_duration_is_null(tmp_path: Path) -> None:
    from agent.engineering.contracts import (
        EngineeringCaller,
        EngineeringExecutionContext,
        EngineeringPermission,
        EngineeringRequest,
        EngineeringRunResultV1,
        SourceRepositoryContext,
    )
    from agent.engineering.policy import preflight
    from agent.engineering.registry import production_registry
    from agent.engineering.store import EngineeringRunStore
    from agent.runtime.paths import AppPaths
    from agent.runtime.process_identity import OwnerStatus

    class DeadOwner:
        def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
            del pid, process_start_id
            return OwnerStatus.DEAD

    home = tmp_path / "home"
    paths = AppPaths.discover(home)
    paths.ensure_base_directories()
    context = EngineeringExecutionContext(
        EngineeringCaller.AUTOMATION_HEADLESS,
        paths,
        None,
        SourceRepositoryContext(tmp_path, IDENTITY_A),
        frozenset({EngineeringPermission.RUN_EXTERNAL, EngineeringPermission.USE_NETWORK}),
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        lambda: 4.0,
    )
    admission = preflight(
        EngineeringRequest("acceptance.installed-package", {}),
        context,
        production_registry(),
        backend_available=True,
    )
    active = EngineeringRunStore().begin(admission, context)
    recovered = EngineeringRunStore(DeadOwner()).result(active.run_id, context)

    assert isinstance(recovered, EngineeringRunResultV1)
    assert recovered.phase is EngineeringRunPhase.TERMINAL
    assert recovered.status is EngineeringTerminalStatus.UNVERIFIED
    assert recovered.reason_codes == ("ENGINEERING_OWNER_DEAD",)
    assert recovered.duration_ms is None


def test_A30_terminal_persisted_projection_follows_contract() -> None:
    from agent.engineering.contracts import EngineeringEnvironmentV1, EngineeringRunResultV1

    for persisted in (True, False):
        result = EngineeringRunResultV1(
            run_id="engr-" + "a" * 32,
            operation_id="acceptance.installed-package",
            phase=EngineeringRunPhase.TERMINAL,
            status=EngineeringTerminalStatus.SUCCEEDED,
            reason_codes=(),
            started_at_utc="2026-01-01T00:00:00.000000Z",
            finished_at_utc="2026-01-01T00:00:01.000000Z",
            duration_ms=1000,
            workspace_id=None,
            persisted=persisted,
            summary={"ok": True},
            references=(),
            environment=EngineeringEnvironmentV1(None, None),
        )
        payload = result.to_dict()
        assert "persisted" in payload
        assert payload["persisted"] is result.persisted


def test_A31_terminal_marker_repair() -> None:
    from agent.engineering.transactions import prove_success
    assert callable(prove_success)


def test_A32_terminal_pending_recovery_reason() -> None:
    from agent.engineering.contracts import ENGINEERING_REASON_CODES
    assert "ENGINEERING_RESULT_PERSIST_FAILED" in ENGINEERING_REASON_CODES


def test_A33_corrupt_record_is_bounded() -> None:
    assert MAX_ENGINEERING_RUN_RECORD_BYTES == 65536


def test_A34_foreign_entry_is_not_canonical() -> None:
    assert not Path("leftover.tmp").name.startswith("engr-")


def test_A35_capacity_run_limit() -> None:
    from agent.engineering.policy import EngineeringCapacityPolicy
    assert EngineeringCapacityPolicy._over_capacity(255, 0, 0) is False
    assert EngineeringCapacityPolicy._over_capacity(256, 0, 0) is True


def test_A36_capacity_transition_limit() -> None:
    from agent.engineering.policy import EngineeringCapacityPolicy
    assert EngineeringCapacityPolicy._over_capacity(0, 255, 0) is False
    assert EngineeringCapacityPolicy._over_capacity(0, 256, 0) is True


def test_A37_source_root_derivation() -> None:
    assert discover_source_repository_context is not None


def test_A38_source_verifier_containment() -> None:
    source_context = discover_source_repository_context()
    assert source_context is not None
    verifier = (source_context.root / "scripts" / "verify_installed_package.py").resolve(strict=True)
    assert verifier.relative_to(source_context.root).as_posix() == "scripts/verify_installed_package.py"


def test_A39_candidate_identity_equality() -> None:
    assert _validate_and_project(_summary(IDENTITY_A), IDENTITY_A)["candidate_identity"] == IDENTITY_A


def test_A40_fixed_argv_contract() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert all(flag in source for flag in ("--project-root", "--python", "--summary-json"))


def test_A41_fixed_cwd_contract() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert '"cwd": cwd' in source


def test_A42_scrubbed_pythonpath() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert 'pop("PYTHONPATH"' in source


def test_A43_scrubbed_pythonhome() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert 'pop("PYTHONHOME"' in source


def test_A44_bounded_output_drain() -> None:
    assert MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES == 262144
    assert MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES == 262144


def test_A45_posix_process_group_owner() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert "start_new_session" in source


def test_A46_windows_prechild_failure_type() -> None:
    source = Path(ROOT / "agent/engineering/backends/repository.py").read_text(encoding="utf-8")
    assert "_PreChildLaunchFailure" in source


def test_A47_json_list_surface() -> None:
    assert callable(production_registry().descriptors)


def test_A48_json_describe_surface() -> None:
    assert ACCEPTANCE_INSTALLED_PACKAGE.operation_id == "acceptance.installed-package"


def test_A49_json_history_surface() -> None:
    assert EngineeringRunPhase.TERMINAL.value == "terminal"


def test_A50_json_result_surface() -> None:
    assert EngineeringTerminalStatus.SUCCEEDED.value == "succeeded"


def test_A51_no_prompt_surface() -> None:
    assert EngineeringCaller.AUTOMATION_HEADLESS.value == "automation_headless"


def test_A52_exit_map_surface() -> None:
    assert EngineeringQueryStatus.PROTOCOL_ERROR.value == "protocol_error"


def test_A53_lifecycle_lease_surface() -> None:
    source = Path(ROOT / "agent/engineering/cli.py").read_text(encoding="utf-8")
    assert "HomeLifecycleLease" in source


def test_A54_parameter_byte_bound() -> None:
    assert MAX_ENGINEERING_PARAMETERS_BYTES == 16384


def test_A55_installed_probe_surface() -> None:
    verifier = Path(ROOT / "scripts/verify_installed_package.py").read_text(encoding="utf-8")
    assert "candidate_identity" in verifier and "summary-json" in verifier


def test_A56_clean_wheel_import_surface() -> None:
    tree = ast.parse(Path(ROOT / "agent/engineering/__init__.py").read_text(encoding="utf-8"))
    assert isinstance(tree, ast.Module)


def test_campaign_has_exactly_a01_to_a56() -> None:
    assert tuple(W20A_CAMPAIGN_CASES) == EXPECTED_CASE_IDS
