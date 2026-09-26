"""Repository-relative source loading and small AST helpers.

The architecture checkers intentionally operate on an explicit repository
root.  That keeps fixture checks deterministic and prevents an installed
package or a coordinator checkout from becoming an accidental authority.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def normalize_relative(path: str | Path) -> str:
    """Return a stable slash-separated repository-relative spelling."""

    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def module_name_for_path(root: Path, path: Path) -> str:
    """Convert a Python file below ``root`` into its importable module name."""

    relative = path.resolve().relative_to(root.resolve()).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def literal_value(node: ast.AST | None) -> Any:
    """Return a literal AST value, or ``None`` for a non-literal expression."""

    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return None


def is_type_checking_test(node: ast.AST) -> bool:
    """Recognize the supported spellings of a ``TYPE_CHECKING`` guard."""

    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    return isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"


def qualified_name(node: ast.AST) -> tuple[str, ...]:
    """Return a dotted name for a ``Name``/``Attribute`` expression."""

    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


@dataclass
class RepositorySource:
    """Explicit source context shared by all migrated checkers."""

    root: Path
    _paths: dict[str, Path] | None = field(default=None, init=False, repr=False)
    _trees: dict[str, ast.Module | None] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    def path(self, relative: str | Path) -> Path:
        return self.root / normalize_relative(relative)

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def text(self, relative: str | Path) -> str:
        path = self.path(relative)
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    def exists(self, relative: str | Path) -> bool:
        return self.path(relative).is_file()

    def python_files(self, relative_root: str = "agent") -> tuple[Path, ...]:
        base = self.path(relative_root)
        if not base.exists():
            return ()
        if base.is_file():
            return (base,) if base.suffix == ".py" else ()
        return tuple(sorted((item for item in base.rglob("*.py") if item.is_file()), key=self.relative))

    def module_paths(self) -> dict[str, Path]:
        if self._paths is None:
            self._paths = {
                module_name_for_path(self.root, path): path
                for path in self.python_files("agent")
            }
        return dict(self._paths)

    def tree(self, relative: str | Path) -> ast.Module | None:
        key = normalize_relative(relative)
        if key not in self._trees:
            path = self.path(key)
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                self._trees[key] = None
            else:
                try:
                    # Empty Python files are valid modules.  Do not use a
                    # truthiness check here: it erases a real empty module.
                    self._trees[key] = ast.parse(source, filename=key)
                except SyntaxError:
                    self._trees[key] = None
        return self._trees[key]

    def tree_for_path(self, path: Path) -> ast.Module | None:
        return self.tree(self.relative(path))
