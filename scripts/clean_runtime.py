"""Safely archive legacy runtime state and remove disposable caches."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAMES = (
    "agent.log",
    "agent_memory.json",
    "agent_metrics.jsonl",
    "benchmark_results.json",
    "health_report.json",
    "memory_backups",
    "reports",
    "task_tracker.json",
    "task_tracker.md",
)
DISPOSABLE_NAMES = (
    ".github/workflows/ci.yml.bak",
    ".pytest_temp",
    ".pytest_tmp",
    ".test_runtime",
    "__pycache__",
)


@dataclass(frozen=True)
class CleanupItem:
    path: Path
    action: str


def discover_cleanup(root: Path, *, include_workspace_temp: bool = False) -> tuple[CleanupItem, ...]:
    """Return only allowlisted cleanup candidates below ``root``."""
    resolved_root = root.resolve()
    names = list(ARCHIVE_NAMES) + list(DISPOSABLE_NAMES)
    if include_workspace_temp:
        names.append(".temp_analysis")
    items: list[CleanupItem] = []
    for name in names:
        path = (resolved_root / name).resolve()
        path.relative_to(resolved_root)
        if path.exists():
            action = "archive" if name in ARCHIVE_NAMES else "delete"
            items.append(CleanupItem(path, action))
    return tuple(items)


def apply_cleanup(items: Sequence[CleanupItem], archive_dir: Path) -> tuple[str, ...]:
    """Apply a previously reviewed plan, archiving user state before cleanup."""
    messages: list[str] = []
    for item in items:
        if item.action == "archive":
            archive_dir.mkdir(parents=True, exist_ok=True)
            destination = archive_dir / item.path.name
            shutil.move(str(item.path), str(destination))
            messages.append(f"archived {item.path} -> {destination}")
        elif item.action == "delete":
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
            messages.append(f"deleted {item.path}")
        else:
            raise ValueError(f"unsupported cleanup action: {item.action}")
    return tuple(messages)


def _archive_dir(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / "runtime" / "archive" / f"legacy-root-{timestamp}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the displayed cleanup plan")
    parser.add_argument(
        "--include-workspace-temp",
        action="store_true",
        help="also remove the legacy .temp_analysis workspace",
    )
    args = parser.parse_args(argv)
    items = discover_cleanup(PROJECT_ROOT, include_workspace_temp=args.include_workspace_temp)
    if not items:
        print("No legacy runtime artifacts found.")
        return 0
    for item in items:
        print(f"{item.action}: {item.path.relative_to(PROJECT_ROOT)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to execute this plan.")
        return 0
    for message in apply_cleanup(items, _archive_dir(PROJECT_ROOT)):
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
