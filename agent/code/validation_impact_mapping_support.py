"""Pure test-mapping helpers for validation-impact selection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def direct_test_mappings(
    python_changes: Sequence[str],
    test_files: Sequence[str],
) -> tuple[set[str], dict[str, set[str]]]:
    mapped: set[str] = set()
    reasons: dict[str, set[str]] = {}
    test_set = set(test_files)
    for changed in python_changes:
        if changed in test_set:
            mapped.add(changed)
            reasons.setdefault(changed, set()).add("changed test file")
        stem = Path(changed).stem
        conventional_names = {f"test_{stem}.py", f"{stem}_test.py"}
        for candidate in test_files:
            if Path(candidate).name in conventional_names:
                mapped.add(candidate)
                reasons.setdefault(candidate, set()).add(
                    f"conventional match for {changed}"
                )
    return mapped, reasons


def index_test_mappings(
    intelligence: Any,
    python_changes: Sequence[str],
    test_files: Sequence[str],
    mapped: set[str],
    reasons: dict[str, set[str]],
    modules_for_path: Callable[[str], Sequence[str]],
    import_resolves_change: Callable[[str, str, set[str]], bool],
) -> None:
    if not python_changes or not test_files:
        return
    try:
        index = intelligence.index_repository()
    except (OSError, UnicodeError, ValueError):
        index = None
    if index is None:
        return
    test_set = set(test_files)
    changed_modules = {
        module
        for path in python_changes
        for module in modules_for_path(path)
    }
    analyses = {
        analysis.file_path: analysis
        for analysis in index.analyses
        if analysis.file_path in test_set
    }
    for test_path, analysis in analyses.items():
        for edge in analysis.imports:
            if import_resolves_change(
                edge.target,
                edge.source_file,
                changed_modules,
            ):
                mapped.add(test_path)
                reasons.setdefault(test_path, set()).add(
                    "analyzed import edge to changed module"
                )
                break


__all__ = ["direct_test_mappings", "index_test_mappings"]
