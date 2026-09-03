"""AST-based architecture checks for the observability spine."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OBSERVABILITY_ROOT = "agent/observability"
PRESENTATION_ROOT = "agent/presentation"
CLI_ROOT = "agent/interfaces/cli"
DOMAIN_ROOTS = ("agent/planning", "agent/orchestration", "agent/tools", "agent/runtime")
RICH_MODULES = frozenset({"rich", "curses", "prompt_toolkit", "textual"})
SECRET_FIELD_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "passphrase",
        "secret",
        "credential",
        "authorization",
        "cookie",
    }
)
MUTATION_NAMES = frozenset(
    {
        "approve",
        "approve_action",
        "call",
        "complete",
        "emit",
        "execute",
        "invoke",
        "mint_authority",
        "run",
        "save_checkpoint",
        "set_authority",
        "set_capability_ceiling",
        "write_checkpoint",
    }
)
PATH_SAFETY_CALLS = frozenset(
    {
        "abspath",
        "absolute",
        "commonpath",
        "commonprefix",
        "is_relative_to",
        "islink",
        "is_symlink",
        "lstat",
        "normpath",
        "readlink",
        "realpath",
        "relpath",
        "relative_to",
        "resolve",
        "samefile",
    }
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One deterministic source-local architecture finding."""

    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "detail": self.detail,
            "line": self.line,
        }


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sources(root: Path) -> Iterator[tuple[str, Path, ast.Module]]:
    agent_root = root / "agent"
    if not agent_root.exists():
        return
    for path in sorted(agent_root.rglob("*.py")):
        relative = _relative(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError) as exc:
            yield relative, path, ast.Module(body=[], type_ignores=[])
            del exc
            continue
        yield relative, path, tree


