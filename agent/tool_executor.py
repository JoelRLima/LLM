import hashlib
from typing import Any

from agent.contracts import ToolArgs, ToolResult
from agent.llm.prompts import ERROR_PATTERNS
from agent.parsers import normalize_tool_result, stringify
from agent.planning.errors import ToolNotFoundError
from agent.runtime.logging import logger


class ToolExecutor:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def run_tool(
        self, tool_name: str, args: ToolArgs, record_result: bool = True
    ) -> ToolResult:
        result: ToolResult
        if tool_name not in self.orchestrator.skills:
            raise ToolNotFoundError(f"Tool '{tool_name}' não foi registrada no Orchestrator.")
        if self.orchestrator.active_skills and tool_name not in self.orchestrator.active_skills:
            allowed = ", ".join(sorted(self.orchestrator.active_skills)) if self.orchestrator.active_skills else "todas disponíveis"
            result = {
                "ok": False,
                "done": False,
                "data": None,
                "error": f"Tool '{tool_name}' não está permitida para esta persona. Ferramentas disponíveis: {allowed}",
                "message": None,
            }
            if record_result:
                self.orchestrator.agent_state.record_tool_result(tool_name, args, result)
            return result

        print(f"⚙️  Usando {tool_name}...", end="", flush=True)
        logger.info(f"Executando tool {tool_name} com args {args}")
        try:
            raw_result = self.orchestrator.skills[tool_name].execute(args)
        except Exception as e:
            logger.error(f"Erro ao executar tool {tool_name}: {e}", exc_info=True)
            raw_result = {
                "ok": False,
                "done": False,
                "data": None,
                "error": f"Erro ao executar tool: {e}",
                "message": "Exceção durante a execução da ferramenta.",
            }
        result = normalize_tool_result(raw_result, ERROR_PATTERNS)

        msg = result.get("message") or ("Concluído" if result.get("ok") else "Falha")
        print(f" {msg}")
        if self.orchestrator.verbose:
            print(f"[DEBUG] Resultado completo: {stringify(result)}")

        if record_result:
            self.orchestrator.agent_state.record_tool_result(tool_name, args, result)
        return result

    def summarize_text(self, text: str, context: str = "") -> str:
        try:
            summarize_skill = self.orchestrator.skills.get("summarize")
            if summarize_skill:
                result = summarize_skill.execute({"text": text, "context": context})
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
        self.orchestrator.agent_state.memory.state["analyzed_files"][file_path] = summary[:150]
        self.orchestrator.agent_state.memory.state["file_summaries"][file_path] = summary
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_hash = hashlib.sha256(f.read().encode("utf-8")).hexdigest()
            self.orchestrator.agent_state.memory.state.setdefault("file_hashes", {})[file_path] = file_hash
        except Exception:
            pass
