"""Finite Application contracts for explicit legacy-state migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm_agent.application.context import AppPaths


@dataclass(frozen=True, slots=True)
class StateMigrationRequest:
    source: str | Path
    workspace: str | Path
    app_paths: AppPaths


@dataclass(frozen=True, slots=True)
class StateMigrationResult:
    source: str
    copied: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def copied_count(self) -> int:
        return len(self.copied)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)
