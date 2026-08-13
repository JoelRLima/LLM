"""Operational-mode ceiling checks at the canonical invocation boundary."""

from __future__ import annotations

from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_support import denial


def requests_test_execution(value: object) -> bool:
    """Recognize validation requests that can execute workspace-owned code."""

    if isinstance(value, dict):
        for key, item in value.items():
            if key == "include_tests" and bool(item):
                return True
            if requests_test_execution(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(requests_test_execution(item) for item in value)
    return False


def required_capabilities_for_invocation(
    descriptor: ToolDescriptor,
    arguments: object,
) -> frozenset[str]:
    """Add argument-dependent effects without weakening descriptor policy."""

    required = frozenset(descriptor.capabilities)
    if "validate" in required and requests_test_execution(arguments):
        required |= frozenset({"process"})
    return required


def check_capability_ceiling(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    ceiling: frozenset[str] | None,
    mode: str | None,
    allowed: frozenset[str] | None,
) -> ToolResult | None:
    required = required_capabilities_for_invocation(descriptor, invocation.args)
    mode_missing = required - ceiling if ceiling is not None else frozenset()
    caller_missing = required - allowed if allowed is not None else frozenset()
    missing = mode_missing | caller_missing
    if (
        ceiling is not None
        and "validate" in required
        and requests_test_execution(invocation.args)
    ):
        return denial(
            invocation,
            ToolStatus.PERMISSION_DENIED,
            "OPERATIONAL_MODE_DENIED",
            f"Modo {mode or 'operational mode'} nao permite executar codigo de testes do workspace.",
        )
    if not missing:
        return None
    mode_denied = bool(mode_missing)
    code = ("PERMISSION_DENIED", "OPERATIONAL_MODE_DENIED")[mode_denied]
    detail = (
        "Capabilities nao autorizadas.",
        f"Modo {mode or 'operational mode'} nao permite capabilities: {', '.join(sorted(missing))}.",
    )[mode_denied]
    return denial(invocation, ToolStatus.PERMISSION_DENIED, code, detail)
