import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict

from agent.runtime.logging import logger

from .base import BaseSkill

# A allowlist é comparada por tokens completos e a execução não usa shell.
# Essa combinação é uma invariável de segurança: operadores como `;` e `|`
# nunca podem ser interpretados pelo sistema operacional.
ALLOWED_COMMANDS = {
    "pytest", "ruff", "mypy",
    "git status", "git log", "git diff",
    "echo", "type", "dir", "tree", "ls",
}

# Pré-calcula o conjunto de comandos allowlisted de um único token e o
# mapeamento de comandos allowlisted de dois tokens, para comparação O(1).
_SINGLE_TOKEN_ALLOWED = {c for c in ALLOWED_COMMANDS if " " not in c}
_TWO_TOKEN_ALLOWED = {tuple(c.split(" ", 1)) for c in ALLOWED_COMMANDS if " " in c}

# ----------------------------------------------------------------------
# Limite de caracteres para a saída da ferramenta
# ----------------------------------------------------------------------
MAX_OUTPUT_CHARS = 4000


def _split_command(command: str) -> list[str] | None:
    """Tokeniza `command` com shlex. Retorna None se a sintaxe for inválida
    (ex.: aspas não fechadas) — nesse caso o comando é rejeitado."""
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _is_command_allowed(tokens: list[str]) -> bool:
    """Verifica se os tokens já divididos por shlex correspondem
    *exatamente* (não apenas por prefixo de string) a um comando
    permitido, considerando comandos de um ou dois tokens."""
    if not tokens:
        return False
    first = tokens[0].lower()
    if first in _SINGLE_TOKEN_ALLOWED:
        return True
    if len(tokens) >= 2:
        first_two = (first, tokens[1].lower())
        if first_two in _TWO_TOKEN_ALLOWED:
            return True
    return False


class ShellSkill(BaseSkill):
    name = "shell"
    description = (
        "Executa comandos de inspeção e validação permitidos: pytest, ruff, mypy, git (status/log/diff), listagem e echo. "
        "NÃO executa comandos destrutivos como rm, del, format, etc."
    )

    def __init__(self, base_dir: str = ".", timeout: int = 30) -> None:
        self.base_dir = str(Path(base_dir).resolve())
        self.timeout = timeout

    def get_schema(self) -> Dict[str, Any]:
        return {
            "command": "string: o comando completo a executar (ex: 'pytest tests/', 'git status')",
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        command = args.get("command", "").strip()
        if not command:
            return {"ok": False, "done": True, "error": "Nenhum comando fornecido."}

        tokens = _split_command(command)
        if tokens is None:
            return {
                "ok": False, "done": True,
                "error": "Comando com sintaxe inválida (aspas não fechadas ou similar)."
            }

        if not _is_command_allowed(tokens):
            return {
                "ok": False, "done": True,
                "error": f"Comando não permitido: '{command}'. Apenas validação, listagem e operações Git somente-leitura são permitidas."
            }

        try:
            result = subprocess.run(
                tokens,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.base_dir,
            )
            output = result.stdout
            if result.stderr:
                output += ("\n[stderr]\n" + result.stderr)

            # -------------------------------------------------------------
            # Truncamento de saída (Item 1.2)
            # -------------------------------------------------------------
            total_chars = len(output)
            if total_chars > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + (
                    f"\n... (output truncado, {total_chars} caracteres no total)"
                )
                trunc_msg = f" (truncado, {total_chars} caracteres)"
            else:
                trunc_msg = ""

            ok = result.returncode == 0
            return {
                "ok": ok,
                "done": True,
                "data": output.strip() or "(sem saída)",
                "error": None if ok else f"Exit code {result.returncode}",
                "message": (
                    "Comando executado com sucesso." if ok
                    else f"Comando falhou (exit {result.returncode})."
                ) + trunc_msg,
            }
        except FileNotFoundError:
            return {
                "ok": False, "done": True,
                "error": f"Executável '{tokens[0]}' não encontrado no PATH."
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "done": True, "error": f"Timeout após {self.timeout}s."}
        except Exception as e:
            logger.error(f"ShellSkill error: {e}", exc_info=True)
            return {"ok": False, "done": True, "error": str(e)}
