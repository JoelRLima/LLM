"""Execute validated Python code in an ephemeral, resource-limited process."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from agent.runtime.logging import logger

from .base import BaseSkill
from .python_sandbox_policy import validate_code
from .python_sandbox_runtime import (
    SandboxLimits,
    build_wrapper,
    inspect_sandbox,
    validate_sandbox_state,
)

MAX_OUTPUT_CHARS = 4000
WINDOWS_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


class PythonExecutorSkill(BaseSkill):
    name = "python_executor"
    description = (
        "Executa código Python seguro em um subprocesso isolado, com timeout, "
        "imports restritos e builtins restritos em runtime."
    )

    MAX_FILES_CREATED = 20
    MAX_DIRS_CREATED = 10
    MAX_TREE_DEPTH = 5
    MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
    MAX_TOTAL_SIZE_BYTES = 5 * 1024 * 1024
    MAX_STDOUT_HARD_LIMIT = 2_000_000
    MAX_STDERR_HARD_LIMIT = 2_000_000

    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout = timeout_seconds

    def get_schema(self) -> dict[str, Any]:
        return {
            "code": {
                "type": "string",
                "description": "Código Python a ser executado. Use print() para exibir resultados.",
            }
        }

    @property
    def _limits(self) -> SandboxLimits:
        return SandboxLimits(
            max_files=self.MAX_FILES_CREATED,
            max_directories=self.MAX_DIRS_CREATED,
            max_depth=self.MAX_TREE_DEPTH,
            max_file_size=self.MAX_FILE_SIZE_BYTES,
            max_total_size=self.MAX_TOTAL_SIZE_BYTES,
        )

    def _validate_code(self, code: str) -> str | None:
        return validate_code(code)

    def _build_wrapper(self, code: str) -> str:
        return build_wrapper(code)

    def _inspect_sandbox(self, temp_dir: str) -> dict[str, Any]:
        return inspect_sandbox(temp_dir)

    def _validate_sandbox_state(self, temp_dir: str) -> str | None:
        return validate_sandbox_state(temp_dir, self._limits)

    @staticmethod
    def _drop_privileges() -> None:
        try:
            import pwd

            lookup_user = getattr(pwd, "getpwnam", None)
            if not callable(lookup_user):
                return
            nobody = lookup_user("nobody")
            set_group: Callable[[int], None] | None = getattr(os, "setgid", None)
            set_user: Callable[[int], None] | None = getattr(os, "setuid", None)
            if set_group is not None and set_user is not None:
                set_group(nobody.pw_gid)
                set_user(nobody.pw_uid)
        except Exception as exc:
            logger.debug(f"[python_executor] Falha ao reduzir privilégios: {exc}")

    def _run_process(self, temp_dir: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "script.py"]
        common: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": self.timeout,
            "cwd": temp_dir,
        }
        if os.name == "nt":
            return subprocess.run(command, creationflags=WINDOWS_NO_WINDOW, **common)
        get_effective_user = getattr(os, "geteuid", None)
        if os.name == "posix" and callable(get_effective_user) and get_effective_user() == 0:
            return subprocess.run(command, preexec_fn=self._drop_privileges, **common)
        return subprocess.run(command, **common)

    @staticmethod
    def _error(error: str, message: str) -> dict[str, Any]:
        return {"ok": False, "done": True, "error": error, "message": message}

    def _write_script(self, temp_dir: str, wrapped_code: str) -> str | None:
        script_path = os.path.join(temp_dir, "script.py")
        try:
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(wrapped_code)
        except Exception as exc:
            return str(exc)
        return None

    def _validate_output(self, stdout: str, stderr: str) -> dict[str, Any] | None:
        if len(stdout) <= self.MAX_STDOUT_HARD_LIMIT and len(stderr) <= self.MAX_STDERR_HARD_LIMIT:
            return None
        return self._error(
            "Limite de tamanho de stdout/stderr excedido.",
            "A execução foi rejeitada por exceder os limites de saída permitidos pela sandbox.",
        )

    @staticmethod
    def _format_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        output = result.stdout or ""
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        total_chars = len(output)
        truncated = total_chars > MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS] + f"\n... (output truncado, {total_chars} caracteres no total)"
        suffix = f" (truncado, {total_chars} caracteres)" if truncated else ""
        if result.returncode == 0:
            return {
                "ok": True,
                "done": True,
                "data": output.strip() or "(sem saída)",
                "error": None,
                "message": "Código executado com sucesso." + suffix,
            }
        return {
            "ok": False,
            "done": True,
            "error": f"Código terminou com erro (exit {result.returncode})",
            "message": (output.strip() or "(sem saída)") + suffix,
        }

    def _execute_in_sandbox(self, temp_dir: str, wrapped_code: str) -> dict[str, Any]:
        write_error = self._write_script(temp_dir, wrapped_code)
        if write_error:
            return self._error(write_error, "Erro ao criar arquivo da sandbox.")
        try:
            result = self._run_process(temp_dir)
        except subprocess.TimeoutExpired:
            return self._error(f"Timeout após {self.timeout}s", "O código excedeu o tempo limite.")
        except Exception as exc:
            return self._error(str(exc), "Erro ao executar o subprocesso.")
        output_error = self._validate_output(result.stdout or "", result.stderr or "")
        if output_error:
            return output_error
        state_error = self._validate_sandbox_state(temp_dir)
        if state_error:
            return self._error(
                state_error,
                f"Execução rejeitada pela validação pós-execução da sandbox: {state_error}",
            )
        return self._format_result(result)

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        code = str(args.get("code", ""))
        if not code.strip():
            return self._error("código vazio", "Nenhum código fornecido.")
        validation_error = self._validate_code(code)
        if validation_error:
            return self._error(validation_error, f"Erro de segurança na validação: {validation_error}")
        try:
            with tempfile.TemporaryDirectory(prefix="agent_sandbox_", ignore_cleanup_errors=True) as temp_dir:
                return self._execute_in_sandbox(temp_dir, self._build_wrapper(code))
        finally:
            logger.debug("[python_executor] Sandbox destruída e limpeza concluída")
