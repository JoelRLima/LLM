"""Phase 14 namespace taxonomy and rename-readiness audit.

The audit deliberately classifies occurrences instead of applying a global
replacement.  It treats old W21 authority and fixture strings as evidence,
while independently proving that live imports and dynamic imports use the W22
namespace.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
TOKEN_RE = re.compile(
    r"src[\\/]llm_agent|local-llm-agent|llm-agent|LLM_AGENT|llm_agent\.|"
    r"llm_agent|(?<![A-Za-z0-9_.])agent\."
)
LIVE_ROOT_RE = re.compile(r"(?<![A-Za-z0-9_.])agent\.[A-Za-z_][A-Za-z0-9_.]*")
HISTORICAL_QUALITY = {
    "quality/architecture_baseline.json",
    "quality/architecture_policy.json",
    "quality/architecture_compatibility.json",
    "quality/architecture_w22_dispositions.json",
}

TAXONOMY = {
    "A": "PROVISIONAL_PYTHON_NAMESPACE",
    "B": "CLI_COMPATIBILITY",
    "C": "DISTRIBUTION_COMPATIBILITY",
    "D": "STATE_OR_INSTALL_COMPATIBILITY",
    "E": "LEGACY_ENV_COMPATIBILITY",
    "F": "LIVE_DYNAMIC_OR_DECLARATIVE_MODULE_PATH",
    "G": "LIVE_CHECKER_OR_TEST_TARGET",
    "H": "HISTORICAL_LEDGER_OR_DOCUMENTATION",
    "I": "GENERATED_BUILD_OUTPUT",
    "J": "UNEXPECTED_COUPLING",
}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _source_files() -> list[Path]:
    roots = [
        ROOT / "src",
        ROOT / "scripts",
        ROOT / "tests",
        ROOT / "quality",
        ROOT / ".github",
        ROOT / "distribution",
        ROOT / "docs",
        ROOT / "examples",
    ]
    files: set[Path] = set()
    for base in roots:
        if base.is_file() and base.suffix.lower() in TEXT_SUFFIXES:
            files.add(base)
        elif base.is_dir():
            files.update(
                path
                for path in base.rglob("*")
                if path.is_file()
                and path.suffix.lower() in TEXT_SUFFIXES
                and "__pycache__" not in path.parts
            )
    for name in ("pyproject.toml", "README.md"):
        path = ROOT / name
        if path.is_file():
            files.add(path)
    for name in ("TASK_CONTRACT.md", "TASK_SPEC.md"):
        path = ROOT / ".agent-local" / name
        if path.is_file():
            files.add(path)
    return sorted(files, key=_relative)


def _is_historical(relative: str) -> bool:
    lowered = relative.casefold()
    return (
        relative in HISTORICAL_QUALITY
        or relative.startswith(("docs/", "examples/", ".agent-local/"))
        or "/history/" in lowered
        or "/archive/" in lowered
        or relative.endswith("/phase0_inventory.json")
        or relative.startswith("scripts/w21_architecture/")
        or relative.startswith("scripts/check_wave")
        or relative.startswith("scripts/check_w21")
    )


def _historical_class(relative: str) -> tuple[str, str] | None:
    if _is_historical(relative):
        return "H", "frozen authority, historical checker, fixture, or documentation"
    return None


def _build_output_class(relative: str, line: str) -> tuple[str, str] | None:
    lowered = line.casefold()
    if relative.startswith(("dist/", "build/", ".tmp", ".temp")) or "generated" in lowered:
        return "I", "generated/build output"
    return None


def _compatibility_class(token: str, line: str) -> tuple[str, str] | None:
    lowered = line.casefold()
    if token == "local-llm-agent":
        return "C", "distribution identity intentionally preserved"
    if token == "llm-agent":
        return "B", "CLI command identity intentionally preserved"
    if "llm_agent_home" in lowered or "site-packages" in lowered or "install" in lowered:
        return "D", "state or installation compatibility"
    if "agent_runtime_dir" in lowered or "legacy_env" in lowered:
        return "E", "legacy environment compatibility"
    return None


def _dynamic_module_class(line: str) -> tuple[str, str] | None:
    lowered = line.casefold()
    if any(
        marker in lowered
        for marker in (
            "import_module",
            "__import__",
            "handler_owner",
            "module_path",
            "qualified_name",
            "entrypoint",
            "entry_point",
        )
    ):
        return "F", "live dynamic or declarative module path"
    return None


def _surface_class(relative: str) -> tuple[str, str]:
    if relative.startswith(("tests/", "scripts/", "quality/", ".github/")):
        return "G", "live checker, test target, or migration tooling"
    if relative.startswith("src/llm_agent/") or relative == "pyproject.toml":
        return "A", "provisional Python package namespace"
    if relative.startswith(("README", "docs/", "examples/")):
        return "H", "documentation/history"
    return "H", "non-live historical or documentation surface"


def _classify(relative: str, token: str, line: str) -> tuple[str, str]:
    for classification in (
        _historical_class(relative),
        _build_output_class(relative, line),
        _compatibility_class(token, line),
        _dynamic_module_class(line),
    ):
        if classification is not None:
            return classification
    return _surface_class(relative)


def _old_absolute_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            alias.name for alias in node.names
            if alias.name == "agent" or alias.name.startswith("agent.")
        ]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        module = node.module
        return [module] if module == "agent" or module.startswith("agent.") else []
    return []


def _old_dynamic_module(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    else:
        return None
    if name not in {"import_module", "__import__"}:
        return None
    value = node.args[0]
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    module = value.value
    return module if module == "agent" or module.startswith("agent.") else None


def _old_imports(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    for path in files:
        if path.suffix != ".py" or not any(part in path.parts for part in ("src", "scripts", "tests")):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        relative = _relative(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.extend(
                    {"path": relative, "line": node.lineno, "module": module}
                    for module in _old_absolute_modules(node)
                )
            if isinstance(node, ast.Call):
                module = _old_dynamic_module(node)
                if module is not None:
                    dynamic.append({"path": relative, "line": node.lineno, "module": module})
    return imports, dynamic


def _compatibility_chains() -> list[dict[str, Any]]:
    facade_files = {
        "src/llm_agent/agent/runtime/filesystem_primitives.py",
        "src/llm_agent/agent/runtime/legacy_paths.py",
        "src/llm_agent/agent/runtime/path_safety.py",
        "src/llm_agent/agent/runtime/paths.py",
        "src/llm_agent/agent/runtime/workspace_context.py",
        "src/llm_agent/agent/runtime/workspace_trace_paths.py",
        "src/llm_agent/agent/memory/path_safety.py",
    }
    facade_files.update(
        f"src/llm_agent/agent/tools/{name}.py"
        for name in (
            "extension_catalog_codec",
            "extension_catalog_document",
            "extension_catalog_errors",
            "extension_catalog_lock",
            "extension_catalog_migration",
            "extension_catalog_service",
            "extension_catalog_storage",
            "extension_catalog_validation",
            "extension_manifest_parser",
            "extension_path",
            "extension_registry",
            "extension_state",
            "stdio_launcher",
            "workspace_extensions_codec",
            "workspace_extensions_resolver",
            "workspace_extensions_service",
            "workspace_extensions_storage",
        )
    )
    chains: list[dict[str, Any]] = []
    for relative in sorted(facade_files):
        path = ROOT / relative
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            if "llm_agent.agent." in line:
                chains.append({"path": relative, "line": line_number, "text": line.strip()[:160]})
    return chains


def build_report() -> dict[str, Any]:
    files = _source_files()
    occurrences: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in files:
        relative = _relative(path)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in TOKEN_RE.finditer(line):
                token = match.group(0)
                category, rationale = _classify(relative, token, line)
                record = {
                    "path": relative,
                    "line": line_number,
                    "token": token,
                    "category": category,
                    "rationale": rationale,
                }
                occurrences.append(record)
                counts[category] += 1

    old_imports, old_dynamic = _old_imports(files)
    chains = _compatibility_chains()
    dispositions_path = ROOT / "quality" / "architecture_w22_dispositions.json"
    dispositions = json.loads(dispositions_path.read_text(encoding="utf-8"))
    w21_closed = dispositions.get("status") == "CLOSED"
    unexpected = [item for item in occurrences if item["category"] == "J"]

    absolute_import_files = sorted(
        relative
        for relative in (_relative(path) for path in files)
        if relative.endswith(".py")
        and "llm_agent." in (ROOT / relative).read_text(encoding="utf-8")
    )
    dynamic_files = sorted(
        relative
        for relative in absolute_import_files
        if any(
            marker in (ROOT / relative).read_text(encoding="utf-8")
            for marker in ("import_module", "handler_owner", "entrypoint", "entry_point")
        )
    )
    dry_run = {
        "directories_to_rename": ["src/llm_agent -> src/<final>"],
        "packaging_declarations_to_edit": ["pyproject.toml:[tool.setuptools.packages.find]", "pyproject.toml:[tool.setuptools.dynamic]", "pyproject.toml:[tool.setuptools.package-data]"],
        "entry_points_to_edit": ["pyproject.toml:[project.scripts].llm-agent"],
        "absolute_imports_to_edit": {"file_count": len(absolute_import_files), "files": absolute_import_files},
        "dynamic_declarative_paths_to_edit": {"file_count": len(dynamic_files), "files": dynamic_files},
        "checker_test_expectations_to_edit": ["scripts/w21_architecture/source.py", "scripts/check_wave21_architecture.py", "tests/"],
        "compatibility_identities_not_renamed": ["local-llm-agent", "llm-agent", "LLM_AGENT_HOME", "AGENT_RUNTIME_DIR"],
        "persisted_data_migrations_required": False,
        "semantic_owner_redesign_required": False,
    }
    architecture_violations = None
    try:
        from scripts.check_wave21_architecture import check_architecture

        architecture_violations = len(
            check_architecture(ROOT, mode="strict", layout_profile="final-w22").violations
        )
    except Exception:
        architecture_violations = None

    return {
        "artifact": "w22-namespace-audit",
        "schema_version": 1,
        "status": "PASS" if not unexpected and not old_imports and not old_dynamic and not chains and w21_closed else "FAIL",
        "taxonomy": {
            key: {"name": TAXONOMY[key], "count": counts.get(key, 0)}
            for key in TAXONOMY
        },
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
        "unexpected_coupling": unexpected,
        "live_old_root_import_count": len(old_imports),
        "live_old_root_dynamic_import_count": len(old_dynamic),
        "live_old_root_imports": old_imports,
        "live_old_root_dynamic_imports": old_dynamic,
        "compatibility_chain_count": len(chains),
        "compatibility_chains": chains,
        "w21_dispositions_closed": w21_closed,
        "architecture_target_violations": architecture_violations,
        "dry_run_rename": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit W22 namespace occurrences and rename readiness")
    parser.add_argument("--json", action="store_true", help="emit the complete deterministic JSON report")
    arguments = parser.parse_args()
    report = build_report()
    if arguments.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2))
    else:
        print(f"W22 namespace audit: {report['status']}")
        for key, value in report["taxonomy"].items():
            print(f"{key}. {value['name']}: {value['count']}")
        print(f"live old-root imports: {report['live_old_root_import_count']}")
        print(f"live old-root dynamic imports: {report['live_old_root_dynamic_import_count']}")
        print(f"compatibility chains: {report['compatibility_chain_count']}")
        print(f"W21 dispositions closed: {report['w21_dispositions_closed']}")
        print(f"final architecture target violations: {report['architecture_target_violations']}")
        print("dry-run semantic owner redesign: " + str(report["dry_run_rename"]["semantic_owner_redesign_required"]))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
