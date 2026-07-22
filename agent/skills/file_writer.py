from __future__ import annotations

import ast
import hashlib
import textwrap
from pathlib import Path
from typing import Any, Dict

from agent.runtime.logging import logger

from .base import BaseSkill
from .file_writer_runtime import (
    apply_edit,
    confirm_protected_edit,
    prepare_workspace,
    review_and_commit,
)

AGENT_CORE_DIR = "agent/"
AGENT_EDIT_ALLOWLIST: set[str] = set()


def _is_auto_confirm() -> bool:
    """Lê a preferência headless; falhas mantêm o padrão interativo seguro."""
    try:
        from agent.runtime.config import carregar_config
        config = carregar_config()
    except Exception:
        return False
    return bool(config.get("auto_confirm", False))


class FileWriterSkill(BaseSkill):
    name = "file_writer"
    description = (
        "Cria ou edita arquivos de texto no projeto por escrita, append, patch, "
        "remoção de linhas ou substituição de símbolo Python."
    )

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def _get_workspace_path(self, original_path: Path) -> str:
        relative = original_path.relative_to(self.base_dir)
        workspace = self.base_dir / ".temp_analysis" / "workspace" / relative
        workspace.parent.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def _invalidate_cache(self, file_path: str) -> None:
        try:
            cached = self.base_dir / ".temp_analysis" / file_path
            workspace = Path(self._get_workspace_path(self.base_dir / file_path))
            for candidate in (cached, workspace):
                if candidate.exists():
                    candidate.unlink()
        except Exception as exc:
            logger.warning("Falha ao invalidar cache para '%s': %s", file_path, exc)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "action": "string: write, patch, append, delete_lines ou ast_patch",
            "file_path": "string: caminho relativo do arquivo",
            "content": "string: conteúdo para write/append",
            "old_content": "string: trecho exato para patch",
            "new_content": "string: substituição para patch",
            "start_line": "integer: linha inicial para delete_lines",
            "end_line": "integer: linha final para delete_lines",
            "target": "string: função ou classe para ast_patch",
            "new_code": "string: novo código completo para ast_patch",
            "old_hash": "string: SHA256 anterior opcional",
        }

    def _is_safe(self, requested: Path) -> tuple[bool, str]:
        try:
            relative = requested.relative_to(self.base_dir)
        except ValueError:
            return False, "Acesso fora do diretório do projeto não é permitido."
        relative_text = relative.as_posix()
        if relative_text.startswith(AGENT_CORE_DIR) and relative_text not in AGENT_EDIT_ALLOWLIST:
            return False, f"'{relative_text}' é um arquivo core do agente e não pode ser modificado."
        blocked = {".exe", ".dll", ".so", ".pyc", ".bin", ".jpg", ".png", ".zip", ".tar"}
        if requested.suffix.lower() in blocked:
            return False, f"Extensão '{requested.suffix}' não é permitida para escrita."
        return True, ""

    @staticmethod
    def _find_symbol(tree: ast.AST, target: str) -> ast.FunctionDef | ast.ClassDef | None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == target:
                return node
        return None

    def _ast_patch(self, file_path: Path, target: str, new_code: str, old_hash: str | None = None) -> Dict[str, Any]:
        try:
            original = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": "Erro ao ler arquivo para patch."}
        if old_hash and hashlib.sha256(original.encode("utf-8")).hexdigest() != old_hash:
            return {"ok": False, "done": True, "error": "hash mismatch", "message": "Hash não confere."}
        try:
            tree = ast.parse(original)
        except SyntaxError as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": "Arquivo com erro de sintaxe."}
        symbol = self._find_symbol(tree, target)
        if symbol is None:
            return {"ok": False, "done": True, "error": "target not found", "message": f"'{target}' não encontrado."}
        lines = original.splitlines(keepends=True)
        start = symbol.lineno - 1
        end = (symbol.end_lineno or symbol.lineno) - 1
        indentation = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        block = textwrap.indent(textwrap.dedent(new_code).strip("\n"), indentation) + "\n"
        proposed = "".join(lines[:start] + [block] + lines[end + 1 :])
        try:
            ast.parse(proposed)
            file_path.write_text(proposed, encoding="utf-8")
        except (SyntaxError, OSError) as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": "Novo código inválido ou não gravável."}
        return {"ok": True, "done": True, "message": f"'{target}' substituído com sucesso em '{file_path}'."}

    def execute(self, args: Dict[str, Any]) -> Any:
        file_path = args.get("file_path", "")
        if not file_path:
            return {"ok": False, "done": True, "error": "Nenhum file_path fornecido."}
        try:
            requested = (self.base_dir / str(file_path)).resolve()
            safe, reason = self._is_safe(requested)
            if not safe:
                return {"ok": False, "done": True, "error": f"Escrita bloqueada: {reason}"}
            denied = confirm_protected_edit(str(file_path), requested, _is_auto_confirm)
            if denied is not None:
                return denied
            workspace, error = prepare_workspace(requested, self._get_workspace_path(requested))
            if error is not None or workspace is None:
                return error
            operation_error = apply_edit(str(args.get("action", "write")), workspace, args, self._ast_patch)
            if operation_error is not None:
                return operation_error
            return review_and_commit(requested, workspace, str(file_path), _is_auto_confirm, self._invalidate_cache)
        except Exception as exc:
            logger.error("FileWriterSkill error: %s", exc, exc_info=True)
            return {"ok": False, "done": True, "error": f"Erro ao escrever arquivo: {exc}"}
