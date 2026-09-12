from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.observability.audit_projection_fields import MAX_AUDIT_TEXT, _safe_relative_path
from agent.runtime.path_safety import normalize_relative_path

_EXTERNAL = {"scope": "external"}


def _legacy_normalize(value: str | Path) -> str:
    return os.path.normpath(str(value).replace("\\", "/")).replace("\\", "/")


def _legacy_is_absolute_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized.startswith(("/", "//")) or (
        len(normalized) >= 3
        and normalized[0].isalpha()
        and normalized[1:3] == ":/"
    )


def _legacy_safe_relative_path(value: object) -> str | dict[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    if _legacy_is_absolute_path(raw):
        return dict(_EXTERNAL)
    normalized = _legacy_normalize(raw)
    if normalized in {"", "."}:
        return None
    return (
        dict(_EXTERNAL)
        if normalized == ".." or normalized.startswith("../")
        else normalized[:MAX_AUDIT_TEXT]
    )


@pytest.mark.parametrize(
    "value",
    [
        "a/b",
        "a/./b",
        "a/../b",
        "..",
        "../x",
        "/tmp/x",
        r"C:\tmp\x",
        ".",
        "foo/../C:/tmp/x",
        "a//b",
        r"a\b",
        r"\tmp\x",
        "a/../../b",
        "  \t",
        "",
    ],
)
def test_normalize_relative_path_matches_legacy_host_operation(value: str) -> None:
    assert normalize_relative_path(value) == _legacy_normalize(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        [],
        "",
        "  \t",
        ".",
        "./",
        "a/b",
        "a/./b",
        "a/../b",
        "a/../../b",
        "a//b",
        r"a\b",
        "..",
        "../x",
        "/tmp/x",
        r"C:\tmp\x",
        r"C:foo\..\bar",
        r"\tmp\x",
        r"\\server\share",
        "foo/../C:/tmp/x",
    ],
)
def test_audit_projection_without_workspace_matches_legacy_oracle(value: object) -> None:
    assert _safe_relative_path(value, None) == _legacy_safe_relative_path(value)


def test_audit_projection_without_workspace_preserves_legacy_drive_like_tail() -> None:
    value = "foo/../C:/tmp/x"

    assert _legacy_safe_relative_path(value) == "C:/tmp/x"
    assert _safe_relative_path(value, None) == "C:/tmp/x"


def test_audit_projection_without_workspace_keeps_bounded_relative_paths() -> None:
    value = "nested/" + "x" * 400

    projected = _safe_relative_path(value, None)

    assert projected == value[:MAX_AUDIT_TEXT]


def test_audit_projection_with_workspace_preserves_internal_and_external_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert _safe_relative_path(str(workspace / "src" / "main.py"), workspace) == "src/main.py"
    assert _safe_relative_path(str(workspace / "src" / "nested" / "main.py"), workspace) == "src/nested/main.py"
    assert _safe_relative_path(r"src\nested\main.py", workspace) == "src/nested/main.py"
    assert _safe_relative_path(str(tmp_path / "outside.py"), workspace) == _EXTERNAL
    assert _safe_relative_path("../outside.py", workspace) == _EXTERNAL
    assert _safe_relative_path(".", workspace) == _EXTERNAL
