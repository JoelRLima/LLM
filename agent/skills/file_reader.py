from pathlib import Path
from typing import Any

from .base import BaseSkill
from .safe_path import resolve_safe_path


class FileReaderSkill(BaseSkill):
    name = "file_reader"
    description = (
        "Lê trechos de um arquivo de texto dentro do diretório seguro. "
        "Pode ler o arquivo inteiro (respeitando um limite de segurança) ou um intervalo de linhas específico."
    )

    def __init__(self, base_dir: str = ".", max_chars: int = 5000) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.max_chars = max_chars

    def get_schema(self) -> dict[str, Any]:
        return {
            "file_path": {
                "type": "string",
                "description": "Caminho relativo do arquivo."
            },
            "start_line": {
                "type": "integer",
                "description": "Linha inicial (1-indexada) para leitura parcial. Opcional."
            },
            "end_line": {
                "type": "integer",
                "description": "Linha final (1-indexada) para leitura parcial. Opcional. Se omitido, lê até o final."
            }
        }

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        file_path = str(args.get("file_path", ""))
        if not file_path:
            return self._error("caminho vazio", "Nenhum caminho de arquivo fornecido.")

        requested, error = resolve_safe_path(self.base_dir, file_path)
        if error or requested is None:
            return self._error("acesso negado", error or "Caminho inválido.")

        requested = self._workspace_version(requested)
        if not requested.exists():
            return self._error("arquivo não encontrado", f"Arquivo '{file_path}' não existe.")
        if not requested.is_file():
            return self._error("não é um arquivo", f"'{file_path}' não é um arquivo regular.")

        type_error = self._file_type_error(requested)
        if type_error:
            return type_error
        return self._read_requested(requested, file_path, args)

    def _workspace_version(self, requested: Path) -> Path:
        try:
            relative = requested.relative_to(self.base_dir)
        except ValueError:
            return requested
        workspace_copy = self.base_dir / ".temp_analysis" / "workspace" / relative
        return workspace_copy if workspace_copy.exists() else requested

    def _file_type_error(self, requested: Path) -> dict[str, Any] | None:
        allowed_extensions = {
            ".txt", ".md", ".py", ".json", ".csv", ".log",
            ".yaml", ".yml", ".html", ".css", ".js", ".ts", ".tsx",
            ".toml", ".ini", ".cfg", ".sh", ".env", ".xml", ".rst",
            ".gitignore", ".dockerignore", ".editorconfig",
        }
        allowed_no_ext_names = {
            "makefile", "dockerfile", "procfile", "readme",
            "license", "notice", "authors", "changelog",
        }

        extension = requested.suffix.lower()
        name = requested.name.lower()
        if extension in allowed_extensions or name in allowed_extensions or name in allowed_no_ext_names:
            return None
        return self._error("tipo não permitido", f"Extensão não permitida: '{extension or name}' para '{requested.name}'.")

    def _read_requested(
        self,
        requested: Path,
        file_path: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            with open(requested, "r", encoding="utf-8") as f:
                full_content = f.read()
            total_chars = len(full_content)
            lines = full_content.splitlines(keepends=True)
            total_lines = len(lines)

            start_line = args.get("start_line")
            end_line = args.get("end_line")

            if start_line is not None or end_line is not None:
                return self._read_lines(lines, start_line, end_line, total_lines, total_chars)
            return self._read_with_chunking_and_summary(requested, lines, total_lines, total_chars)

        except UnicodeDecodeError:
            return self._error("encoding inválido", f"O arquivo '{file_path}' não parece ser texto UTF-8.")
        except Exception as e:
            return self._error(str(e), f"Erro ao ler arquivo '{file_path}'.")

    def _read_lines(
        self,
        lines: list[str],
        start_line: int | None,
        end_line: int | None,
        total_lines: int,
        total_chars: int,
    ) -> dict[str, Any]:
        """Leitura de um intervalo específico de linhas."""
        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = total_lines
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return self._error("parâmetros inválidos", "start_line e end_line devem ser números inteiros.")
        if start_line < 1:
            start_line = 1
        if end_line > total_lines:
            end_line = total_lines
        if start_line > end_line:
            return self._error("intervalo inválido", "start_line não pode ser maior que end_line.")

        content = "".join(lines[start_line - 1:end_line])
        message = f"Linhas {start_line}-{end_line} de {total_lines} lidas com sucesso. Caracteres: {len(content)}/{total_chars}."
        return {
            "ok": True,
            "done": True,
            "data": content,
            "total_lines": total_lines,
            "total_chars": total_chars,
            "truncated": False,
            "error": None,
            "message": message
        }

    def _read_with_chunking_and_summary(
        self,
        requested: Path,
        lines: list[str],
        total_lines: int,
        total_chars: int,
    ) -> dict[str, Any]:
        """
        Lê o arquivo completo em chunks automáticos.
        Se o arquivo for grande, gera um resumo e salva o conteúdo completo
        em um arquivo temporário (opcional), evitando sobrecarregar o contexto.
        """
        CHUNK_SIZE = 100
        start = 1
        all_content = []

        # Coleta o conteúdo em chunks
        while start <= total_lines:
            end = min(start + CHUNK_SIZE - 1, total_lines)
            result = self._read_lines(lines, start, end, total_lines, total_chars)
            if not result.get("ok"):
                # Se falhar no primeiro chunk, retorna erro
                if start == 1:
                    return result
                break

            all_content.append(result.get("data", ""))
            start += CHUNK_SIZE

        full_content = "".join(all_content)

        # Se o conteúdo for pequeno, retorna diretamente
        if total_chars <= self.max_chars:
            return {
                "ok": True,
                "done": True,
                "data": full_content,
                "total_lines": total_lines,
                "total_chars": total_chars,
                "truncated": False,
                "error": None,
                "message": f"Arquivo lido completamente. Linhas: {total_lines}, caracteres: {total_chars}."
            }

        # Para arquivos grandes: gera um resumo e salva o conteúdo em disco
        temp_path = self._save_temp_copy(requested, full_content)

        # Gera um resumo simples (pode ser substituído por chamada ao summarize skill)
        summary = self._generate_summary(requested, lines, total_lines, total_chars, full_content)

        return {
            "ok": True,
            "done": True,
            "data": summary,
            "total_lines": total_lines,
            "total_chars": total_chars,
            "truncated": False,
            "error": None,
            "message": (
                f"Arquivo lido em {len(all_content)} chunk(s). "
                f"Devido ao tamanho ({total_chars} caracteres), um resumo foi gerado. "
                f"O conteúdo completo está disponível em '{temp_path}'. "
                f"Use file_reader com start_line/end_line para ler trechos específicos."
            )
        }

    def _save_temp_copy(self, requested: Path, content: str) -> str:
        """Salva uma cópia temporária do conteúdo em disco."""
        temp_dir = self.base_dir / ".temp_analysis"
        temp_dir.mkdir(exist_ok=True)
        temp_path = temp_dir / requested.name
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
            return str(temp_path.relative_to(self.base_dir))
        except Exception:
            return "[não foi possível salvar cópia temporária]"

    def _generate_summary(
        self,
        requested: Path,
        lines: list[str],
        total_lines: int,
        total_chars: int,
        full_content: str,
    ) -> str:
        """
        Gera um resumo estruturado do arquivo.
        Este método pode ser estendido para usar a skill `summarize` via LLM.
        Por enquanto, gera um resumo baseado na estrutura.
        """
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
        output: list[str],
        title: str,
        values: list[str],
        prefix: str,
        limit: int,
    ) -> None:
        if not values:
            return
        output.append(title)
        output.extend(f"{prefix}{value}" for value in values[:limit])

    def _error(self, error: str, message: str) -> dict[str, Any]:
        return {"ok": False, "done": True, "error": error, "message": message}
