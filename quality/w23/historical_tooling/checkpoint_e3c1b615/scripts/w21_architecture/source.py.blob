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

# W21 checkers keep their logical ``agent/...`` paths so their frozen rule and
# fixture identities remain stable.  W22 moved a finite set of owners out of
# the Agent package, so the final layout needs an explicit physical projection
# for those paths instead of silently looking under ``llm_agent.agent``.
_FINAL_W22_LOGICAL_PREFIXES = (
    ("agent/actions", "actions"),
    ("agent/application_services", "application/services"),
    ("agent/discovery", "discovery"),
    ("agent/execution", "execution"),
    ("agent/extensions", "extensions"),
    ("agent/filesystem", "filesystem"),
    ("agent/interfaces", "interfaces"),
    ("agent/outputs", "outputs"),
    ("agent/process", "process"),
    ("agent/storage", "storage"),
    ("agent/workspace", "workspace"),
)
_FINAL_W22_EXACT_PATHS = {
    "agent/engineering/cli.py": "interfaces/cli/engineering.py",
}


def _final_w22_physical_suffix(relative: str) -> str | None:
    exact = _FINAL_W22_EXACT_PATHS.get(relative)
    if exact is not None:
        return exact
    for logical_prefix, physical_suffix in _FINAL_W22_LOGICAL_PREFIXES:
        if relative == logical_prefix:
            return physical_suffix
        if relative.startswith(logical_prefix + "/"):
            return f"{physical_suffix}/{relative[len(logical_prefix) + 1:]}"
    return None


def _final_w22_logical_prefix(path_suffix: str) -> str | None:
    for logical, physical in _FINAL_W22_EXACT_PATHS.items():
        if path_suffix == physical:
            return logical
    for logical_prefix, physical_suffix in _FINAL_W22_LOGICAL_PREFIXES:
        if path_suffix == physical_suffix:
            return logical_prefix
        if path_suffix.startswith(physical_suffix + "/"):
            return f"{logical_prefix}/{path_suffix[len(physical_suffix) + 1:]}"
    return None


def _final_w22_projected_module(module: str) -> str | None:
    for logical_prefix, physical_suffix in _FINAL_W22_LOGICAL_PREFIXES:
        logical_module = logical_prefix.replace("/", ".")
        physical_module = "llm_agent." + physical_suffix.replace("/", ".")
        if module == physical_module:
            return logical_module
        if module.startswith(physical_module + "."):
            return logical_module + module[len(physical_module):]
    if module == "llm_agent.interfaces.cli.engineering":
        return "agent.engineering.cli"
    return None


def _final_w22_physical_module(module: str) -> str | None:
    for logical_prefix, physical_suffix in _FINAL_W22_LOGICAL_PREFIXES:
        logical_module = logical_prefix.replace("/", ".")
        physical_module = "llm_agent." + physical_suffix.replace("/", ".")
        if module == logical_module:
            return physical_module
        if module.startswith(logical_module + "."):
            return physical_module + module[len(logical_module):]
    if module == "agent.engineering.cli":
        return "llm_agent.interfaces.cli.engineering"
    return None


def normalize_relative(path: str | Path) -> str:
    """Return a stable slash-separated repository-relative spelling."""

    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


@dataclass(frozen=True)
class SourceLayout:
    """One of the finite repository layouts needed by the W22 migration."""

    profile: str
    repository_root: Path
    python_source_root: Path
    package_directory: Path
    import_module_root: str
    agent_module_root: str | None

    def w21_module_identity(self, module: str) -> str | None:
        """Map a W22 module to its frozen W21 Agent identity when applicable."""

        if self.profile == "final-w22" and not (
            module == self.agent_module_root
            or (self.agent_module_root is not None and module.startswith(self.agent_module_root + "."))
        ):
            # W21's frozen Agent graph deliberately has no identity for the
            # new top-level W22 platform/application packages. Those edges
            # are governed by the W22 owner policy; projecting them back into
            # the frozen graph would manufacture false W21 new-edge findings.
            return None
        prefix = self.agent_module_root
        if prefix is None or (module != prefix and not module.startswith(prefix + ".")):
            return None
        return "agent" + module[len(prefix) :]

    def project_w21_module(self, module: str) -> str | None:
        """Project a frozen W21 Agent identity into this declared layout."""

        if self.profile == "final-w22":
            projected = _final_w22_physical_module(module)
            if projected is not None:
                return projected
        prefix = self.agent_module_root
        if prefix is None or (module != "agent" and not module.startswith("agent.")):
            return None
        return prefix + module[len("agent") :]

    def graph_relative_path(self, path: Path) -> str:
        """Return the deterministic source identity used by graph signatures."""

        identity_root = self.repository_root if self.profile == "legacy-agent" else self.python_source_root
        return path.resolve().relative_to(identity_root.resolve()).as_posix()

    @property
    def agent_source_directory(self) -> Path | None:
        """Physical directory for the W21 Agent source tree, if present in this profile."""

        if self.agent_module_root is None:
            return None
        if self.profile == "final-w22":
            return self.package_directory / "agent"
        return self.package_directory

    def path_for_w21_relative(self, relative: str | Path) -> Path:
        """Resolve a frozen W21 Agent path in this finite source layout."""

        value = normalize_relative(relative)
        if self.profile == "final-w22":
            physical_suffix = _final_w22_physical_suffix(value)
            if physical_suffix is not None:
                return self.repository_root / "src" / "llm_agent" / physical_suffix
        if value == "agent":
            suffix = Path()
        elif value.startswith("agent/"):
            suffix = Path(value[len("agent/") :])
        else:
            return self.repository_root / value
        directory = self.agent_source_directory
        if directory is None:
            raise ValueError(f"layout {self.profile!r} has no W21 Agent source directory")
        return directory / suffix

    def w21_relative_path(self, path: Path) -> str:
        """Render an Agent source path with its stable pre-W22 repository spelling."""

        if self.profile == "final-w22":
            try:
                suffix = path.resolve().relative_to((self.repository_root / "src" / "llm_agent").resolve()).as_posix()
            except ValueError:
                pass
            else:
                logical = _final_w22_logical_prefix(suffix)
                if logical is not None:
                    return logical
        directory = self.agent_source_directory
        if directory is not None:
            try:
                suffix = path.resolve().relative_to(directory.resolve()).as_posix()
            except ValueError:
                pass
            else:
                return "agent" if suffix == "." else f"agent/{suffix}"
        return path.resolve().relative_to(self.repository_root).as_posix()

    @classmethod
    def for_profile(cls, repository_root: Path, profile: str) -> SourceLayout:
        """Resolve a supported source layout without inferring it from the candidate."""

        root = Path(repository_root).resolve()
        profiles = {
            "legacy-agent": (Path("."), Path("agent"), "agent", "agent"),
            "src-agent": (Path("src"), Path("agent"), "agent", "agent"),
            "provisional-product": (Path("src"), Path("llm_agent"), "llm_agent", None),
            "final-w22": (Path("src"), Path("llm_agent"), "llm_agent", "llm_agent.agent"),
        }
        try:
            source_relative, package_relative, import_root, agent_root = profiles[profile]
        except KeyError as exc:
            raise ValueError(f"unsupported W22 source layout profile: {profile!r}") from exc
        source_root = (root / source_relative).resolve()
        package_directory = (source_root / package_relative).resolve()
        return cls(profile, root, source_root, package_directory, import_root, agent_root)


