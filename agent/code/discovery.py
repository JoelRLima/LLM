from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Iterable

from agent.code.contracts import ProjectProfile
from agent.runtime.path_safety import (
    WorkspacePathError,
    is_link_like,
    resolve_workspace_path,
)

IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "runtime",
        "dist",
        "build",
    }
)

MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "setup.py",
        "setup.cfg",
        "package.json",
        "tsconfig.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
    }
)

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
}


class ProjectDiscovery:
    def __init__(self, root: str | Path, max_files: int = 5000) -> None:
        self.root = Path(root).resolve()
        self.max_files = max_files

    def iter_files(self) -> Iterable[Path]:
        count = 0
        for current, directory_names, file_names in os.walk(
            self.root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            safe_directories: list[str] = []
            for name in sorted(directory_names):
                path = current_path / name
                if name in IGNORED_DIRECTORIES or is_link_like(path):
                    continue
                try:
                    resolve_workspace_path(
                        self.root,
                        path,
                        require_directory=True,
                    )
                except (OSError, WorkspacePathError):
                    continue
                safe_directories.append(name)
            directory_names[:] = safe_directories

            for name in sorted(file_names):
                path = current_path / name
                if is_link_like(path):
                    continue
                try:
                    resolved = resolve_workspace_path(
                        self.root,
                        path,
                        require_file=True,
                    )
                except (OSError, WorkspacePathError):
                    continue
                yield resolved
                count += 1
                if count >= self.max_files:
                    return

    def discover(self) -> ProjectProfile:
        languages: Counter[str] = Counter()
        manifests: list[str] = []
        source_roots: set[str] = set()
        test_roots: set[str] = set()
        scanned = 0
        for path in self.iter_files():
            scanned += 1
            relative = path.relative_to(self.root)
            relative_posix = relative.as_posix()
            if path.name in MANIFESTS:
                manifests.append(relative_posix)
            language = LANGUAGE_BY_EXTENSION.get(path.suffix.lower())
            if language:
                languages[language] += 1
                if len(relative.parts) > 1:
                    source_roots.add(relative.parts[0])
            if any(part.lower() in {"test", "tests", "spec", "specs"} for part in relative.parts[:-1]):
                test_roots.add(relative.parts[0])

        truncated = scanned >= self.max_files
        git_metadata = self.root / ".git"
        vcs = (
            "git"
            if not is_link_like(git_metadata) and git_metadata.exists()
            else None
        )
        return ProjectProfile(
            root=str(self.root),
            vcs=vcs,
            languages=dict(sorted(languages.items())),
            manifests=tuple(sorted(manifests)),
            source_roots=tuple(sorted(source_roots)),
            test_roots=tuple(sorted(test_roots)),
            scanned_files=scanned,
            truncated=truncated,
        )