def _violation(rule: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _module_name(node: ast.ImportFrom | ast.Import) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else ""
    return node.module or ""


def _is_observability_or_presentation(relative: str) -> bool:
    return relative.startswith((OBSERVABILITY_ROOT + "/", PRESENTATION_ROOT + "/"))


def _has_name(node: ast.AST, names: frozenset[str]) -> bool:
    return any(isinstance(item, ast.Name) and item.id in names for item in ast.walk(node))


def _attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _check_s1_ui_imports(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not _is_observability_or_presentation(relative):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            module = _module_name(node)
            top = module.split(".", 1)[0]
            if top in RICH_MODULES or module.startswith("rich."):
                findings.append(_violation("W9-S1", relative, "UI/terminal import leaks into a UI-neutral owner", node))
    return findings


def _check_s2_cli_imports(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not _is_observability_or_presentation(relative):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = _module_name(node)
                module_path = module.replace(".", "/")
                if module_path == CLI_ROOT or module_path.startswith(CLI_ROOT + "/"):
                    findings.append(_violation("W9-S2", relative, "CLI import leaks into a UI-neutral owner", node))
    return findings


def _check_s3_mode_branching(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith(DOMAIN_ROOTS):
            continue
        mode_imported = False
        for body_node in tree.body:
            if isinstance(body_node, ast.ImportFrom) and body_node.module == "agent.observability.modes":
                mode_imported = any(alias.name == "ObservabilityMode" for alias in body_node.names)
            if isinstance(body_node, ast.Import):
                mode_imported = mode_imported or any(alias.name.endswith("observability") for alias in body_node.names)
        if not mode_imported and not _has_name(tree, frozenset({"ObservabilityMode"})):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp, ast.Match, ast.Compare)) and _has_name(
                node, frozenset({"ObservabilityMode"})
            ):
                findings.append(_violation("W9-S3", relative, "domain owner branches on ObservabilityMode", node))
    return findings


def _check_s4_direct_trace_calls(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith(DOMAIN_ROOTS):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _attribute_name(node.func)
            if name in {"TraceStore", "append_observation", "append_event", "write_observation"}:
                findings.append(_violation("W9-S4", relative, "distributed domain code calls trace storage directly", node))
    return findings


def _check_s5_presentation_mutation(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith(PRESENTATION_ROOT + "/"):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _attribute_name(node.func)
            if name in MUTATION_NAMES:
                findings.append(_violation("W9-S5", relative, f"presentation calls mutation API {name!r}", node))
    return findings


def _check_s6_diagnostic_state_leak(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not ("state" in relative or "checkpoint" in relative):
            continue
        module_has_diagnostic = _has_name(tree, frozenset({"DiagnosticRecord"}))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _attribute_name(node.func) in {"add_event", "append_state_event"}:
                if module_has_diagnostic or _has_name(node, frozenset({"DiagnosticRecord", "diagnostic"})):
                    findings.append(_violation("W9-S6", relative, "diagnostic data is appended to state/checkpoint truth", node))
    return findings


def _check_s7_presentation_event_kind(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith(PRESENTATION_ROOT + "/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if _attribute_name(node.value.func) == "RuntimeEventKind":
                    findings.append(_violation("W9-S7", relative, "presentation creates a semantic RuntimeEventKind", node))
    return findings


def _check_s8_global_trace_paths(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith((OBSERVABILITY_ROOT + "/", PRESENTATION_ROOT + "/")):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _attribute_name(node.func) not in {"Path", "PurePath"}:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                continue
            text = node.args[0].value.casefold()
            if "trace" in text or "bookmark" in text or "export" in text:
                findings.append(_violation("W9-S8", relative, "new trace path is process-global instead of workspace-bound", node))
    return findings


def _check_s9_unredacted_export_fields(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith((OBSERVABILITY_ROOT + "/", PRESENTATION_ROOT + "/")):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Dict, ast.DictComp)):
                continue
            keys = []
            if isinstance(node, ast.Dict):
                keys = [item.value for item in node.keys if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            for key in keys:
                normalized = key.casefold().replace("-", "_")
                if normalized in SECRET_FIELD_PARTS or any(part in normalized for part in ("api_key", "password", "authorization")):
                    findings.append(_violation("W9-S9", relative, f"obvious credential field {key!r} requires canonical redaction", node))
    return findings


def _check_s10_cli_types(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not relative.startswith(PRESENTATION_ROOT + "/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name) and node.annotation.id in {
                "Console", "Table", "Panel", "Live"
            }:
                findings.append(_violation("W9-S10", relative, "CLI rendering type leaks into presentation contract", node))
    return findings


def _check_s11_path_safety(root: Path) -> list[ArchitectureViolation]:
    """Keep path confinement/link inspection in the canonical runtime owner."""

    findings: list[ArchitectureViolation] = []
    for relative, _, tree in _sources(root):
        if not _is_observability_or_presentation(relative):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _attribute_name(node.func)
                if name in PATH_SAFETY_CALLS:
                    findings.append(
                        _violation(
                            "W9-S11",
                            relative,
                            f"manual path confinement/link inspection call {name!r} belongs in the runtime owner",
                            node,
                        )
                    )
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                walked_ancestor = any(
                    isinstance(child, ast.Attribute) and child.attr in {"parent", "parents"}
                    for child in ast.walk(node)
                )
                if walked_ancestor:
                    findings.append(
                        _violation(
                            "W9-S11",
                            relative,
                            "manual ancestor walking belongs in the runtime path-safety owner",
                            node,
                        )
                    )
    return findings


_check_s1 = _check_s1_ui_imports
_check_s2 = _check_s2_cli_imports
_check_s3 = _check_s3_mode_branching
_check_s4 = _check_s4_direct_trace_calls
_check_s5 = _check_s5_presentation_mutation
_check_s6 = _check_s6_diagnostic_state_leak
_check_s7 = _check_s7_presentation_event_kind
_check_s8 = _check_s8_global_trace_paths
_check_s9 = _check_s9_unredacted_export_fields
_check_s10 = _check_s10_cli_types
_check_s11 = _check_s11_path_safety


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    resolved_root = Path(root).expanduser().resolve() if root is not None else ROOT
    source = Path(path).expanduser().resolve()
    try:
        relative = _relative(source, resolved_root)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        return [ArchitectureViolation("W9-S0", str(source), f"source is missing or unparsable: {type(exc).__name__}")]
    del tree
    checks = (
        _check_s1,
        _check_s2,
        _check_s3,
        _check_s4,
        _check_s5,
        _check_s6,
        _check_s7,
        _check_s8,
        _check_s9,
        _check_s10,
        _check_s11,
    )
    return [finding for check in checks for finding in check(resolved_root) if finding.path == relative]


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    resolved = Path(root).expanduser().resolve()
    checks = (
        _check_s1,
        _check_s2,
        _check_s3,
        _check_s4,
        _check_s5,
        _check_s6,
        _check_s7,
        _check_s8,
        _check_s9,
        _check_s10,
        _check_s11,
    )
    return [finding for check in checks for finding in check(resolved)]


find_violations = check_architecture
check_wave9_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check observability ownership boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W9 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
