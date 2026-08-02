import subprocess
from pathlib import Path
from typing import Any, Dict

from agent.approval import (
    ApprovalDecision,
    ApprovalPort,
    ApprovalRequest,
    RequireExplicitApproval,
)
from agent.runtime.logging import logger
from agent.runtime.workspace_context import WorkspaceContext

from .base import BaseSkill
from .process_environment import confined_process_environment
from .process_paths import workspace_argument_error
from .process_safety import (
    ALLOWED_SHELL_COMMANDS,
    hardened_command,
    shell_effect,
    unsafe_command_error,
)
from .process_safety import (
    is_shell_command_allowed as _is_command_allowed,
)
from .process_safety import (
    split_command as _split_command,
)

ALLOWED_COMMANDS = ALLOWED_SHELL_COMMANDS

# ----------------------------------------------------------------------
# Limite de caracteres para a saída da ferramenta
# ----------------------------------------------------------------------
MAX_OUTPUT_CHARS = 4000

class ShellSkill(BaseSkill):
    name = "shell"
    description = (
        "Executa comandos de inspeção e validação permitidos: pytest, ruff, mypy, git (status/log/diff), listagem e echo. "
        "NÃO executa comandos destrutivos como rm, del, format, etc."
    )

    def __init__(
        self,
        base_dir: str | Path = ".",
        timeout: int = 30,
        *,
        workspace: WorkspaceContext | None = None,
        approval_policy: ApprovalPort | None = None,
    ) -> None:
        self.workspace = workspace or WorkspaceContext.create(base_dir)
        self.base_dir = self.workspace.root
        self.timeout = timeout
        self.approval_policy = approval_policy or RequireExplicitApproval()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "command": "string: o comando completo a executar (ex: 'pytest tests/', 'git status')",
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        command = str(args.get("command", "")).strip()
        if not command:
            return {"ok": False, "done": True, "error": "Nenhum comando fornecido."}
        tokens = _split_command(command)
        if tokens is None:
            return {
                "ok": False, "done": True,
                "error": "Comando com sintaxe inválida (aspas não fechadas ou similar)."
            }
        denied = self._preflight(tokens, command)
        if denied is not None:
            return denied
        return self._execute_tokens(tokens)

    def _preflight(
        self,
        tokens: list[str],
        command: str,
    ) -> dict[str, Any] | None:
        if not _is_command_allowed(tokens):
            return {
                "ok": False, "done": True,
                "error": f"Comando não permitido: '{command}'. Apenas validação, listagem e operações Git somente-leitura são permitidas."
            }
        policy_error = unsafe_command_error(tokens)
        if policy_error:
            return {"ok": False, "done": True, "error": policy_error}
        operand_start = 2 if tokens[0].casefold() == "git" else 1
        path_error = workspace_argument_error(
            tokens,
            self.workspace,
            operand_start=operand_start,
        )
        if path_error:
            return {"ok": False, "done": True, "error": path_error}
        effect = shell_effect(tokens)
        return self._confirm_effect(effect, command) if effect else None

    def _execute_tokens(self, tokens: list[str]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                list(hardened_command(tokens)),
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.workspace.root,
                env=confined_process_environment(self.workspace),
                check=False,
            )
            return self._format_result(result)
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

    @staticmethod
    def _format_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        total_chars = len(output)
        if total_chars > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + (
                f"\n... (output truncado, {total_chars} caracteres no total)"
            )
            truncation = f" (truncado, {total_chars} caracteres)"
        else:
            truncation = ""
        ok = result.returncode == 0
        message = (
            "Comando executado com sucesso."
            if ok
            else f"Comando falhou (exit {result.returncode})."
        )
        return {
            "ok": ok,
            "done": True,
            "data": output.strip() or "(sem saída)",
            "error": None if ok else f"Exit code {result.returncode}",
            "message": message + truncation,
        }

    def _confirm_effect(
        self,
        effect: str,
        command: str,
    ) -> dict[str, Any] | None:
        decision = self.approval_policy.request(
            ApprovalRequest(
                action=effect,
                resource=str(self.workspace.root),
                prompt=f"Autorizar o comando com efeito '{effect}' no workspace?",
                metadata={"command": command},
            )
        )
        if decision is ApprovalDecision.APPROVED:
            return None
        if decision is ApprovalDecision.REQUIRED:
            return {
                "ok": False,
                "done": False,
                "status": "blocked",
                "error": "confirmation_required",
                "message": "O comando aguarda aprovação explícita.",
            }
        return {
            "ok": False,
            "done": False,
            "status": "cancelled",
            "error": "approval_rejected",
            "message": "Execução rejeitada pelo usuário.",
        }
