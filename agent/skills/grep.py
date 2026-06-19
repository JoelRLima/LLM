import os
import re
from pathlib import Path
from .base import BaseSkill

class GrepSkill(BaseSkill):
    name = "grep"
    description = "Busca por um padrão (texto ou regex) em arquivos dentro do diretório seguro."

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self):
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

    def execute(self, args: dict) -> dict:
        pattern = args.get("pattern", "")
        if not pattern:
            return {"ok": False, "done": True, "error": "padrão vazio", "message": "Nenhum padrão de busca fornecido."}

        search_path = args.get("path", ".")
        recursive = args.get("recursive", True)
        case_sensitive = args.get("case_sensitive", True)
        max_results = args.get("max_results", 20)
        exclude_dirs = args.get("exclude_dirs", [".venv", "__pycache__", ".git", "node_modules"])
        exclude_files = args.get("exclude_files", ["agent.log", "agent_memory.json"])

        # Resolve caminho seguro
        try:
            requested = (self.base_dir / search_path).resolve()
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": f"Caminho inválido: {search_path}"}

        if not str(requested).startswith(str(self.base_dir)):
            return {"ok": False, "done": True, "error": "acesso negado", "message": f"Acesso fora do diretório seguro: {search_path}"}
        if not requested.exists():
            return {"ok": False, "done": True, "error": "não encontrado", "message": f"'{search_path}' não existe."}

        # Lista de arquivos a percorrer
        files_to_search = []
        if requested.is_file():
            files_to_search = [requested]
        elif requested.is_dir():
            if recursive:
                for root, dirs, files in os.walk(requested):
                    # Exclui diretórios indesejados
                    dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
                    for file in files:
                        files_to_search.append(Path(root) / file)
            else:
                for f in os.listdir(requested):
                    full = requested / f
                    if full.is_file():
                        files_to_search.append(full)

        # Filtra apenas arquivos de texto
        text_extensions = {".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".html", ".css", ".js"}
        files_to_search = [f for f in files_to_search if f.suffix.lower() in text_extensions]

        # Exclui arquivos indesejados por nome
        files_to_search = [f for f in files_to_search if f.name not in exclude_files]

        # Compila o padrão
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Expressão regular inválida."}

        results = []
        total_matches = 0

        for file_path in files_to_search:
            if len(results) >= max_results:
                break
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append({
                                "file": str(file_path.relative_to(self.base_dir)),
                                "line": line_num,
                                "content": line.strip()[:200]
                            })
                            total_matches += 1
                            if len(results) >= max_results:
                                break
            except Exception:
                continue

        return {
            "ok": True,
            "done": True,
            "data": results,
            "total_matches": total_matches,
            "truncated": total_matches > max_results,
            "error": None,
            "message": f"{len(results)} correspondências encontradas (total: {total_matches})."
        }