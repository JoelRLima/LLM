"""Application and compatibility paths.

``AppPaths`` is resolved once at the application boundary.  It separates
configuration, durable data, operational state, cache and logs from both the
installed package and the workspace.  The string constants at the end of this
module are temporary compatibility facades for legacy consumers; new code must
receive an ``AppPaths`` or ``WorkspacePaths`` instance explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

APP_DIRECTORY_NAME = "local-llm-agent"


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _environment_path(
    environment: Mapping[str, str],
    key: str,
    fallback: Path,
) -> Path:
    value = environment.get(key)
    return _absolute(value) if value else _absolute(fallback)


@dataclass(frozen=True)
class WorkspacePaths:
    """All durable and disposable paths owned by one workspace."""

    workspace_id: str
    data_dir: Path
    state_dir: Path
    cache_dir: Path

    @property
    def memory_file(self) -> Path:
        return self.data_dir / "agent_memory.json"

    @property
    def memory_db_file(self) -> Path:
        return self.data_dir / "agent_memory.db"

    @property
    def memory_backup_dir(self) -> Path:
        return self.data_dir / "memory_backups"

    @property
    def task_definitions_dir(self) -> Path:
        return self.data_dir / 'task_definitions'

    @property
    def checkpoint_file(self) -> Path:
        return self.state_dir / "agent_checkpoint.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "application.lock"

    @property
    def metrics_file(self) -> Path:
        return self.state_dir / "agent_metrics.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / "artifacts"

    @property
    def restore_points_dir(self) -> Path:
        return self.state_dir / "restore_points"

    @property
    def chat_history_file(self) -> Path:
        return self.data_dir / "chat_history.json"

    @property
    def task_tracker_json(self) -> Path:
        return self.state_dir / "task_tracker.json"

    @property
    def task_tracker_markdown(self) -> Path:
        return self.state_dir / "task_tracker.md"

    @property
    def scratch_dir(self) -> Path:
        return self.cache_dir / "scratch"

    @property
    def benchmark_results_file(self) -> Path:
        return self.state_dir / "benchmark_results.json"

    @property
    def extensions_file(self) -> Path:
        """Durable enabled-extension registry for this workspace."""
        return self.data_dir / "extensions.json"

    @property
    def workspace_extensions_file(self) -> Path:
        """Versioned extension intent and grants for this workspace."""
        return self.extensions_file

    @property
    def workspace_extensions_lock_file(self) -> Path:
        """Cross-process lock adjacent to the workspace extension document."""
        return self.workspace_extensions_file.with_name(
            f"{self.workspace_extensions_file.name}.lock"
        )

    def ensure_directories(self) -> None:
        """Create writable workspace storage after bootstrap validation."""

        for directory in (
            self.data_dir,
            self.state_dir,
            self.cache_dir,
            self.memory_backup_dir,
            self.task_definitions_dir,
            self.reports_dir,
            self.artifacts_dir,
            self.restore_points_dir,
            self.scratch_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class AppPaths:
    """Resolved application locations, with no filesystem side effects."""

    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    log_dir: Path

    @classmethod
    def discover(
        cls,
        app_home: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "AppPaths":
        environment = os.environ if env is None else env
        explicit_home = app_home or environment.get("LLM_AGENT_HOME")
        if explicit_home:
            home = _absolute(explicit_home)
            return cls(
                config_dir=home / "config",
                data_dir=home / "data",
                state_dir=home / "state",
                cache_dir=home / "cache",
                log_dir=home / "logs",
            )

        legacy_runtime = environment.get("AGENT_RUNTIME_DIR")
        if legacy_runtime:
            runtime = _absolute(legacy_runtime)
            return cls(
                config_dir=runtime / "config",
                data_dir=runtime / "data",
                state_dir=runtime,
                cache_dir=runtime / "cache",
                log_dir=runtime / "logs",
            )

        user_home = Path.home()
        if os.name == "nt":
            roaming = _environment_path(
                environment,
                "APPDATA",
                user_home / "AppData" / "Roaming",
            )
            local = _environment_path(
                environment,
                "LOCALAPPDATA",
                user_home / "AppData" / "Local",
            )
            return cls(
                config_dir=roaming / APP_DIRECTORY_NAME,
                data_dir=local / APP_DIRECTORY_NAME / "data",
                state_dir=local / APP_DIRECTORY_NAME / "state",
                cache_dir=local / APP_DIRECTORY_NAME / "cache",
                log_dir=local / APP_DIRECTORY_NAME / "logs",
            )

        config_home = _environment_path(environment, "XDG_CONFIG_HOME", user_home / ".config")
        data_home = _environment_path(
            environment,
            "XDG_DATA_HOME",
            user_home / ".local" / "share",
        )
        state_home = _environment_path(
            environment,
            "XDG_STATE_HOME",
            user_home / ".local" / "state",
        )
        cache_home = _environment_path(environment, "XDG_CACHE_HOME", user_home / ".cache")
        return cls(
            config_dir=config_home / APP_DIRECTORY_NAME,
            data_dir=data_home / APP_DIRECTORY_NAME,
            state_dir=state_home / APP_DIRECTORY_NAME,
            cache_dir=cache_home / APP_DIRECTORY_NAME,
            log_dir=state_home / APP_DIRECTORY_NAME / "logs",
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "agent.log"

    @property
    def health_report_file(self) -> Path:
        return self.state_dir / "health_report.json"

    @property
    def last_workspace_file(self) -> Path:
        """Last successfully opened workspace for the interactive launcher."""
        return self.state_dir / "last_workspace.json"

    @property
    def extensions_dir(self) -> Path:
        return self.data_dir / "extensions"

    @property
    def extensions_registry_file(self) -> Path:
        """Legacy CLI registry; modern bootstrap uses catalog/workspace state."""
        return self.extensions_dir / "registry.json"

    @property
    def extensions_catalog_file(self) -> Path:
        """Versioned global catalog consumed by extension-aware bootstrap."""
        return self.extensions_dir / "catalog.json"

    @property
    def extensions_catalog_lock_file(self) -> Path:
        """Cross-process writer lock adjacent to the versioned catalog."""
        return self.extensions_dir / "catalog.json.lock"

    def for_workspace(self, workspace_id: str) -> WorkspacePaths:
        if not workspace_id or any(char in workspace_id for char in ("/", "\\", "..")):
            raise ValueError("workspace_id inválido.")
        return WorkspacePaths(
            workspace_id=workspace_id,
            data_dir=(self.data_dir / "workspaces" / workspace_id).resolve(),
            state_dir=(self.state_dir / "workspaces" / workspace_id).resolve(),
            cache_dir=(self.cache_dir / "workspaces" / workspace_id).resolve(),
        )

    def ensure_base_directories(self) -> None:
        """Create application-owned directories only after explicit startup."""

        for directory in (
            self.config_dir,
            self.data_dir,
            self.state_dir,
            self.cache_dir,
            self.log_dir,
            self.extensions_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# Legacy process-global facades.  They deliberately retain the old relative
# default so importing compatibility code does not touch a real user profile.
RUNTIME_DIR = os.environ.get("AGENT_RUNTIME_DIR", "runtime")
LOG_FILE = os.path.join(RUNTIME_DIR, "agent.log")
CHECKPOINT_FILE = os.path.join(RUNTIME_DIR, "agent_checkpoint.json")
METRICS_FILE = os.path.join(RUNTIME_DIR, "agent_metrics.jsonl")
MEMORY_FILE = os.path.join(RUNTIME_DIR, "agent_memory.json")
MEMORY_DB_FILE = os.path.join(RUNTIME_DIR, "agent_memory.db")
MEMORY_BACKUP_DIR = os.path.join(RUNTIME_DIR, "memory_backups")
RESTORE_POINTS_DIR = os.path.join(RUNTIME_DIR, "restore_points")
CHAT_HISTORY_FILE = os.path.join(RUNTIME_DIR, "chat_history.json")
REPORTS_DIR = os.path.join(RUNTIME_DIR, "reports")
TASK_TRACKER_JSON = os.path.join(RUNTIME_DIR, "task_tracker.json")
TASK_TRACKER_MD = os.path.join(RUNTIME_DIR, "task_tracker.md")
BENCHMARK_RESULTS_FILE = os.path.join(RUNTIME_DIR, "benchmark_results.json")
HEALTH_REPORT_FILE = os.path.join(RUNTIME_DIR, "health_report.json")


def ensure_runtime_dir() -> None:
    """Create the legacy runtime directory for compatibility consumers."""

    Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
