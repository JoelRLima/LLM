"""Compatibility skill for file, directory, and security source analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from llm_agent.agent.code.intelligence import CodeIntelligenceService
from llm_agent.agent.tools.result_completeness import (
    EvidenceProvenance,
    legacy_result_successful,
)

from .base import BaseSkill
from .python_security_analysis import analyze_security_file
from .python_source_analysis import analyze_python_file
from .safe_path import resolve_confined_file, resolve_safe_path
from .security_symbols import SECURITY_SYMBOL_REGISTRY, get_pattern_id_map

__all__ = ["CodeAnalyzerSkill", "SECURITY_SYMBOL_REGISTRY", "get_pattern_id_map"]

MAX_SOURCE_BYTES = 1_000_000
MAX_CODE_INCLUDED_SOURCE_BYTES = 256_000
MAX_DIRECTORY_FILES = 200
MAX_DIRECTORY_SCAN_ENTRIES = 20_000
MAX_DIRECTORY_OUTPUT_BYTES = 2 * 1024 * 1024


class CodeAnalyzerSkill(BaseSkill):
    name = "code_analyzer"
    description = (
        "Analisa estrutura de código com adapter de linguagem. mode=directory "
        "serve para um mapa estrutural; compact=true retorna estrutura resumida "
        "DERIVED_LOSSY e não prova valores literais ou constantes. Para conteúdo "
        "ou valores exatos use file_reader. O mapa de diretório pode omitir "
        "subdiretórios excluídos e arquivos cuja análise falhou; use mode=file "
        "quando a observação de um descendente específico for necessária. "
        "Python recebe análise AST; outras linguagens têm fallback textual "
        "explicitamente identificado. A analise de diretorio e limitada e "
        "informa truncated, skipped_large_files e failed_files."
    )

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.intelligence = CodeIntelligenceService(self.base_dir)

    def get_schema(self) -> dict[str, Any]:
        return {
            "target": {"type": "string", "description": "Caminho relativo do arquivo ou diretório."},
            "mode": {
                "type": "string",
                "description": (
                    "Modo file, directory ou security; directory é um mapa "
                    "agregado e não substitui automaticamente a análise de um child."
                ),
                "enum": ["file", "directory", "security"],
            },
            "include_code": {
                "type": "boolean",
                "description": "Inclui o codigo fonte quando o arquivo cabe no limite de analise.",
            },
            "compact": {
                "type": "boolean",
                "description": "Retorna somente a estrutura essencial (DERIVED_LOSSY); não contém valores literais.",
            },
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
            max_source_bytes = MAX_CODE_INCLUDED_SOURCE_BYTES if include_code else MAX_SOURCE_BYTES
            if file_path.stat().st_size > max_source_bytes:
                return self._error(
                    "source file exceeds analysis size limit",
                    f"Maximum source size is {max_source_bytes} bytes for this analysis mode.",
                )
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
            "evidence_provenance": EvidenceProvenance.DERIVED_LOSSY.value,
            "source_extent": {"kind": "analysis"},
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
            "evidence_provenance": EvidenceProvenance.DERIVED_LOSSY.value,
            "source_extent": {"kind": "analysis"},
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
        files, truncated = self._python_files(dir_path)
        skipped_large_files = 0
        failed_files = 0
        output_bytes = 0
        max_source_bytes = MAX_CODE_INCLUDED_SOURCE_BYTES if include_code else MAX_SOURCE_BYTES
        for file_path in files:
            try:
                if file_path.stat().st_size > max_source_bytes:
                    skipped_large_files += 1
                    truncated = True
                    continue
            except OSError:
                truncated = True
                continue
            relative = str(file_path.relative_to(self.base_dir))
            result = self._analyze_file(file_path, include_code, compact)
            if not legacy_result_successful(result):
                failed_files += 1
                continue
            serialized_size = len(
                json.dumps(result["data"], ensure_ascii=False, default=str).encode("utf-8")
            )
            if output_bytes + serialized_size > MAX_DIRECTORY_OUTPUT_BYTES:
                truncated = True
                break
            project_map[relative] = result["data"]
            output_bytes += serialized_size
            if not compact:
                self._collect_dependencies(result["data"], relative, dependencies)
        data: dict[str, Any] = {
            "files": project_map,
            "total_files": len(project_map),
            "truncated": truncated,
            "skipped_large_files": skipped_large_files,
            "failed_files": failed_files,
        }
        if not compact:
            data["dependencies"] = dependencies
        return {
            "evidence_provenance": EvidenceProvenance.DERIVED_LOSSY.value,
            "source_extent": {"kind": "analysis"},
            "ok": True,
            "done": True,
            "data": data,
            "error": None,
            "message": f"Mapa gerado com {len(project_map)} arquivos (modo {'compacto' if compact else 'completo'}).",
        }

    def _python_entry(self, entry: Any, ignored: set[str]) -> tuple[Path | None, Path | None, bool]:
        if entry.name.startswith(".") or entry.name in ignored:
            return None, None, False
        try:
            if entry.is_dir(follow_symlinks=False):
                return None, Path(entry.path), False
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".py"):
                return None, None, False
        except OSError:
            return None, None, True
        candidate = resolve_confined_file(self.base_dir, Path(entry.path))
        return candidate, None, False



    def _python_files(self, directory: Path) -> tuple[list[Path], bool]:
        discovered: list[Path] = []
        ignored = {"__pycache__", "venv", "env", "node_modules", "build", "dist"}
        pending = [directory]
        scanned_entries = 0
        truncated = False
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        scanned_entries += 1
                        if scanned_entries > MAX_DIRECTORY_SCAN_ENTRIES:
                            return sorted(discovered, key=lambda path: path.as_posix()), True
                        candidate, child, failed = self._python_entry(entry, ignored)
                        truncated = truncated or failed
                        if child is not None:
                            pending.append(child)
                        if candidate is None:
                            continue
                        if len(discovered) >= MAX_DIRECTORY_FILES:
                            return sorted(discovered, key=lambda path: path.as_posix()), True
                        discovered.append(candidate)
            except OSError:
                truncated = True
        return sorted(discovered, key=lambda path: path.as_posix()), truncated

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
            if file_path.stat().st_size > MAX_SOURCE_BYTES:
                return self._error(
                    "source file exceeds analysis size limit",
                    f"Maximum source size is {MAX_SOURCE_BYTES} bytes.",
                )
            data = analyze_security_file(file_path, self.base_dir)
        except SyntaxError as exc:
            return self._error(str(exc), "Erro de sintaxe no arquivo.")
        except (OSError, ValueError) as exc:
            return self._error(str(exc), "Erro ao ler/parsear o arquivo.")
        return {
            "evidence_provenance": EvidenceProvenance.DERIVED_LOSSY.value,
            "source_extent": {"kind": "analysis"},
            "ok": True,
            "done": True,
            "data": data,
            "error": None,
            "message": f"Extração de segurança concluída: {data['total_facts']} fatos observáveis encontrados.",
        }
