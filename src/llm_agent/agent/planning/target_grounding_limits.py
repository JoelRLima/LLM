"""Shared bounded-discovery limits for semantic target grounding."""

DEFAULT_MAX_FILES = 4096
DEFAULT_MAX_ENUMERATED_PATHS = 16384
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024
IDENTIFIER_CHUNK_BYTES = 64 * 1024
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".agent-local",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "site-packages",
    }
)

__all__ = [
    "DEFAULT_MAX_ENUMERATED_PATHS",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_TOTAL_SOURCE_BYTES",
    "EXCLUDED_DIRECTORY_NAMES",
    "IDENTIFIER_CHUNK_BYTES",
]
