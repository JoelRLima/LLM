import subprocess
from pathlib import Path
from threading import Event
from typing import Any, Dict

from agent.runtime.logging import logger
from agent.runtime.workspace_context import WorkspaceContext

from .base import BaseSkill
from .process_environment import confined_process_environment
from .process_paths import workspace_argument_error
from .process_safety import (
    git_read_only_error,
    hardened_command,
    split_command,
)
from .shell_process import ShellProcessError, run_bounded_process


class GitSkill(BaseSkill):
    name = "git_reader"
    description = "Consulta somente metadados do historico Git local."

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
            "command": "string (somente 'log' permitido)",
            "args": "string (opcional: -N, -n N ou --max-count[=]N)"
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        full_cmd, error = self._validated_command(args)
        if error is not None:
            return error
        assert full_cmd is not None
        return self._run(full_cmd)

    def execute_with_context(
        self,
        args: Dict[str, Any],
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Event | None = None,
    ) -> Any:
        full_cmd, error = self._validated_command(args)
        if error is not None:
            return error
        assert full_cmd is not None
        return self._run(full_cmd, cancellation_token, cancellation_event)

    def _validated_command(
        self,
        args: Dict[str, Any],
    ) -> tuple[list[str] | None, dict[str, Any] | None]:
        cmd = args.get("command")
        if not cmd or cmd != "log":
            return None, {
                "ok": False,
                "done": False,
                "error": (
                    "Apenas o comando 'log' é permitido por segurança."
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

    def _run(
        self,
        full_cmd: list[str],
        cancellation_token: Any | None = None,
        cancellation_event: Event | None = None,
    ) -> dict[str, Any]:
        try:
            environment = confined_process_environment(self.workspace)
            result = run_bounded_process(
                list(hardened_command(full_cmd)),
                workspace=self.workspace.root,
                environment=environment,
                timeout=self.timeout,
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
            )
            return self._format_result(result)
        except FileNotFoundError:
            return {"ok": False, "done": False, "error": "O git não está instalado ou não foi encontrado no PATH."}
        except ShellProcessError as exc:
            return {
                "ok": False,
                "done": False,
                "status": exc.status,
                "error": exc.detail,
                "message": exc.detail,
            }
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
