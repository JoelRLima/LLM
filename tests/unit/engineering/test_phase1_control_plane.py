from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendStateIndeterminateError,
    EngineeringBackendStatus,
    EngineeringCaller,
    EngineeringEffects,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringOperationDescriptor,
    EngineeringPermission,
    EngineeringQueryStatus,
    EngineeringRequest,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringRunSummaryV1,
    EngineeringScope,
    EngineeringTerminalStatus,
)
from agent.engineering.policy import EngineeringPreflight
from agent.engineering.registry import ACCEPTANCE_INSTALLED_PACKAGE, EngineeringRegistry, production_registry
from agent.engineering.service import (
    EngineeringService,
    EngineeringStoreError,
    EngineeringTerminalIntent,
)
from agent.runtime.paths import AppPaths

RUN_ID = "engr-" + "a" * 32
NOW = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
CANDIDATE = "a" * 40 + ":" + "b" * 64 + ":" + "c" * 64


class Backend:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        del request, context
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        assert isinstance(self.value, EngineeringBackendOutcome)
        return self.value


class Store:
    def __init__(self, fail_begin: str | None = None) -> None:
        self.fail_begin = fail_begin
        self.started = 0

    def begin(self, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1:
        del context
        if self.fail_begin:
            raise EngineeringStoreError(self.fail_begin)
        self.started += 1
        return EngineeringRunSummaryV1(
            RUN_ID,
            admission.descriptor.operation_id,
            EngineeringRunPhase.ACTIVE,
            None,
            (),
            "2026-01-02T03:04:05.000006Z",
            None,
            None,
            admission.environment.workspace_id,
            True,
        )

    def publish_managed_guard(
        self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, context: EngineeringExecutionContext
    ) -> None:
        del active, admission, context

    def finish(
        self,
        active: EngineeringRunSummaryV1,
        admission: EngineeringPreflight,
        intent: EngineeringTerminalIntent,
        context: EngineeringExecutionContext,
    ) -> EngineeringRunResultV1:
        del context
        return EngineeringRunResultV1(
            run_id=active.run_id,
            operation_id=active.operation_id,
            phase=EngineeringRunPhase.TERMINAL,
            status=intent.status,
            reason_codes=intent.reason_codes,
            started_at_utc=active.started_at_utc,
            finished_at_utc="2026-01-02T03:04:06.000006Z",
            duration_ms=1000,
            workspace_id=active.workspace_id,
            persisted=True,
            summary=intent.summary,
            references=intent.references,
            environment=admission.environment,
        )


def context(tmp_path: Path, permissions: frozenset[EngineeringPermission] = frozenset()) -> EngineeringExecutionContext:
    from agent.engineering.contracts import SourceRepositoryContext

    return EngineeringExecutionContext(
        EngineeringCaller.AUTOMATION_HEADLESS,
        AppPaths.discover(tmp_path / "home"),
        None,
        SourceRepositoryContext(tmp_path, CANDIDATE),
        permissions,
        lambda: NOW,
        lambda: 10.0,
    )


def service(tmp_path: Path, *, backend: Backend | None = None, store: Store | None = None) -> EngineeringService:
    actual_backend = backend or Backend(
        EngineeringBackendOutcome(EngineeringBackendStatus.SUCCEEDED, {}, (), False, True)
    )
    registry = EngineeringRegistry((ACCEPTANCE_INSTALLED_PACKAGE,))
    return EngineeringService(registry, {"acceptance.installed-package": actual_backend}, store or Store())


def test_closed_status_vocabularies_keep_blocked_query_only() -> None:
    assert [item.value for item in EngineeringTerminalStatus] == ["succeeded", "failed", "unverified", "protocol_error"]
    assert EngineeringQueryStatus.BLOCKED.value == "blocked"


def test_exact_production_descriptor_and_field_set(tmp_path: Path) -> None:
    view = service(tmp_path).list_operations(context(tmp_path))[0].to_dict()
    assert set(view) == {
        "schema_version",
        "operation_id",
        "scope",
        "effects",
        "available",
        "unavailable_reason",
        "parameter_schema",
    }
    assert view["operation_id"] == "acceptance.installed-package"
    assert view["effects"] == {
        "network": True,
        "real_model": False,
        "managed_external_process": True,
        "mutating_or_executing": True,
        "hermetic": False,
    }


@pytest.mark.parametrize(
    ("engineering_request", "permissions", "expected"),
    [
        (EngineeringRequest(" BAD ", {}), frozenset(), "ENGINEERING_OPERATION_NOT_FOUND"),
        (EngineeringRequest("missing.operation", {}), frozenset(), "ENGINEERING_OPERATION_NOT_FOUND"),
        (EngineeringRequest("acceptance.installed-package", {"x": 1}), frozenset(), "ENGINEERING_INVALID_PARAMETERS"),
        (EngineeringRequest("acceptance.installed-package", {}), frozenset(), "ENGINEERING_EXTERNAL_NOT_AUTHORIZED"),
        (
            EngineeringRequest("acceptance.installed-package", {}),
            frozenset({EngineeringPermission.RUN_EXTERNAL}),
            "ENGINEERING_NETWORK_NOT_AUTHORIZED",
        ),
    ],
)
def test_preflight_precedence(
    tmp_path: Path,
    engineering_request: EngineeringRequest,
    permissions: frozenset[EngineeringPermission],
    expected: str,
) -> None:
    result = service(tmp_path).preflight(engineering_request, context(tmp_path, permissions))
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == expected


def test_service_rejects_normalized_parameters_over_bound(tmp_path: Path) -> None:
    descriptor = EngineeringOperationDescriptor(
        "fake.big",
        EngineeringScope.GLOBAL,
        EngineeringEffects(False, False, False, False, True),
        {"type": "object", "properties": {"data": {}}, "required": [], "additionalProperties": False},
    )
    registry = EngineeringRegistry((descriptor,))
    subject = EngineeringService(
        registry,
        {"fake.big": Backend(EngineeringBackendOutcome(EngineeringBackendStatus.SUCCEEDED, {}, (), False, False))},
        Store(),
    )
    result = subject.preflight(EngineeringRequest("fake.big", {"data": "x" * 16_384}), context(tmp_path))
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_PARAMETERS_TOO_LARGE"


def test_request_cannot_inject_trusted_fields(tmp_path: Path) -> None:
    result = service(tmp_path).preflight(
        EngineeringRequest("acceptance.installed-package", {"permissions": ["run_external"]}), context(tmp_path)
    )
    assert isinstance(result, EngineeringErrorV1)
    assert result.code == "ENGINEERING_INVALID_PARAMETERS"


def test_known_unavailable_descriptor_remains_describable(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx = EngineeringExecutionContext(
        ctx.caller, ctx.app_paths, None, None, ctx.permissions, ctx.utc_now, ctx.monotonic_now
    )
    result = service(tmp_path).describe("acceptance.installed-package", ctx)
    assert not isinstance(result, EngineeringErrorV1)
    assert result.available is False
    assert result.unavailable_reason == "ENGINEERING_SOURCE_REPOSITORY_REQUIRED"


def test_pre_active_persistence_failure_is_error_not_null_run(tmp_path: Path) -> None:
    result = service(tmp_path, store=Store("ENGINEERING_RESULT_PERSIST_FAILED")).run(
        EngineeringRequest("acceptance.installed-package", {}),
        context(tmp_path, frozenset({EngineeringPermission.RUN_EXTERNAL, EngineeringPermission.USE_NETWORK})),
    )
    assert isinstance(result, EngineeringErrorV1)
    assert result.run_id is None


def test_typed_backend_indeterminate_maps_to_unverified(tmp_path: Path) -> None:
    result = service(tmp_path, backend=Backend(EngineeringBackendStateIndeterminateError())).run(
        EngineeringRequest("acceptance.installed-package", {}),
        context(tmp_path, frozenset({EngineeringPermission.RUN_EXTERNAL, EngineeringPermission.USE_NETWORK})),
    )
    assert isinstance(result, EngineeringRunResultV1)
    assert result.status is EngineeringTerminalStatus.UNVERIFIED
    assert result.reason_codes == ("ENGINEERING_BACKEND_STATE_INDETERMINATE",)


def test_unexpected_managed_backend_exception_is_not_settled_failed(tmp_path: Path) -> None:
    result = service(tmp_path, backend=Backend(RuntimeError("boom"))).run(
        EngineeringRequest("acceptance.installed-package", {}),
        context(tmp_path, frozenset({EngineeringPermission.RUN_EXTERNAL, EngineeringPermission.USE_NETWORK})),
    )
    assert isinstance(result, EngineeringRunResultV1)
    assert result.status is EngineeringTerminalStatus.UNVERIFIED
    assert result.reason_codes == ("ENGINEERING_BACKEND_STATE_INDETERMINATE", "ENGINEERING_INTERNAL_ERROR")


def test_dishonest_effect_claim_is_protocol_error(tmp_path: Path) -> None:
    descriptor = EngineeringOperationDescriptor(
        "fake.hermetic",
        EngineeringScope.GLOBAL,
        EngineeringEffects(False, False, True, True, True),
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )
    subject = EngineeringService(
        EngineeringRegistry((descriptor,)),
        {"fake.hermetic": Backend(EngineeringBackendOutcome(EngineeringBackendStatus.SUCCEEDED, {}, (), False, True))},
        Store(),
    )
    result = subject.run(
        EngineeringRequest("fake.hermetic", {}), context(tmp_path, frozenset({EngineeringPermission.RUN_EXTERNAL}))
    )
    assert isinstance(result, EngineeringRunResultV1)
    assert result.status is EngineeringTerminalStatus.PROTOCOL_ERROR


def test_fake_registry_does_not_contaminate_production() -> None:
    fake = EngineeringOperationDescriptor(
        "fake.one", EngineeringScope.GLOBAL, EngineeringEffects(False, False, False, False, True), {"type": "object"}
    )
    EngineeringRegistry((fake,))
    assert [item.operation_id for item in production_registry().descriptors()] == [
        "acceptance.installed-package",
        "evaluation.practical-v1",
        "health.offline",
        "inspection.completed-run",
    ]


@pytest.mark.parametrize("operation_id", ["UPPER", ".leading", "trailing.", "two..dots", "white space"])
def test_operation_id_grammar_rejects_noncanonical_forms(operation_id: str) -> None:
    from agent.engineering.contracts import validate_operation_id

    with pytest.raises(ValueError):
        validate_operation_id(operation_id)


def test_reason_codes_are_deduplicated_in_declaration_order() -> None:
    from agent.engineering.contracts import order_reason_codes

    assert order_reason_codes(["ENGINEERING_INTERNAL_ERROR", "ENGINEERING_CANCELLED", "ENGINEERING_CANCELLED"]) == (
        "ENGINEERING_CANCELLED",
        "ENGINEERING_INTERNAL_ERROR",
    )


def test_hermetic_semaphore_limits_two_and_releases_in_reverse_cleanup(tmp_path: Path) -> None:
    descriptor = EngineeringOperationDescriptor(
        "fake.hermetic-limit",
        EngineeringScope.GLOBAL,
        EngineeringEffects(False, False, False, True, True),
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )
    release = Event()
    entered_two = Event()
    lock = Lock()
    counts = {"active": 0, "maximum": 0}

    class BlockingBackend:
        def execute(
            self, request: EngineeringRequest, execution_context: EngineeringExecutionContext
        ) -> EngineeringBackendOutcome:
            del request, execution_context
            with lock:
                counts["active"] += 1
                counts["maximum"] = max(counts["maximum"], counts["active"])
                if counts["active"] == 2:
                    entered_two.set()
            release.wait(timeout=3)
            with lock:
                counts["active"] -= 1
            return EngineeringBackendOutcome(EngineeringBackendStatus.SUCCEEDED, {}, (), False, False)

    subject = EngineeringService(
        EngineeringRegistry((descriptor,)), {descriptor.operation_id: BlockingBackend()}, Store()
    )
    execution_context = context(tmp_path)
    results: list[object] = []

    def invoke() -> None:
        results.append(subject.run(EngineeringRequest(descriptor.operation_id, {}), execution_context))

    threads = [Thread(target=invoke) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert entered_two.wait(timeout=2)
    time.sleep(0.1)
    assert counts["maximum"] == 2 and len(results) == 0
    release.set()
    for thread in threads:
        thread.join(timeout=3)
    assert len(results) == 3 and counts["maximum"] == 2 and counts["active"] == 0
