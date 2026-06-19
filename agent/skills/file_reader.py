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
        allowed_no_ext_names = {
            "makefile", "dockerfile", "procfile", "readme",
            "license", "notice", "authors", "changelog",
        }

        ext = requested.suffix.lower()
        name_lower = requested.name.lower()

        is_allowed = (
            ext in allowed_extensions
            or name_lower in allowed_extensions
            or name_lower in allowed_no_ext_names
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
                # Modo manual (intervalo de linhas)
                return self._read_lines(lines, start_line, end_line, total_lines, total_chars)
            else:
                # Modo automático: chunking com resumo para arquivos grandes
                return self._read_with_chunking_and_summary(requested, lines, total_lines, total_chars)

        except UnicodeDecodeError:
            return self._error("encoding inválido", f"O arquivo '{file_path}' não parece ser texto UTF-8.")
        except Exception as e:
            return self._error(str(e), f"Erro ao ler arquivo '{file_path}'.")

    def _read_lines(self, lines: list, start_line, end_line, total_lines: int, total_chars: int) -> dict:
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

    def _read_with_chunking_and_summary(self, requested: Path, lines: list, total_lines: int, total_chars: int) -> dict:
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

    def _generate_summary(self, requested: Path, lines: list, total_lines: int, total_chars: int, full_content: str) -> str:
        """
        Gera um resumo estruturado do arquivo.
        Este método pode ser estendido para usar a skill `summarize` via LLM.
        Por enquanto, gera um resumo baseado na estrutura.
        """
        # Extrai informações básicas
        imports = []
        functions = []
        classes = []
        in_multiline = False

        for line in lines:
            stripped = line.strip()
            # Coleta imports
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)
            # Coleta definições de função
            elif stripped.startswith("def "):
                func_name = stripped[4:].split("(")[0].strip()
                functions.append(func_name)
            # Coleta definições de classe
            elif stripped.startswith("class "):
                class_name = stripped[6:].split("(")[0].split(":")[0].strip()
                classes.append(class_name)

        summary_lines = [
            f"=== Resumo do arquivo: {requested.name} ===",
            f"Total: {total_lines} linhas, {total_chars} caracteres.",
            f"Imports encontrados: {len(imports)}",
        ]
        if imports:
            summary_lines.append("Primeiros imports:")
            for imp in imports[:10]:
                summary_lines.append(f"  {imp}")
        if functions:
            summary_lines.append(f"Funções definidas ({len(functions)}):")
            for func in functions[:20]:
                summary_lines.append(f"  - {func}")
        if classes:
            summary_lines.append(f"Classes definidas ({len(classes)}):")
            for cls in classes[:10]:
                summary_lines.append(f"  - {cls}")

        summary_lines.append(
            f"\nO conteúdo completo está disponível no arquivo temporário. "
            f"Use file_reader com start_line/end_line para ler trechos específicos."
        )
        return "\n".join(summary_lines)

    def _error(self, error: str, message: str) -> dict:
        return {"ok": False, "done": True, "error": error, "message": message}