"""Bounded selection mechanics for validation-impact planning."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from agent.code.contracts import ProjectProfile
from agent.code.discovery import ProjectDiscovery
from agent.code.intelligence import CodeIntelligenceService
from agent.runtime.path_safety import resolve_workspace_path

from .validation_impact_contracts import ValidationScope, ValidationSelection
from .validation_impact_mapping_support import direct_test_mappings, index_test_mappings

TARGETED_TEST_CAP = 20
SUBSYSTEM_TEST_CAP = 40


def _normalize(value: str) -> str:
    normalized = str(value).replace("\\", "/").strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def _under(path: str, root: str) -> bool:
    path_value = _normalize(path)
    root_value = _normalize(root)
    return path_value == root_value or path_value.startswith(root_value + "/")


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_normalize(value) for value in values if str(value).strip()}))


class ValidationImpactSelectionMixin:
    """Methods that select bounded validation commands from project impact."""

    max_discovery_files: int
    root: Path
    intelligence: CodeIntelligenceService

    def _static_selections(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        config: dict[str, object],
    ) -> tuple[ValidationSelection, ...]:
        python_files = tuple(
            item
            for item in changed_files
            if Path(item).suffix.casefold() in {".py", ".pyi"}
        )
        if "python" not in project.languages or not python_files:
            return ()
        selections = [
            ValidationSelection(
                "python-syntax",
                ValidationScope.FILE,
                python_files,
                "changed Python files only",
            )
        ]
        if config.get("ruff") is True:
            selections.append(
                ValidationSelection(
                    "ruff",
                    ValidationScope.FILE,
                    python_files,
                    "changed Python files only",
                )
            )
        if config.get("mypy") is True:
            selections.append(
                ValidationSelection(
                    "mypy",
                    ValidationScope.FILE,
                    python_files,
                    "changed Python files only",
                )
            )
        return tuple(selections)

    def _test_files(self, test_roots: Sequence[str]) -> tuple[str, ...]:
        roots = _unique_sorted(test_roots)
        if not roots:
            return ()
        files: list[str] = []
        discovery = ProjectDiscovery(self.root, max_files=self.max_discovery_files)
        for path in discovery.iter_files():
            relative = path.relative_to(self.root).as_posix()
            if (
                path.suffix.casefold() == ".py"
                and any(_under(relative, root) for root in roots)
            ):
                files.append(relative)
        return _unique_sorted(files)

    def _safe_python_file(self, relative: str) -> bool:
        if Path(relative).suffix.casefold() != ".py":
            return False
        try:
            resolve_workspace_path(self.root, relative, require_file=True)
        except (OSError, ValueError):
            return False
        return True

    def _mapped_tests(
        self,
        changed_files: Sequence[str],
        test_files: Sequence[str],
    ) -> tuple[set[str], dict[str, set[str]]]:
        python_changes = [
            item
            for item in changed_files
            if Path(item).suffix.casefold() in {".py", ".pyi"}
        ]
        mapped, reasons = direct_test_mappings(python_changes, test_files)
        index_test_mappings(
            self.intelligence,
            python_changes,
            test_files,
            mapped,
            reasons,
            self._modules_for_path,
            self._import_resolves_change,
        )
        return mapped, reasons

    @staticmethod
    def _modules_for_path(path: str) -> tuple[str, ...]:
        normalized = _normalize(path)
        if normalized.casefold().endswith(".pyi"):
            without_suffix = normalized[:-4]
        elif normalized.casefold().endswith(".py"):
            without_suffix = normalized[:-3]
        else:
            return ()
        parts = without_suffix.split("/")
        if parts[-1] == "__init__":
            parts.pop()
        if not parts:
            return ()
        return (".".join(parts),)

    def _import_resolves_change(
        self,
        target: str,
        source_file: str,
        changed_modules: set[str],
    ) -> bool:
        raw = str(target).strip()
        if not raw:
            return False
        leading = len(raw) - len(raw.lstrip("."))
        remainder = raw[leading:]
        if leading:
            source_modules = self._modules_for_path(source_file)
            if not source_modules:
                return False
            source_parts = source_modules[0].split(".")
            base_length = max(0, len(source_parts) - leading)
            module = ".".join((*source_parts[:base_length], remainder))
        else:
            module = remainder
        module = module.rstrip(".")
        if not module:
            return False
        return any(
            changed == module or changed.startswith(module + ".")
            for changed in changed_modules
        )

    def _bounded_subsystem(
        self,
        changed_files: Sequence[str],
        mapped_targets: Sequence[str],
        test_files: Sequence[str],
        test_roots: Sequence[str],
    ) -> tuple[str, tuple[str, ...]] | None:
        domains = self._changed_domains(changed_files, test_roots)
        candidates: list[tuple[str, tuple[str, ...]]] = []
        for domain in sorted(domains):
            domain_files = tuple(
                path for path in test_files if domain in Path(path).parts[:-1]
            )
            if not domain_files:
                continue
            root = self._common_test_boundary(domain_files, test_roots)
            if root is None:
                continue
            scoped = _unique_sorted(path for path in test_files if _under(path, root))
            if (
                len(scoped) <= SUBSYSTEM_TEST_CAP
                and set(mapped_targets).issubset(set(scoped))
            ):
                candidates.append((root, scoped))
        if len(candidates) != 1:
            return None
        return candidates[0]

    @staticmethod
    def _changed_domains(
        changed_files: Sequence[str],
        test_roots: Sequence[str],
    ) -> set[str]:
        generic = {
            "src",
            "lib",
            "app",
            "apps",
            "package",
            "packages",
            "test",
            "tests",
            "spec",
            "specs",
        }
        domains: set[str] = set()
        for changed in changed_files:
            parts = Path(changed).parts[:-1]
            if parts and parts[0] not in test_roots:
                parts = parts[1:]
            domains.update(
                part
                for part in parts
                if part.casefold() not in generic and part not in {"."}
            )
        return domains

    @staticmethod
    def _common_test_boundary(
        paths: Sequence[str],
        test_roots: Sequence[str],
    ) -> str | None:
        if not paths:
            return None
        split = [Path(path).parts[:-1] for path in paths]
        common: list[str] = []
        for values in zip(*split, strict=False):
            if len(set(values)) != 1:
                break
            common.append(values[0])
        boundary = "/".join(common)
        if not boundary:
            return None
        if any(_normalize(boundary) == _normalize(root) for root in test_roots):
            return None
        return boundary

    @staticmethod
    def _targeted_reason(reasons: dict[str, set[str]]) -> str:
        values = sorted({reason for entries in reasons.values() for reason in entries})
        if not values:
            return "deterministic relevant test mapping"
        return "deterministic relevant test mapping: " + "; ".join(values)


__all__ = [
    "SUBSYSTEM_TEST_CAP",
    "TARGETED_TEST_CAP",
    "ValidationImpactSelectionMixin",
]
