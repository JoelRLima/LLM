import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from agent.contracts import ToolArgs, ToolResult
from agent.parsers import stringify
from agent.planning.errors import ToolNotFoundError
from agent.runtime.logging import logger


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
        gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
        if gateway is not None:
            # Check for non-existent tool to raise ToolNotFoundError if expected
            if hasattr(self.orchestrator, "skills") and tool_name not in self.orchestrator.skills:
                try:
                    gateway.registry.descriptor(tool_name)
                except KeyError as exc:
                    raise ToolNotFoundError(
                        f"Tool '{tool_name}' não foi registrada no Orchestrator."
                    ) from exc

            print(f"⚙️  Usando {tool_name}...", end="", flush=True)
            logger.info(f"Executando tool {tool_name} com args {args}")

            raw_res = gateway.run(
                tool_name,
                args,
                active_skills=self.orchestrator.active_skills or None,
                allowed_capabilities=getattr(self.orchestrator, "allowed_capabilities", None),
                record_result=record_result,
            )
            result = cast(ToolResult, raw_res.to_legacy_dict())
            msg = result.get("message") or ("Concluído" if result.get("ok") else "Falha")
            print(f" {msg}")
            if getattr(self.orchestrator, "verbose", False):
                print(f"[DEBUG] Resultado completo: {stringify(result)}")
            return result

        legacy_invoker = getattr(self.orchestrator, "legacy_tool_invoker", None)
        if legacy_invoker is None:
            raise RuntimeError("ToolInvocationGateway não foi configurado para o runtime standalone.")
        try:
            result = cast(ToolResult, legacy_invoker.invoke(tool_name, args, record_result=record_result))
        except KeyError as exc:
            raise ToolNotFoundError(str(exc)) from exc
        msg = result.get("message") or ("Concluído" if result.get("ok") else "Falha")
        print(f" {msg}")
        if self.orchestrator.verbose:
            print(f"[DEBUG] Resultado completo: {stringify(result)}")
        return result

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
            else:
                legacy_invoker = getattr(self.orchestrator, "legacy_tool_invoker", None)
                if legacy_invoker is not None:
                    result = legacy_invoker.invoke(
                        "summarize", {"text": text, "context": context}, record_result=False
                    )
                    if result.get("ok"):
                        return str(result.get("data", text[:300]))
        except Exception as e:
            logger.warning(f"Falha ao usar summarize_skill: {e}")
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
            with self._resolve_user_path(file_path).open(
                "r",
                encoding="utf-8",
            ) as f:
                file_hash = hashlib.sha256(f.read().encode("utf-8")).hexdigest()
            memory.state.setdefault("file_hashes", {})[memory_key] = file_hash
        except (OSError, ValueError):
            pass
