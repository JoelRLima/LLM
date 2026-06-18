import os
import subprocess
import shlex
from pathlib import Path
from typing import Any, Dict
from .base import BaseSkill
from logger import logger

# Comandos permitidos por prefixo
ALLOWED_COMMANDS = {
    "pytest", "python", "pip", "ruff", "mypy",
    "git status", "git log", "git diff", "git add", "git commit",
    "npm", "node", "echo", "type", "dir", "tree", "ls",
}

def _is_command_allowed(cmd: str) -> bool:
    cmd_lower = cmd.strip().lower()
    for allowed in ALLOWED_COMMANDS:
        if cmd_lower == allowed or cmd_lower.startswith(allowed + " "):
            return True
    return False

class ShellSkill(BaseSkill):
    name = "shell"
    description = (
        "Executa comandos de terminal permitidos: pytest, python, pip, ruff, mypy, git (status/log/diff/add/commit), npm/node. "
        "NÃO executa comandos destrutivos como rm, del, format, etc."
    )

    def __init__(self, base_dir: str = ".", timeout: int = 30) -> None:
        self.base_dir = str(Path(base_dir).resolve())
        self.timeout = timeout

    def get_schema(self) -> Dict[str, Any]:
        return {
            "command": "string: o comando completo a executar (ex: 'pytest tests/', 'git status', 'pip install requests')",
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        command = args.get("command", "").strip()
        if not command:
            return {"ok": False, "done": True, "error": "Nenhum comando fornecido."}

        if not _is_command_allowed(command):
            return {
                "ok": False, "done": True,
                "error": f"Comando não permitido: '{command}'. Apenas pytest, python, pip, ruff, mypy, git (leitura/commit) e npm são permitidos."
            }

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.base_dir,
            )
            output = result.stdout
            if result.stderr:
                output += ("\n[stderr]\n" + result.stderr)

            ok = result.returncode == 0
            return {
                "ok": ok,
                "done": True,
                "data": output.strip() or "(sem saída)",
                "error": None if ok else f"Exit code {result.returncode}",
                "message": "Comando executado com sucesso." if ok else f"Comando falhou (exit {result.returncode})."
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "done": True, "error": f"Timeout após {self.timeout}s."}
        except Exception as e:
            logger.error(f"ShellSkill error: {e}", exc_info=True)
            return {"ok": False, "done": True, "error": str(e)}
