"""Sanitized environment for subprocess-backed workspace skills."""

from __future__ import annotations

import os
from collections.abc import Mapping

from agent.runtime.workspace_context import WorkspaceContext

_REMOVED_ENVIRONMENT = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIFF_OPTS",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_INDEX_FILE",
        "GIT_INDEX_OUTPUT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
        "MYPY_CONFIG_FILE",
        "MYPYPATH",
        "COVERAGE_FILE",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONPYCACHEPREFIX",
        "RUFF_CACHE_DIR",
    }
)


def confined_process_environment(
    workspace: WorkspaceContext,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Remove path redirection from inherited env and relocate mutable homes."""

    environment = dict(source if source is not None else os.environ)
    for key in tuple(environment):
        upper = key.upper()
        if (
            upper in _REMOVED_ENVIRONMENT
            or upper.startswith("GIT_CONFIG_")
            or upper.startswith("GIT_TRACE")
        ):
            environment.pop(key, None)
    root = str(workspace.root)
    for key in (
        "APPDATA",
        "HOME",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    ):
        environment[key] = root
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_ATTR_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_PAGER"] = "cat"
    environment["PAGER"] = "cat"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


__all__ = ["confined_process_environment"]
