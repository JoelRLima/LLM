import os
from pathlib import Path
from .base import BaseSkill

class FileReaderSkill(BaseSkill):
    name = "file_reader"
    description = (
        "Lê trechos de um arquivo de texto dentro do diretório seguro. "
        "Pode ler o arquivo inteiro (respeitando um limite de segurança) ou um intervalo de linhas específico."
    )

    def __init__(self, base_dir: str = ".", max_chars: int = 5000):
        self.base_dir = Path(base_dir).resolve()
        self.max_chars = max_chars

    def get_schema(self):
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

    def execute(self, args: dict) -> dict:
        file_path = args.get("file_path", "")
        if not file_path:
            return self._error("caminho vazio", "Nenhum caminho de arquivo fornecido.")

        try:
            requested = (self.base_dir / file_path).resolve()
        except Exception as e:
            return self._error(str(e), f"Caminho inválido: {file_path}")

        if not str(requested).startswith(str(self.base_dir)):
            return self._error("acesso negado", f"Acesso fora do diretório seguro: {file_path}")
        if not requested.exists():
            return self._error("arquivo não encontrado", f"Arquivo '{file_path}' não existe.")
        if not requested.is_file():
            return self._error("não é um arquivo", f"'{file_path}' não é um arquivo regular.")

        # Extensões permitidas para leitura
        allowed_extensions = {
            ".txt", ".md", ".py", ".json", ".csv", ".log",
            ".yaml", ".yml", ".html", ".css", ".js", ".ts", ".tsx",
            ".toml", ".ini", ".cfg", ".sh", ".env", ".xml", ".rst",
            ".gitignore", ".dockerignore", ".editorconfig",
        }
        # Arquivos sem extensão mas com nomes reconhecidos como texto
        allowed_no_ext_names = {
            "makefile", "dockerfile", "procfile", "readme",
            "license", "notice", "authors", "changelog",
        }

        ext = requested.suffix.lower()
        name_lower = requested.name.lower()

        # Para dotfiles (ex: .gitignore), Path.suffix retorna "" e Path.name retorna ".gitignore".
        # Por isso verificamos: extensão, nome completo (cobre dotfiles), ou nomes sem extensão conhecidos.
        is_allowed = (
            ext in allowed_extensions          # ex: config.toml -> ext=".toml"
            or name_lower in allowed_extensions  # ex: .gitignore -> name=".gitignore"
            or name_lower in allowed_no_ext_names  # ex: Makefile -> name="makefile"
        )
        if not is_allowed:
            return self._error("tipo não permitido", f"Extensão não permitida: '{ext or name_lower}' para '{requested.name}'.")

        try:
            with open(requested, "r", encoding="utf-8") as f:
                full_content = f.read()
            total_chars = len(full_content)
            lines = full_content.splitlines(keepends=True)
            total_lines = len(lines)

            start_line = args.get("start_line")
            end_line = args.get("end_line")

            if start_line is not None or end_line is not None:
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
                is_truncated = False
                message = f"Linhas {start_line}-{end_line} de {total_lines} lidas com sucesso. Caracteres: {len(content)}/{total_chars}."
            else:
                content = full_content
                is_truncated = len(content) > self.max_chars
                if is_truncated:
                    content = content[:self.max_chars] + f"\n... (truncado, {total_chars} caracteres no total)"
                message = (
                    f"Arquivo lido {'parcialmente (truncado)' if is_truncated else 'completamente'}. "
                    f"Linhas: {total_lines}, caracteres: {total_chars}."
                )

            return {
                "ok": True,
                "done": True,
                "data": content,
                "total_lines": total_lines,
                "total_chars": total_chars,
                "truncated": is_truncated,
                "error": None,
                "message": message
            }

        except UnicodeDecodeError:
            return self._error("encoding inválido", f"O arquivo '{file_path}' não parece ser texto UTF-8.")
        except Exception as e:
            return self._error(str(e), f"Erro ao ler arquivo '{file_path}'.")

    def _error(self, error: str, message: str) -> dict:
        return {"ok": False, "done": True, "error": error, "message": message}