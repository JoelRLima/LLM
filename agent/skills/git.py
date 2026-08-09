import subprocess
from pathlib import Path
from typing import Any, Dict

from agent.runtime.logging import logger
from agent.runtime.workspace_context import WorkspaceContext

from .base import BaseSkill
from .process_environment import confined_process_environment
from .process_paths import workspace_argument_error
from .process_safety import (
    git_read_only_error,
    hardened_command,
    resolve_trusted_executable,
    split_command,
)


class GitSkill(BaseSkill):
    name = "git_reader"
    description = "Executa comandos básicos do git (status, log, diff) para inspecionar o repositório."

    def __init__(
        self,
        base_dir: str | Path = ".",
        *,
        workspace: WorkspaceContext | None = None,
        timeout: int = 20,
    ) -> None:
        self.workspace = workspace or WorkspaceContext.create(base_dir)
        self.base_dir = self.workspace.root
        self.timeout = timeout

    def get_schema(self) -> Dict[str, Any]:
        return {
            "command": "string (apenas 'status', 'log', 'diff' permitidos)",
            "args": "string (argumentos extras opcionais, ex: '--oneline -n 5' para o log)"
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        full_cmd, error = self._validated_command(args)
        if error is not None:
            return error
        assert full_cmd is not None
        return self._run(full_cmd)

    def _validated_command(
        self,
        args: Dict[str, Any],
    ) -> tuple[list[str] | None, dict[str, Any] | None]:
        cmd = args.get("command")
        if not cmd or cmd not in ["status", "log", "diff"]:
            return None, {
                "ok": False,
                "done": False,
                "error": (
                    "Apenas comandos 'status', 'log', e 'diff' "
                    "são permitidos por segurança."
                ),
            }
        extra_args = str(args.get("args", "")).strip()
        full_cmd = ["git", cmd]
        if extra_args:
            split_args = split_command(extra_args)
            if split_args is None:
                return None, {
                    "ok": False,
                    "done": False,
                    "error": "Argumentos Git com sintaxe inválida.",
                }
            full_cmd.extend(split_args)
        policy_error = git_read_only_error(full_cmd)
        if policy_error:
            return None, {"ok": False, "done": False, "error": policy_error}
        path_error = workspace_argument_error(
            full_cmd,
            self.workspace,
            operand_start=2,
        )
        if path_error:
            return None, {"ok": False, "done": False, "error": path_error}
        return full_cmd, None

    def _run(self, full_cmd: list[str]) -> dict[str, Any]:
        try:
            environment = confined_process_environment(self.workspace)
            executable = resolve_trusted_executable(
                "git", environment, self.workspace.root
            )
            if executable is None:
                return {
                    "ok": False,
                    "done": False,
                    "error": "O git confiavel nao foi encontrado fora do workspace.",
                }
            command = list(hardened_command(full_cmd))
            command[0] = executable
            result = subprocess.run(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                cwd=self.workspace.root,
                env=environment,
            )
            return self._format_result(result)
        except FileNotFoundError:
            return {"ok": False, "done": False, "error": "O git não está instalado ou não foi encontrado no PATH."}
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "done": False,
                "error": f"Timeout após {self.timeout}s.",
            }
        except Exception as e:
            logger.error(f"GitSkill error: {e}", exc_info=True)
            return {"ok": False, "done": False, "error": str(e)}

    @staticmethod
    def _format_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        if result.returncode != 0:
            return {
                "ok": False,
                "done": False,
                "error": result.stderr or "Git command failed.",
            }
        output = result.stdout if result.stdout.strip() else "(sem saída/vazio)"
        return {"ok": True, "done": True, "data": output}
