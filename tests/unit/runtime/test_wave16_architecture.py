from __future__ import annotations

import shutil

from scripts import check_wave16_architecture as checker


def _artifact_name(*parts: str) -> str:
    return "".join(parts)


def test_wave16_mutation_copy_excludes_local_artifacts() -> None:
    required = {
        _artifact_name(".", "git"),
        _artifact_name(".", "venv"),
        _artifact_name(".", "audit", "-", "local"),
        _artifact_name(".", "agent", "-", "local"),
        _artifact_name("__", "pycache", "__"),
        _artifact_name(".", "pytest", "_", "cache"),
        _artifact_name(".", "pytest", "_", "temp"),
        _artifact_name(".", "tmp"),
        _artifact_name(".", "mypy", "_", "cache"),
        _artifact_name(".", "ruff", "_", "cache"),
    }
    assert required.issubset(set(checker.LOCAL_COPY_EXCLUDES))

    ignored = shutil.ignore_patterns(*checker.LOCAL_COPY_EXCLUDES)(
        "repo",
        sorted(required | {"agent", "scripts"}),
    )
    assert required.issubset(set(ignored))
    assert {"agent", "scripts"}.isdisjoint(ignored)


def test_wave16_mutation_coverage_remains_complete() -> None:
    arms = checker._mutation_arms()
    assert tuple(arm.arm_id for arm in arms) == checker.REQUIRED_MUTATION_ARMS
    assert len(arms) == 16
