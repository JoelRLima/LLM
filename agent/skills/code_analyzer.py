"""Compatibility skill for file, directory, and security source analysis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.code.intelligence import CodeIntelligenceService

from .base import BaseSkill
from .python_security_analysis import analyze_security_file
from .python_source_analysis import analyze_python_file
from .safe_path import resolve_safe_path
from .security_symbols import SECURITY_SYMBOL_REGISTRY, get_pattern_id_map

__all__ = ["CodeAnalyzerSkill", "SECURITY_SYMBOL_REGISTRY", "get_pattern_id_map"]


class CodeAnalyzerSkill(BaseSkill):
    name = "code_analyzer"
    description = (
        "Analisa código com adapter de linguagem. Python recebe análise AST; "
        "outras linguagens têm fallback textual explicitamente identificado."
    )

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.intelligence = CodeIntelligenceService(self.base_dir)

    def get_schema(self) -> dict[str, Any]:
        return {
            "target": {"type": "string", "description": "Caminho relativo do arquivo ou diretório."},
            "mode": {
                "type": "string",
                "description": "Modo file, directory ou security.",
                "enum": ["file", "directory", "security"],
            },
            "include_code": {"type": "boolean", "description": "Inclui o código fonte completo."},
            "compact": {"type": "boolean", "description": "Retorna somente a estrutura essencial."},
        }

    @staticmethod
    def _error(error: str, message: str) -> dict[str, Any]:
        return {"ok": False, "done": True, "error": error, "message": message}

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target", ""))
        if not target:
            return self._error("alvo vazio", "Nenhum caminho fornecido.")
        requested, error = resolve_safe_path(self.base_dir, target)
        if error or requested is None:
            message = error or "Caminho inválido."
            return self._error(message, message)
        mode = str(args.get("mode", "file"))
        include_code = bool(args.get("include_code", False))
        compact = bool(args.get("compact", False))
        if mode == "file":
            return self._analyze_file(requested, include_code, compact)
        if mode == "directory":
            return self._analyze_directory(requested, include_code, compact)
        if mode == "security":
            return self._analyze_security(requested)
        return self._error("modo inválido", "Use 'file', 'directory' ou 'security'.")

    def _analyze_file(
        self,
        file_path: Path,
        include_code: bool = False,
        compact: bool = False,
    ) -> dict[str, Any]:
        if not file_path.is_file():
            return self._error("não é arquivo", f"'{file_path}' não é um arquivo.")
        if file_path.suffix != ".py":
            return self._analyze_with_adapter(file_path)
        try:
            data = analyze_python_file(
                file_path,
                self.base_dir,
                include_code=include_code,
                compact=compact,
            )
        except SyntaxError as exc:
            return self._error(str(exc), "Erro de sintaxe no arquivo.")
        except (OSError, ValueError) as exc:
            return self._error(str(exc), "Erro ao ler/parsear o arquivo.")
        functions = data["functions"]
        classes = data["classes"]
        imports = data.get("imports", [])
        mode_label = " (modo compacto)" if compact else ""
        return {
            "ok": True,
            "done": True,
            "data": data,
            "error": None,
            "message": f"Analisado: {len(functions)} funções, {len(classes)} classes, {len(imports)} imports{mode_label}.",
        }

    def _analyze_with_adapter(self, file_path: Path) -> dict[str, Any]:
        relative = file_path.relative_to(self.base_dir).as_posix()
        try:
            analysis = self.intelligence.analyze_file(relative)
        except (OSError, ValueError) as exc:
            return self._error(str(exc), "Não foi possível analisar o arquivo.")
        return {
            "ok": True,
            "done": True,
            "data": analysis.to_dict(),
            "error": None,
            "message": (
                f"Análise {analysis.level.value} por adapter '{analysis.language}' "
                f"(confiança {analysis.confidence:.2f})."
            ),
        }

    def _analyze_directory(
        self,
        dir_path: Path,
        include_code: bool = False,
        compact: bool = False,
    ) -> dict[str, Any]:
        if not dir_path.is_dir():
            return self._error("não é diretório", f"'{dir_path}' não é um diretório.")
        project_map: dict[str, Any] = {}
        dependencies: dict[str, list[str]] = {}
        for file_path in self._python_files(dir_path):
            relative = str(file_path.relative_to(self.base_dir))
            result = self._analyze_file(file_path, include_code, compact)
            if result.get("ok") is not True:
                continue
            project_map[relative] = result["data"]
            if not compact:
                self._collect_dependencies(result["data"], relative, dependencies)
        data: dict[str, Any] = {"files": project_map, "total_files": len(project_map)}
        if not compact:
            data["dependencies"] = dependencies
        return {
            "ok": True,
            "done": True,
            "data": data,
            "error": None,
            "message": f"Mapa gerado com {len(project_map)} arquivos (modo {'compacto' if compact else 'completo'}).",
        }

    @staticmethod
    def _python_files(directory: Path) -> list[Path]:
        discovered: list[Path] = []
        ignored = {"__pycache__", "venv", "env", "node_modules", "build", "dist"}
        for root, directories, files in os.walk(directory):
            directories[:] = [name for name in directories if not name.startswith(".") and name not in ignored]
            discovered.extend(Path(root) / name for name in files if name.endswith(".py"))
        return discovered

    @staticmethod
    def _collect_dependencies(data: Any, relative: str, dependencies: dict[str, list[str]]) -> None:
        if not isinstance(data, dict):
            return
        imports = data.get("imports", [])
        if not isinstance(imports, list):
            return
        for imported in imports:
            base = str(imported).split(".")[0]
            dependencies.setdefault(base, []).append(relative)

    def _analyze_security(self, file_path: Path) -> dict[str, Any]:
        if not file_path.is_file():
            return self._error("não é arquivo", f"'{file_path}' não é um arquivo.")
        if file_path.suffix != ".py":
            return self._error("tipo não suportado", "Apenas arquivos .py são analisados.")
        try:
            data = analyze_security_file(file_path, self.base_dir)
        except SyntaxError as exc:
            return self._error(str(exc), "Erro de sintaxe no arquivo.")
        except (OSError, ValueError) as exc:
            return self._error(str(exc), "Erro ao ler/parsear o arquivo.")
        return {
            "ok": True,
            "done": True,
            "data": data,
            "error": None,
            "message": f"Extração de segurança concluída: {data['total_facts']} fatos observáveis encontrados.",
        }
