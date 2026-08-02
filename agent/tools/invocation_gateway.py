"""Single controlled invocation gateway for every tool execution."""

from __future__ import annotations

import concurrent.futures
from typing import Any, Callable, Dict, Optional

from agent.approval import ApprovalDecision, ApprovalPort, ApprovalRequest, RequireExplicitApproval
from agent.runtime.logging import logger
from agent.tools.contracts import (
    AuthorizationContext,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)
from agent.tools.tool_registry import ToolRegistry


class ToolInvocationGateway:
    """Controls authorization, schema validation, timeout and telemetry for tool execution."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        state_recorder: Optional[Callable[[str, Dict[str, Any], ToolResult], None]] = None,
        approval_port: ApprovalPort | None = None,
    ) -> None:
        self.registry = registry
        self.event_emitter = event_emitter
        self.state_recorder = state_recorder
        self.approval_port = approval_port or RequireExplicitApproval()

    def run(
        self,
        tool_name: str,
        args: Dict[str, Any],
        *,
        active_skills: Optional[list[str]] = None,
        allowed_capabilities: Optional[frozenset[str]] = None,
        timeout_seconds: Optional[int] = None,
        record_result: bool = True,
        task_id: Optional[str] = None,
        authorization_context: AuthorizationContext | None = None,
    ) -> ToolResult:
        invocation = ToolInvocation(tool_name=tool_name, args=args, task_id=task_id)

        # 1. Verification of tool existence in registry
        try:
            descriptor = self.registry.descriptor(tool_name)
        except KeyError:
            result = ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.UNAVAILABLE,
                error=ToolError("TOOL_NOT_FOUND", f"Ferramenta '{tool_name}' não registrada."),
                message=f"Tool '{tool_name}' não foi encontrada no ToolRegistry.",
            )
            self._record(tool_name, args, result, record_result)
            return result

        # 2. Authorization check (active skills filter)
        if active_skills is not None and tool_name not in active_skills:
            allowed = ", ".join(sorted(active_skills)) if active_skills else "nenhuma"
            result = ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.PERMISSION_DENIED,
                error=ToolError(
                    "PERMISSION_DENIED",
                    f"Tool '{tool_name}' não está permitida para esta persona. Permitidas: {allowed}",
                ),
                message=f"Invocação de '{tool_name}' bloqueada por política de autorização.",
            )
            self._record(tool_name, args, result, record_result)
            return result

        if authorization_context is not None:
            granted = authorization_context.effective_capabilities()
            missing = descriptor.capabilities - granted
            if missing:
                result = ToolResult(
                    invocation_id=invocation.invocation_id,
                    status=ToolStatus.PERMISSION_DENIED,
                    error=ToolError(
                        "PERMISSION_DENIED",
                        f"Tool '{tool_name}' requer capacidades não concedidas: {', '.join(sorted(missing))}",
                    ),
                    message="Invocação bloqueada pela interseção de grants.",
                )
                self._record(tool_name, args, result, record_result)
                return result

        # 2b. Authorization check by capabilities
        if allowed_capabilities is not None:
            descriptor_capabilities: frozenset[str] = getattr(
                descriptor, "capabilities", frozenset()
            )
            if not descriptor_capabilities.issubset(allowed_capabilities):
                result = ToolResult(
                    invocation_id=invocation.invocation_id,
                    status=ToolStatus.PERMISSION_DENIED,
                    error=ToolError(
                        "PERMISSION_DENIED",
                        f"Tool '{tool_name}' requer capacidades não autorizadas: {', '.join(sorted(descriptor_capabilities - allowed_capabilities))}",
                    ),
                    message=f"Invocação de '{tool_name}' bloqueada por capacidades insuficientes.",
                )
                self._record(tool_name, args, result, record_result)
                return result

        approval_result = self._check_effect_approval(invocation, descriptor)
        if approval_result is not None:
            self._record(tool_name, args, approval_result, record_result)
            return approval_result

        # 3. Schema validation before invocation
        try:
            self._validate_arguments(descriptor, args)
        except ValueError as exc:
            result = ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.PROTOCOL_ERROR,
                error=ToolError("INVALID_ARGUMENTS", str(exc)),
                message=f"Argumentos inválidos para '{tool_name}': {exc}",
            )
            self._record(tool_name, args, result, record_result)
            return result

        # 4. Timeout selection (parameter override > descriptor default)
        effective_timeout = timeout_seconds if timeout_seconds is not None else descriptor.timeout_seconds

        # 5. Telemetry start event
        self._emit("tool_start", {"tool": tool_name, "args": args, "invocation_id": invocation.invocation_id})
        logger.info(f"[GATEWAY] Invocando tool '{tool_name}' (id: {invocation.invocation_id})")

        # 6. Invocation with optional timeout
        if effective_timeout and effective_timeout > 0:
            result = self._invoke_with_timeout(invocation, effective_timeout)
        else:
            result = self.registry.invoke(invocation)

        # 7. Telemetry end event
        self._emit(
            "tool_end",
            {
                "tool": tool_name,
                "invocation_id": invocation.invocation_id,
                "status": result.status.value,
                "ok": result.ok,
            },
        )

        # 8. Record result in state
        self._record(tool_name, args, result, record_result)
        return result

    @staticmethod
    def _validate_arguments(descriptor: Any, args: Dict[str, Any]) -> None:
        schema = getattr(descriptor, "schema", None)
        if not schema:
            return
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")

        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if not isinstance(required, list):
            required = [required]

        for key in required:
            if key not in args:
                raise ValueError(f"missing required argument: {key}")

        for key, value in args.items():
            prop_schema = properties.get(key)
            if not prop_schema:
                continue
            ToolInvocationGateway._validate_property(key, value, prop_schema)

    @staticmethod
    def _validate_property(key: str, value: Any, schema: Any) -> None:
        expected_type = schema.get("type")
        valid_types = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }
        if expected_type in valid_types and not valid_types[expected_type]:
            raise ValueError(f"argument '{key}' must be a {expected_type}")

    def _check_effect_approval(self, invocation: ToolInvocation, descriptor: Any) -> ToolResult | None:
        effects = frozenset({"write", "process", "network", "package_install"})
        requested = effects & getattr(descriptor, "capabilities", frozenset())
        if not requested:
            return None
        decision = self.approval_port.request(
            ApprovalRequest(
                action=invocation.tool_name,
                resource=str(invocation.args.get("file_path") or invocation.args.get("target") or "workspace"),
                prompt=f"Autorizar efeitos {', '.join(sorted(requested))} para {invocation.tool_name}?",
                metadata={"task_id": invocation.task_id, "capabilities": sorted(requested)},
            )
        )
        if decision is ApprovalDecision.APPROVED:
            return None
        status = ToolStatus.BLOCKED if decision is ApprovalDecision.REQUIRED else ToolStatus.PERMISSION_DENIED
        code = "APPROVAL_REQUIRED" if status is ToolStatus.BLOCKED else "PERMISSION_DENIED"
        return ToolResult(
            invocation_id=invocation.invocation_id,
            status=status,
            error=ToolError(code, "A aprovação necessária não foi concedida."),
            message="A execução aguarda aprovação." if status is ToolStatus.BLOCKED else "Efeito negado pela política.",
        )

    def _invoke_with_timeout(self, invocation: ToolInvocation, timeout_seconds: int) -> ToolResult:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.registry.invoke, invocation)
        try:
            return future.result(timeout=float(timeout_seconds))
        except concurrent.futures.TimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            logger.warning(
                f"[GATEWAY] Tool '{invocation.tool_name}' excedeu o timeout de {timeout_seconds}s"
            )
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.TIMED_OUT,
                error=ToolError(
                    "TIMEOUT",
                    f"Execução da ferramenta '{invocation.tool_name}' excedeu o limite de {timeout_seconds}s.",
                ),
                message=f"Timeout na execução de {invocation.tool_name}.",
            )
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.FAILED,
                error=ToolError("EXECUTION_ERROR", str(exc)),
                message=f"Exceção durante a execução de {invocation.tool_name}: {exc}",
            )
        else:
            executor.shutdown(wait=True, cancel_futures=True)

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_emitter is not None:
            try:
                self.event_emitter(event_type, data)
            except Exception as exc:
                logger.warning(f"[GATEWAY] Erro ao emitir evento '{event_type}': {exc}")

    def _record(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: ToolResult,
        record_result: bool,
    ) -> None:
        if record_result and self.state_recorder is not None:
            try:
                self.state_recorder(tool_name, args, result)
            except Exception as exc:
                logger.warning(f"[GATEWAY] Erro ao registrar resultado no estado: {exc}")
