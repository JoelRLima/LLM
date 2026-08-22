import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from agent.contracts import ToolArgs, ToolResult
from agent.parsers import stringify
from agent.planning.errors import ToolNotFoundError
from agent.planning.provenance_validation import validate_unresolved_symbolic_arguments
from agent.planning.step_contracts import PreparedInvocation
from agent.runtime.logging import logger
from agent.tools.contracts import ToolInvocationRequest


class ToolExecutor:
    def __init__(
        self,
        orchestrator: Any,
        *,
        path_resolver: Callable[[str | Path], Path] | None = None,
    ):
        self.orchestrator = orchestrator
        self._path_resolver = path_resolver

    def _resolve_user_path(self, file_path: str | Path) -> Path:
        if self._path_resolver is None:
            return Path(file_path)
        return self._path_resolver(file_path)

    def run_tool(
        self, tool_name: str, args: ToolArgs, record_result: bool = True
    ) -> ToolResult:
        request = ToolInvocationRequest(str(uuid.uuid4()), tool_name, args)
        return self.run_tool_invocation(request, record_result=record_result)

    def run_tool_invocation(
        self, request: ToolInvocationRequest, record_result: bool = True
    ) -> ToolResult:
        """Run a pre-correlated request without replacing its invocation ID."""
        gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
        if gateway is None:
            # The legacy invoker remains available to explicit low-level/admin
            # callers, but ToolExecutor is the model-actionable surface and
            # never falls back to a direct skill call.
            raise RuntimeError(
                "ToolInvocationGateway nao foi configurado para o runtime standalone."
            )

        tool_name = request.tool_name
        args = request.arguments
        if hasattr(self.orchestrator, "skills") and tool_name not in self.orchestrator.skills:
            try:
                gateway.registry.descriptor(tool_name)
            except KeyError as exc:
                raise ToolNotFoundError(
                    f"Tool '{tool_name}' nao foi registrada no Orchestrator."
                ) from exc

        resource = self._primary_resource(args)
        resource_text = f" Recurso: {json.dumps(resource, ensure_ascii=True)}." if resource else ""
        print(f"Usando {tool_name}...{resource_text}", end="", flush=True)
        logger.info("Executando tool %s com args %s", tool_name, args)
        raw_res = gateway.invoke(
            request,
            active_skills=self.orchestrator.active_skills or None,
            allowed_capabilities=getattr(self.orchestrator, "allowed_capabilities", None),
            record_result=record_result,
            cancellation_token=getattr(self.orchestrator, "cancellation_token", None),
        )
        # The executor facade is also used by parallel plan slots, where the
        # gateway state recorder is intentionally disabled and the finalizer
        # records this returned value itself. Preserve the canonical details
        # there (executed/artifacts/error_code) instead of downgrading it to
        # the historical projection before the result reaches AgentState.
        result = cast(ToolResult, raw_res.to_legacy_dict(include_details=True))
        msg = result.get("message") or ("Concluido" if result.get("ok") else "Falha")
        print(f" {msg}")
        if getattr(self.orchestrator, "verbose", False):
            print(f"[DEBUG] Resultado completo: {stringify(result)}")
        return result

    def run_prepared_invocation(
        self, prepared: PreparedInvocation, record_result: bool = True
    ) -> ToolResult:
        """Dispatch only the concrete preparation boundary.

        The planner owns resolution and preparation; this adapter preserves
        that boundary while the gateway remains responsible for the final
        schema, authority, mode, grant, approval, and confinement checks.
        """

        invocation_id = prepared.invocation_id or str(uuid.uuid4())
        state = getattr(self.orchestrator, "agent_state", None)
        current_plan_id = getattr(state, "plan_identity", None)
        if prepared.plan_id is not None and current_plan_id != prepared.plan_id:
            return self._prepared_block(
                invocation_id,
                "prepared_invocation_stale",
                "A invocacao preparada pertence a outro plano canonico.",
            )
        current_plan = getattr(state, "plan", None)
        if (
            prepared.plan_id is not None
            and isinstance(current_plan, list)
            and not any(
                isinstance(step, Mapping)
                and step.get("_step_id") == prepared.step_id
                for step in current_plan
            )
        ):
            return self._prepared_block(
                invocation_id,
                "prepared_invocation_stale",
                "A invocacao preparada nao pertence ao plano atual.",
            )
        observations = getattr(state, "tool_history", ())
        if current_plan_id is not None:
            observations = tuple(
                item
                for item in observations
                if not isinstance(item, Mapping)
                or item.get("plan_id") in (None, current_plan_id)
            )
        symbolic_error = validate_unresolved_symbolic_arguments(
            args=dict(prepared.args),
            objective=str(getattr(state, "objective", "") or ""),
            available_observations=observations,
        )
        if symbolic_error is not None:
            return self._prepared_block(
                invocation_id,
                "unresolved_symbolic_argument",
                symbolic_error,
            )
        # Explicit test/admin seams may replace ``run_tool`` on this
        # instance.  Preserve that injection with already-concrete args;
        # the production class method below still goes through the gateway.
        overridden_run_tool = self.run_tool
        if getattr(overridden_run_tool, "__func__", None) is not ToolExecutor.run_tool:
            return cast(
                ToolResult,
                overridden_run_tool(
                    prepared.tool,
                    dict(prepared.args),
                    record_result,
                ),
            )
        request = ToolInvocationRequest(
            invocation_id,
            prepared.tool,
            dict(prepared.args),
        )
        return self.run_tool_invocation(request, record_result=record_result)

    @staticmethod
    def _prepared_block(
        invocation_id: str, error_code: str, message: str
    ) -> ToolResult:
        return {
            "invocation_id": invocation_id,
            "ok": False,
            "done": True,
            "executed": False,
            "status": "blocked",
            "error": message,
            "error_code": error_code,
            "message": message,
        }

    @staticmethod
    def _primary_resource(args: Mapping[str, Any]) -> str | None:
        for key in ("file_path", "target", "path", "directory"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def summarize_text(self, text: str, context: str = "") -> str:
        try:
            gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
            if gateway is not None:
                res = gateway.run(
                    "summarize",
                    {"text": text, "context": context},
                    active_skills=self.orchestrator.active_skills or None,
                    allowed_capabilities=getattr(self.orchestrator, "allowed_capabilities", None),
                    record_result=False,
                )
                if res.ok:
                    return str(res.data or text[:300])
        except Exception as exc:
            logger.warning("Falha ao usar summarize_skill: %s", exc)
        return text[:300] + "..." if len(text) > 300 else text

    def maybe_summarize_and_store(
        self, tool_name: str, args: ToolArgs, result: ToolResult
    ) -> None:
        if tool_name not in ("code_analyzer", "file_reader") or not result.get("ok"):
            return

        file_path = args.get("target") or args.get("file_path")
        if not file_path or "data" not in result:
            return

        content = result.get("data")
        if isinstance(content, dict):
            if not content.get("classes") and not content.get("functions"):
                return
            content = stringify(content)
        if not content or len(str(content)) <= 300:
            return

        summary = self.summarize_text(str(content), context=f"Arquivo: {file_path}")
        memory = self.orchestrator.agent_state.memory
        memory_key = str(file_path)
        memory.remember(memory_key, summary, section="file_summaries")
        memory.state["analyzed_files"][memory_key] = summary[:150]
        try:
            with self._resolve_user_path(file_path).open("r", encoding="utf-8") as handle:
                file_hash = hashlib.sha256(handle.read().encode("utf-8")).hexdigest()
            memory.state.setdefault("file_hashes", {})[memory_key] = file_hash
        except (OSError, ValueError):
            pass
