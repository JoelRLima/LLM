"""Workspace confinement, diff display, and task restore ownership."""

from __future__ import annotations

import datetime
import difflib
import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.code.discovery import ProjectDiscovery
from agent.code.validation import ProjectValidator, ValidationStatus
from agent.planning.plan_model import Plan, ToolPlanStep
from agent.runtime import paths
from agent.runtime.config import DEFAULT_VALIDATION
from agent.runtime.logging import logger
from agent.workspace_rollback import remove_created_files, restore_backups, rollback_transactions

RESTORE_POINTS_DIR = paths.RESTORE_POINTS_DIR
# Compatibility projection: the authored defaults live in the packaged
# configuration resource consumed by ``agent.runtime.config``.
DEFAULT_VALIDATION_CONFIG = DEFAULT_VALIDATION


class ValidationFailedError(Exception):
    """Indica que uma validação configurada falhou após uma modificação."""


class WorkspaceManager:
    def __init__(
        self,
        verbose: bool = False,
        workspace_root: str | Path = ".",
        restore_points_dir: str | Path | None = RESTORE_POINTS_DIR,
        validation_config: Mapping[str, Any] | None = None,
        validation_service: ProjectValidator | None = None,
    ) -> None:
        self.verbose = verbose
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        effective_restore_dir = (
            RESTORE_POINTS_DIR if restore_points_dir is None else restore_points_dir
        )
        self.restore_points_dir = Path(effective_restore_dir).expanduser().resolve()
        self.validation_config = self._normalize_validation_config(validation_config)
        self.validation_service = validation_service or ProjectValidator(
            self.workspace_root,
            validation_config=self.validation_config,
        )
        self.restore_points: List[Dict[str, str]] = []
        self.created_files: List[str] = []
        self._task_transactions: list[Any] = []

    def register_transaction(self, transaction: Any) -> None:
        """Record one committed code transaction for task-level rollback."""

        if transaction not in self._task_transactions:
            self._task_transactions.append(transaction)

    def discard_transactions(self) -> None:
        self._task_transactions.clear()

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
        candidate = (
            raw.resolve()
            if raw.is_absolute()
            else (self.workspace_root / raw).resolve()
        )
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Caminho fora do workspace: {file_path}") from exc
        return candidate

    def create_restore_point(
        self, plan: Plan | list[Mapping[str, Any]]
    ) -> None:
        if not plan:
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        restore_dir = self.restore_points_dir / timestamp

        for step in plan:
            if isinstance(step, ToolPlanStep):
                tool, args = step.tool, step.args
            elif isinstance(step, Mapping):
                # Explicit legacy/checkpoint caller boundary.
                tool = step.get("tool", "")
                args = step.get("args", {})
            else:
                continue
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

    def rollback(self) -> bool:
        if not self.restore_points and not self.created_files and not self._task_transactions:
            return True
        success = rollback_transactions(self._task_transactions, logger)
        if self.verbose:
            print("⏪ [ROLLBACK] Restaurando arquivos ao estado original...")
        success = restore_backups(self.restore_points, self.resolve_path, logger) and success
        success = remove_created_files(self.created_files, self.resolve_path, logger) and success

        self.restore_points.clear()
        self.created_files.clear()
        self._task_transactions.clear()
        return success

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

    def lint_check(self, file_path: str) -> Optional[str]:
        """Validate one Python file through the canonical project service."""

        target = self.resolve_path(file_path)
        if target.suffix != ".py":
            return None

        profile = ProjectDiscovery(self.workspace_root).discover()
        if self.validation_config.get("pytest") is True:
            self.resolve_path(str(self.validation_config["pytest_dir"]))
            profile = replace(
                profile,
                test_roots=(str(self.validation_config["pytest_dir"]),),
            )
        relative = target.relative_to(self.workspace_root).as_posix()
        report = self.validation_service.validate(
            profile,
            [relative],
            include_tests=False,
        )
        if report.status in {ValidationStatus.PASSED, ValidationStatus.UNAVAILABLE}:
            if report.status is ValidationStatus.UNAVAILABLE:
                logger.warning(
                    "Validação opcional indisponível para '%s'; ignorada.",
                    file_path,
                )
            return ""
        message = "\n".join(
            diagnostic.message
            for diagnostic in report.diagnostics
            if diagnostic.message
        ) or report.status.value
        if self.validation_config.get("fail_triggers_replan", False):
            raise ValidationFailedError(message)
        return message
