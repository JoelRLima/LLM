import os
from pathlib import Path
from typing import Any

from .base import BaseSkill
from .safe_path import resolve_safe_path


class DirectoryListerSkill(BaseSkill):
    name = "directory_lister"
    description = "Lista arquivos e pastas em um diretório dentro do diretório seguro."

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self) -> dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Caminho relativo do diretório a ser listado. Use '.' para o diretório raiz."
            }
        }

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        dir_path = args.get("path", ".")
        if not dir_path:
            return {
                "ok": False,
                "done": True,
                "error": "caminho vazio",
                "message": "Nenhum caminho fornecido."
            }

        # Resolve caminho seguro
        requested, error = resolve_safe_path(self.base_dir, dir_path)
        if error or requested is None:
            message = error or "Caminho inválido."
            return {
                "ok": False,
                "done": True,
                "error": message,
                "message": message
            }

        if not requested.exists():
            return {
                "ok": False,
                "done": True,
                "error": "diretório não encontrado",
                "message": f"O diretório '{dir_path}' não existe."
            }

        if not requested.is_dir():
            return {
                "ok": False,
                "done": True,
                "error": "não é um diretório",
                "message": f"'{dir_path}' não é um diretório."
            }

        try:
            items = os.listdir(requested)
            # Monta lista com tipo (arquivo/pasta)
            listing: list[dict[str, str]] = []
            for item in sorted(items):
                full = requested / item
                listing.append({
                    "name": item,
                    "type": "dir" if full.is_dir() else "file"
                })
            return {
                "ok": True,
                "done": True,
                "data": listing,
                "error": None,
                "message": f"{len(listing)} itens encontrados em '{dir_path}'."
            }
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": f"Erro ao listar diretório: {e}"
            }