def w21_source_layout(repository_root: Path) -> SourceLayout:
    """Resolve the physical W21 Agent checkout layout for legacy gates.

    The supported layouts include the pre-move ``agent/`` tree used by
    synthetic/historical repositories, the Phase 2 ``src/agent/`` tree, and
    the final W22 ``src/llm_agent`` candidate.
    Ambiguous roots fail closed instead of silently choosing one.
    """

    root = Path(repository_root).resolve()
    legacy = SourceLayout.for_profile(root, "legacy-agent")
    source = SourceLayout.for_profile(root, "src-agent")
    final = SourceLayout.for_profile(root, "final-w22")
    legacy_present = legacy.agent_source_directory is not None and legacy.agent_source_directory.is_dir()
    source_present = source.agent_source_directory is not None and source.agent_source_directory.is_dir()
    final_present = final.agent_source_directory is not None and final.agent_source_directory.is_dir()
    if legacy_present and source_present:
        raise ValueError(f"repository has ambiguous W21 Agent source roots: {root}")
    if legacy_present and final_present:
        raise ValueError(f"repository has ambiguous W21 Agent source roots: {root}")
    if source_present and final_present:
        raise ValueError(f"repository has ambiguous W21 Agent source roots: {root}")
    if final_present:
        return final
    return source if source_present else legacy


def module_name_for_path(package_root: Path, path: Path, import_module_root: str = "") -> str:
    """Map a source file to its import identity, independent of repository paths."""

    relative = path.resolve().relative_to(package_root.resolve()).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    suffix = ".".join(parts)
    if import_module_root and suffix:
        return f"{import_module_root}.{suffix}"
    return import_module_root or suffix


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


@dataclass(init=False)
class RepositorySource:
    """Explicit source context shared by all migrated checkers."""

    root: Path
    layout: SourceLayout
    _paths: dict[str, Path] | None = field(default=None, init=False, repr=False)
    _trees: dict[str, ast.Module | None] = field(default_factory=dict, init=False, repr=False)

    def __init__(self, root: Path, layout: SourceLayout | str | None = None) -> None:
        resolved_root = Path(root).resolve()
        if layout is None:
            resolved_layout = SourceLayout.for_profile(resolved_root, "legacy-agent")
        elif isinstance(layout, str):
            resolved_layout = SourceLayout.for_profile(resolved_root, layout)
        else:
            resolved_layout = layout
        if resolved_layout.repository_root.resolve() != resolved_root:
            raise ValueError("source layout repository root does not match RepositorySource root")
        self.root = resolved_root
        self.layout = resolved_layout
        self._paths = None
        self._trees = {}

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

    def python_files(self, relative_root: str | None = None) -> tuple[Path, ...]:
        assert isinstance(self.layout, SourceLayout)
        base = self.layout.package_directory if relative_root is None else self.path(relative_root)
        if not base.exists():
            return ()
        if base.is_file():
            return (base,) if base.suffix == ".py" else ()
        return tuple(sorted((item for item in base.rglob("*.py") if item.is_file()), key=self.relative))

    def module_paths(self) -> dict[str, Path]:
        if self._paths is None:
            assert isinstance(self.layout, SourceLayout)
            self._paths = {
                module_name_for_path(self.layout.package_directory, path, self.layout.import_module_root): path
                for path in self.python_files()
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
