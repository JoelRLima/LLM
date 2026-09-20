"""Legacy W18 source-profile resolution for storage migration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agent.runtime.paths import AppHomeOrigin, AppPaths


@dataclass(frozen=True, slots=True)
class W18SourceProfile:
    name: str
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    log_dir: Path
    preserved_candidates: tuple[str, ...]


def source_profile_for(paths: AppPaths, environment: Mapping[str, str] | None = None) -> W18SourceProfile:
    env = dict(os.environ if environment is None else environment)
    home = paths.home_dir
    if paths.home_origin in {AppHomeOrigin.ARGUMENT, AppHomeOrigin.LLM_AGENT_HOME, AppHomeOrigin.INJECTED}:
        return W18SourceProfile(
            "explicit_home",
            home / "config",
            home / "data",
            home / "state",
            home / "cache",
            home / "logs",
            ("data", "state"),
        )
    if paths.home_origin is AppHomeOrigin.AGENT_RUNTIME_DIR:
        return W18SourceProfile(
            "legacy_runtime",
            home / "config",
            home / "data",
            home,
            home / "cache",
            home / "logs",
            (
                "data",
                "health_report.json",
                "last_workspace.json",
                "agent_memory.json",
                "agent_memory.db",
                "memory_backups",
                "agent_checkpoint.json",
                "agent_metrics.jsonl",
                "reports",
                "restore_points",
                "chat_history.json",
                "task_tracker.json",
                "task_tracker.md",
                "benchmark_results.json",
            ),
        )
    if paths.home_origin is AppHomeOrigin.WINDOWS_DEFAULT:
        local = Path(env.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")).expanduser().resolve()
        roaming = Path(env.get("APPDATA", Path.home() / "AppData" / "Roaming")).expanduser().resolve()
        namespace = local / "local-llm-agent"
        return W18SourceProfile(
            "windows_default",
            roaming / "local-llm-agent",
            namespace / "data",
            namespace / "state",
            namespace / "cache",
            namespace / "logs",
            (),
        )
    data_home = Path(env.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser().resolve()
    config_home = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser().resolve()
    state_home = Path(env.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser().resolve()
    cache_home = Path(env.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser().resolve()
    namespace = data_home / "local-llm-agent"
    return W18SourceProfile(
        "xdg_default",
        config_home / "local-llm-agent",
        namespace,
        state_home / "local-llm-agent",
        cache_home / "local-llm-agent",
        state_home / "local-llm-agent" / "logs",
        (),
    )


__all__ = ["W18SourceProfile", "source_profile_for"]
