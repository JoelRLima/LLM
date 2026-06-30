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
            "action": "string: 'write' (cria/sobrescreve), 'patch' (substitui trecho exato), 'append' (adiciona ao final), 'delete_lines' (remove linhas), 'ast_patch' (substitui função/classe por nome)",
            "file_path": "string: caminho relativo do arquivo",
            "content": "string: conteúdo a escrever (para 'write' e 'append')",
            "old_content": "string: trecho EXATO a ser substituído (para 'patch')",
            "new_content": "string: novo trecho que vai substituir o old_content (para 'patch')",
            "start_line": "integer: linha inicial a deletar (para 'delete_lines')",
            "end_line": "integer: linha final a deletar (para 'delete_lines')",
            "target": "string: nome da função, classe ou método a substituir (para 'ast_patch')",
            "new_code": "string: novo código completo da função/classe (para 'ast_patch')",
            "old_hash": "string: hash SHA256 do arquivo antes da edição (opcional, para 'ast_patch')"
        }

    def _is_safe(self, requested: Path) -> tuple[bool, str]:
        """Verifica se o arquivo pode ser escrito com segurança."""
        try:
            rel = requested.relative_to(self.base_dir)
        except ValueError:
            return False, "Acesso fora do diretório do projeto não é permitido."

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

    def _ast_patch(self, file_path: Path, target: str, new_code: str, old_hash: str = None) -> dict:
        import ast, hashlib

        try:
            original = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao ler arquivo para patch."}

        # Validação opcional de hash
        if old_hash:
            current_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
            if current_hash != old_hash:
                return {"ok": False, "done": True, "error": "hash mismatch",
                        "message": f"Hash não confere (esperado {old_hash[:8]}..., atual {current_hash[:8]}...)."}

        try:
            tree = ast.parse(original)
        except SyntaxError as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Arquivo com erro de sintaxe, patch não aplicado."}

        # Encontra o nó alvo
        found = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == target:
                found = node
                break

        if not found:
            return {"ok": False, "done": True, "error": "target not found",
                    "message": f"'{target}' não encontrado no arquivo."}

        # Determina linhas e indentação
        lines = original.splitlines(keepends=True)
        start = found.lineno - 1
        end = found.end_lineno - 1 if hasattr(found, 'end_lineno') and found.end_lineno else start

        orig_line = lines[start]
        indent = len(orig_line) - len(orig_line.lstrip())
        indent_str = orig_line[:indent]

        # Indenta o novo código com a mesma indentação
        new_lines = []
        for line in new_code.strip().splitlines(keepends=True):
            if line.strip():
                new_lines.append(indent_str + line.lstrip())
            else:
                new_lines.append(line)
        new_block = "".join(new_lines)

        # Monta novo conteúdo
        new_content_lines = lines[:start] + [new_block] + lines[end+1:]
        new_content = "".join(new_content_lines)

        # Verifica sintaxe do novo arquivo
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            return {"ok": False, "done": True, "error": str(e), "message": "O novo código tem erro de sintaxe."}

        # Backup leve (na mesma pasta, com extensão .bak)
        backup_path = file_path.with_suffix(file_path.suffix + ".ast_bak")
        try:
            backup_path.write_text(original, encoding="utf-8")
        except Exception:
            pass  # backup não é obrigatório

        # Escreve o arquivo
        try:
            file_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao escrever o patch."}

        return {"ok": True, "done": True, "message": f"'{target}' substituído com sucesso em '{file_path}'."}

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
                total_lines = len(lines)

                if not isinstance(start, int) or not isinstance(end, int):
                    return {"ok": False, "done": True, "error": "'start_line' e 'end_line' devem ser inteiros."}
                if start < 1 or end < 1:
                    return {"ok": False, "done": True, "error": "'start_line' e 'end_line' devem ser >= 1."}
                if start > end:
                    return {"ok": False, "done": True, "error": "'start_line' não pode ser maior que 'end_line'."}
                if start > total_lines or end > total_lines:
                    return {"ok": False, "done": True,
                            "error": f"Intervalo {start}-{end} fora do arquivo (total de {total_lines} linhas)."}

                new_lines = lines[:start - 1] + lines[end:]
                requested.write_text("".join(new_lines), encoding="utf-8")
                return {"ok": True, "done": True, "message": f"Linhas {start}-{end} removidas de '{file_path}'."}

            elif action == "ast_patch":
                target = args.get("target", "")
                new_code = args.get("new_code", "")
                old_hash = args.get("old_hash")
                if not target or not new_code:
                    return {"ok": False, "done": True, "error": "'target' e 'new_code' são obrigatórios para ast_patch."}
                if not requested.exists():
                    return {"ok": False, "done": True, "error": f"Arquivo '{file_path}' não existe."}
                return self._ast_patch(requested, target, new_code, old_hash)

            else:
                return {"ok": False, "done": True, "error": f"Ação desconhecida: '{action}'. Use 'write', 'append', 'patch' ou 'delete_lines'."}

        except Exception as e:
            logger.error(f"FileWriterSkill error: {e}", exc_info=True)
            return {"ok": False, "done": True, "error": f"Erro ao escrever arquivo: {e}"}