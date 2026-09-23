"""Application and compatibility paths.
``AppPaths`` is resolved once at the application boundary.  It separates
configuration, durable data, operational state, cache and logs from both the
installed package and the workspace.  The string constants at the end of this
module are temporary compatibility facades for legacy consumers; new code must
receive an ``AppPaths`` or ``WorkspacePaths`` instance explicitly.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.workspace_trace_paths import WorkspaceTracePaths

APP_DIRECTORY_NAME = 'local-llm-agent'
class AppHomeOrigin(str, Enum):
    ARGUMENT = 'argument'
    LLM_AGENT_HOME = 'llm_agent_home'
    AGENT_RUNTIME_DIR = 'agent_runtime_dir'
    WINDOWS_DEFAULT = 'windows_default'
    XDG_DEFAULT = 'xdg_default'
    INJECTED = 'injected'
_WORKSPACE_ID_PATTERN = re.compile('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
def _environment_path(environment: Mapping[str, str], key: str, fallback: Path) -> Path:
    value = environment.get(key)
    return _absolute(value) if value else _absolute(fallback)
def _ensure_safe_directory(path: Path) -> None:
    """Reject link-like ancestors before creating an owned directory."""
    lexical = Path(path)
    current = lexical
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    for candidate in reversed(chain):
        try:
            inspection = inspect_final_path(candidate)
        except OSError as exc:
            raise OSError(f'unsafe application directory: {candidate}') from exc
        if not inspection.exists:
            continue
        metadata = inspection.metadata
        if inspection.is_link_like or metadata is None:
            raise OSError(f'unsafe application directory: {candidate}')
        if not stat.S_ISDIR(metadata.st_mode):
            raise NotADirectoryError(candidate)
@dataclass(frozen=True)
class WorkspacePaths(WorkspaceTracePaths):
    """All durable and disposable paths owned by one workspace."""
    workspace_id: str
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    @property
    def memory_file(self) -> Path:
        return self.data_dir / 'agent_memory.json'
    @property
    def memory_db_file(self) -> Path:
        return self.data_dir / 'agent_memory.db'
    @property
    def memory_backup_dir(self) -> Path:
        return self.data_dir / 'memory_backups'
    @property
    def task_definitions_dir(self) -> Path:
        return self.data_dir / 'task_definitions'
    @property
    def checkpoint_file(self) -> Path:
        return self.state_dir / 'agent_checkpoint.json'
    @property
    def lock_file(self) -> Path:
        return self.state_dir / 'application.lock'
    @property
    def metrics_file(self) -> Path:
        return self.state_dir / 'agent_metrics.jsonl'
    @property
    def reports_dir(self) -> Path:
        return self.state_dir / 'reports'
    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / 'artifacts'
    @property
    def output_artifacts_dir(self) -> Path:
        return self.artifacts_dir / 'outputs'
    @property
    def restore_points_dir(self) -> Path:
        return self.state_dir / 'restore_points'
    @property
    def chat_history_file(self) -> Path:
        return self.data_dir / 'chat_history.json'
    @property
    def task_tracker_json(self) -> Path:
        return self.state_dir / 'task_tracker.json'
    @property
    def task_tracker_markdown(self) -> Path:
        return self.state_dir / 'task_tracker.md'
    @property
    def scratch_dir(self) -> Path:
        return self.cache_dir / 'scratch'
    @property
    def benchmark_results_file(self) -> Path:
        return self.state_dir / 'benchmark_results.json'
    @property
    def extensions_file(self) -> Path:
        return self.data_dir / 'extensions.json'
    @property
    def workspace_extensions_file(self) -> Path:
        return self.extensions_file
    @property
    def workspace_extensions_lock_file(self) -> Path:
        return self.workspace_extensions_file.with_name(f'{self.workspace_extensions_file.name}.lock')
    @property
    def feedback_file(self) -> Path:
        return self.data_dir / 'human_feedback.json'
    @property
    def feedback_lock_file(self) -> Path:
        return self.data_dir / 'human_feedback.json.lock'
    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.state_dir, self.cache_dir, self.memory_backup_dir, self.task_definitions_dir, self.reports_dir, self.artifacts_dir, self.restore_points_dir, self.scratch_dir, *self.trace_directories):
            _ensure_safe_directory(directory)
            directory.mkdir(parents=True, exist_ok=True)
@dataclass(frozen=True)
class AppPaths:
    home_dir: Path
    config_dir: Path
    global_dir: Path
    workspaces_dir: Path
    cache_dir: Path
    log_dir: Path
    home_origin: AppHomeOrigin = AppHomeOrigin.INJECTED
    @classmethod
    def discover(cls, app_home: str | Path | None=None, env: Mapping[str, str] | None=None) -> 'AppPaths':
        environment = os.environ if env is None else env
        if app_home is not None and str(app_home).strip():
            home = _absolute(app_home)
            origin = AppHomeOrigin.ARGUMENT
        elif environment.get('LLM_AGENT_HOME'):
            home = _absolute(environment['LLM_AGENT_HOME'])
            origin = AppHomeOrigin.LLM_AGENT_HOME
        elif environment.get('AGENT_RUNTIME_DIR'):
            home = _absolute(environment['AGENT_RUNTIME_DIR'])
            origin = AppHomeOrigin.AGENT_RUNTIME_DIR
        elif os.name == 'nt':
            local = _environment_path(environment, 'LOCALAPPDATA', Path.home() / 'AppData' / 'Local')
            home = local / APP_DIRECTORY_NAME / 'home'
            origin = AppHomeOrigin.WINDOWS_DEFAULT
        else:
            data_home = _environment_path(environment, 'XDG_DATA_HOME', Path.home() / '.local' / 'share')
            home = data_home / APP_DIRECTORY_NAME / 'home'
            origin = AppHomeOrigin.XDG_DEFAULT
        home = _absolute(home)
        return cls(home_dir=home, config_dir=home / 'config', global_dir=home / 'global', workspaces_dir=home / 'workspaces', cache_dir=home / 'cache', log_dir=home / 'logs', home_origin=origin)
    @property
    def config_file(self) -> Path:
        return self.config_dir / 'config.json'
    @property
    def storage_layout_file(self) -> Path:
        return self.global_dir / 'storage_layout.json'
    @property
    def migrations_dir(self) -> Path:
        return self.global_dir / 'migrations'
    @property
    def w18_to_w19_migration_receipt_file(self) -> Path:
        return self.migrations_dir / 'w18_to_w19_v1.json'
    @property
    def log_file(self) -> Path:
        return self.log_dir / 'agent.log'
    @property
    def health_report_file(self) -> Path:
        return self.global_dir / 'health_report.json'
    @property
    def last_workspace_file(self) -> Path:
        return self.global_dir / 'last_workspace.json'
    @property
    def recent_workspaces_file(self) -> Path:
        return self.global_dir / 'recent_workspaces.json'
    @property
    def extensions_dir(self) -> Path:
        return self.global_dir / 'extensions'
    @property
    def extensions_registry_file(self) -> Path:
        return self.extensions_dir / 'registry.json'
    @property
    def extensions_catalog_file(self) -> Path:
        return self.extensions_dir / 'catalog.json'
    @property
    def extensions_catalog_lock_file(self) -> Path:
        return self.extensions_dir / 'catalog.json.lock'
    @property
    def engineering_dir(self) -> Path:
        return self.global_dir / 'engineering'
    @property
    def discovery_dir(self) -> Path:
        return self.global_dir / 'discovery'
    @property
    def discovery_frecency_file(self) -> Path:
        return self.discovery_dir / 'frecency.json'
    @property
    def discovery_frecency_lock_file(self) -> Path:
        return self.discovery_dir / 'frecency.json.lock'
    @property
    def engineering_runs_dir(self) -> Path:
        return self.engineering_dir / 'runs'
    @property
    def engineering_locks_dir(self) -> Path:
        return self.engineering_dir / 'locks'
    @property
    def engineering_transitions_dir(self) -> Path:
        return self.engineering_dir / 'transitions'
    @property
    def engineering_store_lock_file(self) -> Path:
        return self.engineering_locks_dir / 'store.lock'
    def engineering_run_file(self, run_id: str) -> Path:
        if not re.fullmatch('engr-[0-9a-f]{32}', run_id):
            raise ValueError('invalid Engineering run id')
        return self.engineering_runs_dir / f'{run_id}.json'
    def engineering_run_lock_file(self, run_id: str) -> Path:
        if not re.fullmatch('engr-[0-9a-f]{32}', run_id):
            raise ValueError('invalid Engineering run id')
        return self.engineering_locks_dir / f'run-{run_id}.lock'
    def engineering_transition_file(self, run_id: str) -> Path:
        if not re.fullmatch('engr-[0-9a-f]{32}', run_id):
            raise ValueError('invalid Engineering run id')
        return self.engineering_transitions_dir / f'{run_id}.pending.json'
    def engineering_resource_lock_file(self, resource_identity: str) -> Path:
        if not isinstance(resource_identity, str) or not resource_identity or len(resource_identity.encode('utf-8')) > 512:
            raise ValueError('invalid Engineering resource identity')
        digest = hashlib.sha256(resource_identity.encode('utf-8')).hexdigest()
        return self.engineering_locks_dir / f'resource-{digest}.lock'
    def validate_engineering_directories(self) -> None:
        for directory in (self.engineering_dir, self.engineering_runs_dir, self.engineering_locks_dir, self.engineering_transitions_dir):
            _ensure_safe_directory(directory)
            if not directory.is_dir():
                raise NotADirectoryError(directory)
    def for_workspace(self, workspace_id: str) -> WorkspacePaths:
        if not isinstance(workspace_id, str) or not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
            raise ValueError('invalid workspace_id')
        return WorkspacePaths(workspace_id=workspace_id, data_dir=(self.workspaces_dir / workspace_id / 'data').resolve(), state_dir=(self.workspaces_dir / workspace_id / 'state').resolve(), cache_dir=(self.workspaces_dir / workspace_id / 'cache').resolve())
    def ensure_base_directories(self) -> None:
        for directory in (self.config_dir, self.global_dir, self.workspaces_dir, self.migrations_dir, self.cache_dir, self.log_dir, self.extensions_dir, self.engineering_dir, self.engineering_runs_dir, self.engineering_locks_dir, self.engineering_transitions_dir, self.discovery_dir):
            _ensure_safe_directory(directory)
            directory.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR = os.environ.get('AGENT_RUNTIME_DIR', 'runtime')
LOG_FILE = os.path.join(RUNTIME_DIR, 'agent.log')
CHECKPOINT_FILE = os.path.join(RUNTIME_DIR, 'agent_checkpoint.json')
METRICS_FILE = os.path.join(RUNTIME_DIR, 'agent_metrics.jsonl')
MEMORY_FILE = os.path.join(RUNTIME_DIR, 'agent_memory.json')
MEMORY_DB_FILE = os.path.join(RUNTIME_DIR, 'agent_memory.db')
MEMORY_BACKUP_DIR = os.path.join(RUNTIME_DIR, 'memory_backups')
RESTORE_POINTS_DIR = os.path.join(RUNTIME_DIR, 'restore_points')
CHAT_HISTORY_FILE = os.path.join(RUNTIME_DIR, 'chat_history.json')
REPORTS_DIR = os.path.join(RUNTIME_DIR, 'reports')
TASK_TRACKER_JSON = os.path.join(RUNTIME_DIR, 'task_tracker.json')
TASK_TRACKER_MD = os.path.join(RUNTIME_DIR, 'task_tracker.md')
BENCHMARK_RESULTS_FILE = os.path.join(RUNTIME_DIR, 'benchmark_results.json')
HEALTH_REPORT_FILE = os.path.join(RUNTIME_DIR, 'health_report.json')
def ensure_runtime_dir() -> None:
    """Create the legacy runtime directory for compatibility consumers."""
    Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
__all__ = ['APP_DIRECTORY_NAME', 'AppHomeOrigin', 'AppPaths', 'WorkspacePaths', 'RUNTIME_DIR', 'LOG_FILE', 'CHECKPOINT_FILE', 'METRICS_FILE', 'MEMORY_FILE', 'MEMORY_DB_FILE', 'MEMORY_BACKUP_DIR', 'RESTORE_POINTS_DIR', 'CHAT_HISTORY_FILE', 'REPORTS_DIR', 'TASK_TRACKER_JSON', 'TASK_TRACKER_MD', 'BENCHMARK_RESULTS_FILE', 'HEALTH_REPORT_FILE', 'ensure_runtime_dir']
