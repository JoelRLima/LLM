from __future__ import annotations

import pytest

from agent.planning.errors import ToolNotFoundError
from agent.runtime.failures import (
    UNKNOWN_FAILURE_CODE,
    FailureFact,
    failure_fact_from_legacy_message,
)
from agent.runtime.outcome_taxonomy import error_definition
from agent.tools.contracts import ToolError, ToolResult, ToolStatus


def test_registered_code_uses_the_existing_registry() -> None:
    definition = error_definition("FILE_NOT_FOUND")
    assert definition is not None

    fact = FailureFact.from_code("FILE_NOT_FOUND", message="wording one")

    assert fact.definition is definition
    assert fact.layer is definition.layer
    assert fact.retryable is definition.retryable
    assert fact.hard is definition.hard
    assert fact.status == definition.default_status


def test_tool_result_success_is_not_a_failure() -> None:
    result = ToolResult("invocation-1", ToolStatus.SUCCEEDED, data="ok")

    assert FailureFact.from_tool_result(result) is None


def test_tool_result_preserves_structured_code_and_occurrence_status() -> None:
    result = ToolResult(
        "invocation-2",
        ToolStatus.TIMED_OUT,
        error=ToolError("TIMEOUT", "arbitrary wording", {"attempt": 1}),
    )

    fact = FailureFact.from_tool_result(result, tool_name="file_reader", step_id="s1")

    assert fact is not None
    assert fact.code == "TIMEOUT"
    assert fact.status == ToolStatus.TIMED_OUT.value
    assert fact.layer.value == "provider" or fact.layer.value == "runtime"
    assert fact.invocation_id == "invocation-2"
    assert fact.tool_name == "file_reader"
    assert fact.step_id == "s1"
    assert fact.detail == {"attempt": 1}


def test_failed_result_without_code_is_unknown_and_non_retryable() -> None:
    result = ToolResult("invocation-3", ToolStatus.FAILED, message="timeout permission denied")

    fact = FailureFact.from_tool_result(result)

    assert fact is not None
    assert fact.code == UNKNOWN_FAILURE_CODE
    assert fact.retryable is False
    assert fact.hard is False


def test_status_fact_adds_hardness_without_overwriting_registry_code() -> None:
    result = ToolResult(
        "invocation-4",
        ToolStatus.PERMISSION_DENIED,
        error=ToolError("TOOL_ERROR", "not retryable by status"),
    )

    fact = FailureFact.from_tool_result(result)

    assert fact is not None
    assert fact.code == "TOOL_ERROR"
    assert fact.status == ToolStatus.PERMISSION_DENIED.value
    assert fact.hard is True
    assert fact.retryable is False


@pytest.mark.parametrize(
    ("code", "status", "expected_retryable", "expected_hard"),
    [
        ("TOOL_ERROR", ToolStatus.FAILED, True, False),
        ("TOOL_ERROR", ToolStatus.PERMISSION_DENIED, False, True),
        ("AUTHORITY_DENIED", ToolStatus.PERMISSION_DENIED, False, True),
        ("MODEL_PROVIDER_ERROR", ToolStatus.FAILED, True, True),
    ],
)
def test_effective_retryability_respects_registry_and_decisive_status(
    code: str,
    status: ToolStatus,
    expected_retryable: bool,
    expected_hard: bool,
) -> None:
    fact = FailureFact.from_code(code, status=status)

    assert fact.retryable is expected_retryable
    assert fact.hard is expected_hard


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (FileNotFoundError("anything"), "FILE_NOT_FOUND", "failed"),
        (PermissionError("anything"), "PERMISSION_DENIED", "permission_denied"),
        (TimeoutError("anything"), "TIMEOUT", "timed_out"),
    ],
)
def test_exception_adapter_uses_exception_type(error, code: str, status: str) -> None:
    fact = FailureFact.from_exception(error)

    assert fact.code == code
    assert fact.status == status


def test_runtime_exception_adapter_preserves_tool_not_found_type_boundary() -> None:
    fact = FailureFact.from_exception(ToolNotFoundError("missing tool"))

    assert fact.code == "TOOL_NOT_FOUND"
    assert fact.retryable is True


def test_unknown_text_cannot_select_timeout_or_permission_policy() -> None:
    fact = failure_fact_from_legacy_message("timeout permission denied sandbox")

    assert fact.code == UNKNOWN_FAILURE_CODE
    assert fact.retryable is False
    assert fact.status == ToolStatus.FAILED.value


def test_direct_unknown_fact_cannot_override_registry_conservatism() -> None:
    fact = FailureFact(
        code="UNREGISTERED_CODE",
        layer="provider",
        status="failed",
        retryable=True,
        hard=False,
        message="timeout",
    )

    assert fact.layer.value == "runtime"
    assert fact.retryable is False


def test_public_projection_redacts_unsafe_registry_detail() -> None:
    fact = FailureFact.from_code(
        "SAFETY_BLOCK",
        message="secret implementation detail",
        detail={"secret": "do-not-publish"},
    )

    public = fact.to_public_dict()

    assert "secret implementation detail" not in str(public)
    assert "do-not-publish" not in str(public)
    assert "error_code" not in public


def test_public_projection_does_not_trust_public_safe_occurrence_diagnostics() -> None:
    fact = FailureFact.from_code(
        "TIMEOUT",
        message="api_key=SUPERSECRET",
        detail={"api_key": "SUPERSECRET", "attempt": 2},
    )

    public = fact.to_public_dict()

    assert "SUPERSECRET" not in str(public)
    assert public["error_code"] == "TIMEOUT"
    assert public["status"] == ToolStatus.TIMED_OUT.value
    assert public["layer"] == "runtime"
    assert "detail" not in public


def test_wording_does_not_change_structured_classification() -> None:
    first = FailureFact.from_code("TOOL_ERROR", message="timeout")
    second = FailureFact.from_code("TOOL_ERROR", message="permission denied")

    assert (first.code, first.layer, first.retryable, first.hard) == (
        second.code,
        second.layer,
        second.retryable,
        second.hard,
    )
