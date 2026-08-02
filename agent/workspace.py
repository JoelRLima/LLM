from __future__ import annotations

import ast
import datetime
import difflib
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.runtime import paths
from agent.runtime.logging import logger

RESTORE_POINTS_DIR = paths.RESTORE_POINTS_DIR
DEFAULT_VALIDATION_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "ruff": False,
    "mypy": False,
    "pytest": False,
    "pytest_dir": "tests/",
    "fail_triggers_replan": False,
}


class ValidationFailedError(Exception):
    """Indica que uma validação configurada falhou após uma modificação."""


class WorkspaceManager:
    def __init__(
        self,
        verbose: bool = False,
        workspace_root: str | Path = ".",
        restore_points_dir: str | Path | None = RESTORE_POINTS_DIR,
        validation_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.verbose = verbose
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        effective_restore_dir = (
            RESTORE_POINTS_DIR if restore_points_dir is None else restore_points_dir
        )
        self.restore_points_dir = Path(effective_restore_dir).expanduser().resolve()
        self.validation_config = self._normalize_validation_config(validation_config)
        self.restore_points: List[Dict[str, str]] = []
        self.created_files: List[str] = []

    @staticmethod
    def _normalize_validation_config(
        validation_config: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        normalized = dict(DEFAULT_VALIDATION_CONFIG)
        if validation_config is None:
            return normalized
        for key, fallback in DEFAULT_VALIDATION_CONFIG.items():
            value = validation_config.get(key, fallback)
            if isinstance(fallback, bool):
                normalized[key] = value if isinstance(value, bool) else fallback
            elif isinstance(value, str):
                normalized[key] = value
        return normalized

    def resolve_path(self, file_path: str | Path) -> Path:
        raw = Path(file_path).expanduser()
        candidate = raw.resolve() if raw.is_absolute() else (self.workspace_root / raw).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Caminho fora do workspace: {file_path}") from exc
        return candidate

    def create_restore_point(self, plan: list) -> None:
        if not plan:
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        restore_dir = self.restore_points_dir / timestamp

        for step in plan:
            tool = step.get("tool", "") if isinstance(step, dict) else ""
            args = step.get("args", {}) if isinstance(step, dict) else {}
            if tool not in {"file_writer", "shell", "python_executor"}:
                continue
            raw_path = args.get("file_path") or args.get("target") or ""
            if not raw_path:
                continue
            target = self.resolve_path(str(raw_path))
            if target.exists():
                self._backup_file(target, restore_dir)
            else:
                target_text = str(target)
                if target_text not in self.created_files:
                    self.created_files.append(target_text)
                    if self.verbose:
                        print(f"[DEBUG] '{target}' marcado como novo.")

    def _backup_file(self, target: Path, restore_dir: Path) -> None:
        relative = target.relative_to(self.workspace_root)
        backup = restore_dir / relative
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            self.restore_points.append({"original": str(target), "backup": str(backup)})
            if self.verbose:
                print(f"[DEBUG] Checkpoint salvo para '{target}'")
        except OSError as exc:
            logger.warning("Falha ao criar checkpoint para '%s': %s", target, exc)

    def rollback(self) -> None:
        if not self.restore_points and not self.created_files:
            return
        if self.verbose:
            print("⏪ [ROLLBACK] Restaurando arquivos ao estado original...")

        for entry in reversed(self.restore_points):
            original = self.resolve_path(entry["original"])
            backup = Path(entry["backup"])
            try:
                shutil.copy2(backup, original)
                backup.unlink(missing_ok=True)
            except OSError as exc:
                logger.error("Falha ao restaurar '%s': %s", original, exc)

        for file_path in reversed(self.created_files):
            target = self.resolve_path(file_path)
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                logger.error("Falha ao remover arquivo criado '%s': %s", target, exc)

        self.restore_points.clear()
        self.created_files.clear()

    def show_diff(self, file_path: str, new_content: str) -> None:
        target = self.resolve_path(file_path)
        try:
            original = target.read_text(encoding="utf-8")
        except OSError:
            original = ""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=f"{file_path} (proposto)",
        )
        diff_text = "".join(diff)
        if diff_text.strip():
            print(f"\n📝 [DIFF] Mudanças propostas para '{file_path}':")
            print(diff_text)
        else:
            print(f"📝 [DIFF] Nenhuma mudança em '{file_path}'.")

    def _run_ruff(self, file_path: Path) -> Optional[str]:
        return self._run_validation_command(
            ["ruff", "check", str(file_path)],
            "Ruff",
            "Ferramenta 'ruff' não está instalada; verificação ignorada.",
        )

    def _run_mypy(self, file_path: Path) -> Optional[str]:
        return self._run_validation_command(
            ["mypy", "--ignore-missing-imports", str(file_path)],
            "Mypy",
            "Ferramenta 'mypy' não está instalada; verificação ignorada.",
        )

    def _run_pytest(self, pytest_dir: str) -> Optional[str]:
        target = self.resolve_path(pytest_dir)
        return self._run_validation_command(
            [sys.executable, "-m", "pytest", str(target)],
            "Pytest",
            "Ferramenta 'pytest' não está instalada; verificação ignorada.",
        )

    def _run_validation_command(
        self,
        command: list[str],
        label: str,
        unavailable_message: str,
    ) -> Optional[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self.workspace_root,
            )
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                return f"{label}: {output}" if output else f"{label}: verificação falhou."
        except FileNotFoundError:
            logger.warning(unavailable_message)
        except subprocess.TimeoutExpired:
            logger.warning("Verificação '%s' excedeu o tempo limite (10s); ignorada.", label)
        except OSError as exc:
            logger.warning("Falha inesperada ao executar '%s': %s", label, exc)
        return None

    def lint_check(self, file_path: str) -> Optional[str]:
        target = self.resolve_path(file_path)
        if target.suffix != ".py":
            return None

        errors: List[str] = []
        try:
            ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"Sintaxe: {exc}")

        if self.validation_config.get("enabled", True):
            checks = (
                ("ruff", lambda: self._run_ruff(target)),
                ("mypy", lambda: self._run_mypy(target)),
                (
                    "pytest",
                    lambda: self._run_pytest(str(self.validation_config["pytest_dir"])),
                ),
            )
            for name, check in checks:
                if self.validation_config.get(name, False) and (error := check()):
                    errors.append(error)

        if not errors:
            return ""
        message = "\n".join(errors)
        if self.validation_config.get("fail_triggers_replan", False):
            raise ValidationFailedError(message)
        return message
