"""Approval preparation for concrete tool invocations."""

from __future__ import annotations

from typing import Any

from agent.approval import ApprovalDecision, ApprovalRequest, format_concrete_operation
from agent.runtime.logging import logger
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_support import denial
from agent.tools.mode_enforcement import required_capabilities_for_invocation


def check_effect_approval(gateway: Any, invocation: ToolInvocation, descriptor: ToolDescriptor) -> ToolResult | None:
    effects = frozenset({"write", "vcs_write", "process", "network", "package_install", "validate"})
    requested = effects & required_capabilities_for_invocation(descriptor, invocation.args)
    if not requested:
        return None
    gateway._emit("approval_requested", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id})
    try:
        operation, concrete_metadata = format_concrete_operation(
            invocation.tool_name,
            str(invocation.args.get("file_path") or invocation.args.get("target") or "workspace"),
            invocation.args,
        )
        decision = gateway.approval_port.request(
            ApprovalRequest(
                action=invocation.tool_name,
                resource=str(invocation.args.get("file_path") or invocation.args.get("target") or "workspace"),
                prompt=f"Autorizar efeitos {', '.join(sorted(requested))} para {operation}?",
                metadata={
                    "task_id": invocation.task_id,
                    "invocation_id": invocation.invocation_id,
                    "capabilities": sorted(requested),
                    **concrete_metadata,
                },
            )
        )
    except Exception as exc:
        logger.warning("[GATEWAY] Approval provider failed: %s", type(exc).__name__)
        return denial(invocation, ToolStatus.FAILED, "APPROVAL_FAILED", "Approval provider failed.")
    if decision is ApprovalDecision.APPROVED:
        gateway._emit("approval_approved", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id})
        return None
    status = ToolStatus.BLOCKED if decision is ApprovalDecision.REQUIRED else ToolStatus.PERMISSION_DENIED
    code = "APPROVAL_REQUIRED" if status is ToolStatus.BLOCKED else "APPROVAL_DENIED"
    return denial(invocation, status, code, "A aprovacao necessaria nao foi concedida.")
