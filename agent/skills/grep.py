import os
import re
from pathlib import Path
from typing import Any

from .base import BaseSkill
from .safe_path import resolve_safe_path


class GrepSkill(BaseSkill):
    name = "grep"
    description = "Busca por um padrão (texto ou regex) em arquivos dentro do diretório seguro."

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self) -> dict[str, Any]:
        return {
            "pattern": {
                "type": "string",
                "description": "Padrão a ser buscado (texto literal ou expressão regular)."
            },
            "path": {
                "type": "string",
                "description": "Caminho relativo do diretório ou arquivo onde buscar. Use '.' para o diretório raiz."
            },
            "recursive": {
                "type": "boolean",
                "description": "Se deve buscar recursivamente em subdiretórios. Padrão: true."
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Se a busca deve diferenciar maiúsculas de minúsculas. Padrão: true."
            },
            "max_results": {
                "type": "integer",
                "description": "Número máximo de resultados a retornar. Padrão: 20."
            },
            "exclude_dirs": {
                "type": "array",
                "description": "Lista de nomes de diretórios a excluir da busca. Padrão: ['.venv', '__pycache__', '.git', 'node_modules']."
            },
            "exclude_files": {
                "type": "array",
                "description": "Lista de nomes de arquivos a excluir da busca. Padrão: ['agent.log', 'agent_memory.json']."
            }
        }

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern", ""))
        if not pattern:
            return {"ok": False, "done": True, "error": "padrão vazio", "message": "Nenhum padrão de busca fornecido."}

        search_path = str(args.get("path", "."))
        recursive = bool(args.get("recursive", True))
        case_sensitive = bool(args.get("case_sensitive", True))
        raw_max_results = args.get("max_results", 20)
        max_results = raw_max_results if isinstance(raw_max_results, int) and raw_max_results > 0 else 20
        exclude_dirs = {str(item) for item in args.get("exclude_dirs", [".venv", "__pycache__", ".git", "node_modules"])}
        exclude_files = {str(item) for item in args.get("exclude_files", ["agent.log", "agent_memory.json"])}

        # Resolve caminho seguro
        requested, error = resolve_safe_path(self.base_dir, search_path)
        if error or requested is None:
            message = error or "Caminho inválido."
            return {"ok": False, "done": True, "error": message, "message": message}
        if not requested.exists():
            return {"ok": False, "done": True, "error": "não encontrado", "message": f"'{search_path}' não existe."}

        files_to_search = self._collect_files(requested, recursive, exclude_dirs)
        files_to_search = [path for path in files_to_search if self._is_searchable(path, exclude_files)]
        try:
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": "Expressão regular inválida."}
        results = self._search(regex, files_to_search, max_results)
        return {
            "ok": True,
            "done": True,
            "data": results,
            "total_matches": len(results),
            "truncated": len(results) >= max_results,
            "error": None,
            "message": f"{len(results)} correspondências encontradas (total: {len(results)}).",
        }

    @staticmethod
    def _collect_files(requested: Path, recursive: bool, exclude_dirs: set[str]) -> list[Path]:
        if requested.is_file():
            return [requested]
        if not requested.is_dir():
            return []
        if not recursive:
            return [path for path in requested.iterdir() if path.is_file()]
        discovered: list[Path] = []
        for root, directories, files in os.walk(requested):
            directories[:] = [name for name in directories if name not in exclude_dirs and not name.startswith(".")]
            discovered.extend(Path(root) / name for name in files)
        return discovered

    @staticmethod
    def _is_searchable(path: Path, exclude_files: set[str]) -> bool:
        text_extensions = {".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".html", ".css", ".js"}
        return path.suffix.lower() in text_extensions and path.name not in exclude_files

    def _search(self, regex: re.Pattern[str], files: list[Path], max_results: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for file_path in files:
            if len(results) >= max_results:
                break
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    for line_num, line in enumerate(handle, 1):
                        if regex.search(line):
                            results.append({
                                "file": str(file_path.relative_to(self.base_dir)),
                                "line": line_num,
                                "content": line.strip()[:200]
                            })
                            if len(results) >= max_results:
                                break
            except Exception:
                continue
        return results
