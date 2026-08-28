import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base import BaseSkill
from .file_reader_evidence import FileReaderEvidenceMixin
from .safe_path import resolve_safe_path


class FileReaderSkill(FileReaderEvidenceMixin, BaseSkill):
    name = "file_reader"
    description = (
        "Lê trechos de um arquivo de texto dentro do diretório seguro. "
        "Pode ler o arquivo inteiro (respeitando um limite de segurança) ou um intervalo de linhas específico."
    )

    def __init__(
        self,
        base_dir: str | Path = ".",
        max_chars: int = 5000,
        scratch_dir: str | Path | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.scratch_dir = (
            Path(scratch_dir).expanduser().resolve()
            if scratch_dir is not None
            else self.base_dir / ".temp_analysis"
        )
        self.max_chars = max_chars

    def get_schema(self) -> dict[str, Any]:
        return {
            "file_path": {"type": "string", "description": "Caminho relativo do arquivo."},
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Linha inicial (1-indexada) para leitura parcial. Opcional.",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Linha final (1-indexada) para leitura parcial. Opcional. Se omitido, lê até o final.",
            },
        }

    def validate_arguments(
        self,
        args: Mapping[str, Any],
        *,
        bound_fields: frozenset[str] = frozenset(),
        planning: bool = False,
    ) -> None:
        del planning
        start = args.get("start_line")
        end = args.get("end_line")
        if (
            "start_line" not in bound_fields
            and "end_line" not in bound_fields
            and start is not None
            and end is not None
            and start > end
        ):
            raise ValueError(f"'start_line' ({start}) cannot exceed 'end_line' ({end})")

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        file_path = str(args.get("file_path", ""))
        if not file_path:
            return self._error("caminho vazio", "Nenhum caminho de arquivo fornecido.")
        requested, error = resolve_safe_path(self.base_dir, file_path)
        if error or requested is None:
            return self._error(
                "acesso negado",
                error or "Caminho inválido.",
                error_code="PERMISSION_DENIED",
            )
        requested = self._workspace_version(requested)
        if not requested.exists():
            return self._error(
                "arquivo não encontrado",
                f"Arquivo '{file_path}' não existe.",
                error_code="FILE_NOT_FOUND",
            )
        if not requested.is_file():
            return self._error("não é um arquivo", f"'{file_path}' não é um arquivo regular.")
        type_error = self._file_type_error(requested)
        return type_error if type_error else self._read_requested(requested, file_path, args)

    def _workspace_version(self, requested: Path) -> Path:
        try:
            relative = requested.relative_to(self.base_dir)
        except ValueError:
            return requested
        workspace_copy = self.scratch_dir / "workspace" / relative
        return workspace_copy if workspace_copy.exists() else requested

    def _file_type_error(self, requested: Path) -> dict[str, Any] | None:
        allowed_extensions = {
            ".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml",
            ".html", ".css", ".js", ".ts", ".tsx", ".toml", ".ini", ".cfg",
            ".sh", ".env", ".xml", ".rst", ".gitignore", ".dockerignore",
            ".editorconfig",
        }
        allowed_names = {"makefile", "dockerfile", "procfile", "readme", "license", "notice", "authors", "changelog"}
        extension = requested.suffix.lower()
        name = requested.name.lower()
        if extension in allowed_extensions or name in allowed_extensions or name in allowed_names:
            return None
        return self._error(
            "tipo não permitido",
            f"Extensão não permitida: '{extension or name}' para '{requested.name}'.",
        )

    def _read_requested(self, requested: Path, file_path: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            full_content = requested.read_text(encoding="utf-8")
            total_chars = len(full_content)
            source_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
            lines = full_content.splitlines(keepends=True)
            total_lines = len(lines)
            start_line = args.get("start_line")
            end_line = args.get("end_line")
            if start_line is not None or end_line is not None:
                return self._read_lines(
                    lines,
                    start_line,
                    end_line,
                    total_lines,
                    total_chars,
                    source_identity=file_path,
                    source_hash=source_hash,
                )
            return self._read_with_chunking_and_summary(
                requested,
                lines,
                total_lines,
                total_chars,
                source_identity=file_path,
                source_hash=source_hash,
            )
        except UnicodeDecodeError:
            return self._error("encoding inválido", f"O arquivo '{file_path}' não parece ser texto UTF-8.")
        except Exception as exc:
            return self._error(str(exc), f"Erro ao ler arquivo '{file_path}'.")

    def _save_temp_copy(self, requested: Path, content: str) -> str:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.scratch_dir / requested.name
        try:
            temp_path.write_text(content, encoding="utf-8")
        except Exception:
            return "[não foi possível salvar cópia temporária]"
        try:
            return str(temp_path.relative_to(self.base_dir))
        except ValueError:
            return str(temp_path)

    def _generate_summary(
        self,
        requested: Path,
        lines: list[str],
        total_lines: int,
        total_chars: int,
        full_content: str,
    ) -> str:
        imports, functions, classes = self._summary_symbols(lines)
        summary_lines = [
            f"=== Resumo do arquivo: {requested.name} ===",
            f"Total: {total_lines} linhas, {total_chars} caracteres.",
            f"Imports encontrados: {len(imports)}",
        ]
        self._append_summary_group(summary_lines, "Primeiros imports:", imports, "  ", 10)
        self._append_summary_group(summary_lines, f"Funções definidas ({len(functions)}):", functions, "  - ", 20)
        self._append_summary_group(summary_lines, f"Classes definidas ({len(classes)}):", classes, "  - ", 10)
        summary_lines.append(
            "\nO conteúdo completo está disponível no arquivo temporário. "
            "Use file_reader com start_line/end_line para ler trechos específicos."
        )
        return "\n".join(summary_lines)

    @staticmethod
    def _summary_symbols(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
        imports: list[str] = []
        functions: list[str] = []
        classes: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)
            elif stripped.startswith("def "):
                functions.append(stripped[4:].split("(")[0].strip())
            elif stripped.startswith("class "):
                classes.append(stripped[6:].split("(")[0].split(":")[0].strip())
        return imports, functions, classes

    @staticmethod
    def _append_summary_group(
        output: list[str], title: str, values: list[str], prefix: str, limit: int
    ) -> None:
        if values:
            output.append(title)
            output.extend(f"{prefix}{value}" for value in values[:limit])

    def _error(
        self,
        error: str,
        message: str,
        *,
        error_code: str = "TOOL_ERROR",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "done": True,
            "error": error,
            "error_code": error_code,
            "message": message,
        }
