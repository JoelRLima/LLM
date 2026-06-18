import os
from pathlib import Path
from typing import Any, Dict, List
from .base import BaseSkill
from logger import logger

# Arquivos que o agente NUNCA pode modificar (núcleo do próprio sistema)
CORE_FILES_BLOCKLIST = {
    "cli.py", "logger.py", "config.py", "session.py",
    "agent/orchestrator.py", "agent/router.py", "agent/memory.py",
    "agent/parsers.py", "agent/prompts.py",
    "agent/skills/__init__.py", "agent/skills/base.py",
    "agent/skills/file_writer.py", "agent/skills/python_executor.py",
}

class FileWriterSkill(BaseSkill):
    name = "file_writer"
    description = (
        "Cria ou edita arquivos de texto no projeto. "
        "Pode criar um arquivo novo, sobrescrever o conteúdo completo, ou aplicar uma edição cirúrgica "
        "substituindo um bloco de texto específico. "
        "NÃO pode modificar os arquivos core do agente (cli.py, orchestrator.py, etc.)."
    )

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "action": "string: 'write' (cria/sobrescreve), 'patch' (substitui trecho), 'append' (adiciona ao final), 'delete_lines' (remove linhas)",
            "file_path": "string: caminho relativo do arquivo",
            "content": "string: conteúdo a escrever (para 'write' e 'append')",
            "old_content": "string: trecho EXATO a ser substituído (para 'patch')",
            "new_content": "string: novo trecho que vai substituir o old_content (para 'patch')",
            "start_line": "integer: linha inicial a deletar (1-indexada, para 'delete_lines')",
            "end_line": "integer: linha final a deletar (1-indexada, para 'delete_lines')",
        }

    def _is_safe(self, requested: Path) -> tuple[bool, str]:
        """Verifica se o arquivo pode ser escrito com segurança."""
        # Restrição de path traversal
        if not str(requested).startswith(str(self.base_dir)):
            return False, "Acesso fora do diretório do projeto não é permitido."

        # Calcula caminho relativo para checar blocklist
        try:
            rel = requested.relative_to(self.base_dir)
        except ValueError:
            return False, "Caminho inválido."

        rel_str = str(rel).replace("\\", "/")

        # Checa contra blocklist de arquivos core
        for blocked in CORE_FILES_BLOCKLIST:
            if rel_str == blocked or rel_str.endswith("/" + blocked):
                return False, f"'{rel_str}' é um arquivo core do agente e não pode ser modificado."

        # Bloqueia extensões binárias/executáveis
        blocked_exts = {".exe", ".dll", ".so", ".pyc", ".bin", ".jpg", ".png", ".zip", ".tar"}
        if requested.suffix.lower() in blocked_exts:
            return False, f"Extensão '{requested.suffix}' não é permitida para escrita."

        return True, ""

    def execute(self, args: Dict[str, Any]) -> Any:
        action = args.get("action", "write")
        file_path = args.get("file_path", "")

        if not file_path:
            return {"ok": False, "done": True, "error": "Nenhum file_path fornecido."}

        try:
            requested = (self.base_dir / file_path).resolve()
        except Exception as e:
            return {"ok": False, "done": True, "error": f"Caminho inválido: {e}"}

        safe, reason = self._is_safe(requested)
        if not safe:
            return {"ok": False, "done": True, "error": f"Escrita bloqueada: {reason}"}

        # Garante que o diretório pai existe
        requested.parent.mkdir(parents=True, exist_ok=True)

        try:
            if action == "write":
                content = args.get("content", "")
                requested.write_text(content, encoding="utf-8")
                lines = content.count("\n") + 1
                return {"ok": True, "done": True, "message": f"'{file_path}' escrito com sucesso ({lines} linhas)."}

            elif action == "append":
                content = args.get("content", "")
                with open(requested, "a", encoding="utf-8") as f:
                    f.write(content)
                return {"ok": True, "done": True, "message": f"Conteúdo adicionado ao final de '{file_path}'."}

            elif action == "patch":
                old_content = args.get("old_content")
                new_content = args.get("new_content", "")
                if old_content is None:
                    return {"ok": False, "done": True, "error": "Falta 'old_content' para a ação 'patch'."}
                if not requested.exists():
                    return {"ok": False, "done": True, "error": f"Arquivo '{file_path}' não existe para aplicar patch."}
                current = requested.read_text(encoding="utf-8")
                if old_content not in current:
                    return {"ok": False, "done": True, "error": f"'old_content' não encontrado exatamente no arquivo. Verifique espaços e indentação."}
                patched = current.replace(old_content, new_content, 1)
                requested.write_text(patched, encoding="utf-8")
                return {"ok": True, "done": True, "message": f"Patch aplicado em '{file_path}' com sucesso."}

            elif action == "delete_lines":
                start = args.get("start_line")
                end = args.get("end_line")
                if start is None or end is None:
                    return {"ok": False, "done": True, "error": "Falta 'start_line' ou 'end_line' para 'delete_lines'."}
                if not requested.exists():
                    return {"ok": False, "done": True, "error": f"Arquivo '{file_path}' não existe."}
                lines = requested.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines = lines[:start - 1] + lines[end:]
                requested.write_text("".join(new_lines), encoding="utf-8")
                return {"ok": True, "done": True, "message": f"Linhas {start}-{end} removidas de '{file_path}'."}

            else:
                return {"ok": False, "done": True, "error": f"Ação desconhecida: '{action}'. Use 'write', 'append', 'patch' ou 'delete_lines'."}

        except Exception as e:
            logger.error(f"FileWriterSkill error: {e}", exc_info=True)
            return {"ok": False, "done": True, "error": f"Erro ao escrever arquivo: {e}"}
