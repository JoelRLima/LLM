"""Static ownership gates for the Wave 13 architecture boundaries.

The checker intentionally proves only source-structural properties.  Runtime
behaviour (for example, whether a verifier can be bypassed) belongs to the
focused reproducer tests owned by the implementation.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One deterministic Wave 13 source-local architecture finding."""

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


def _source(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _tree(root: Path, relative: str) -> ast.Module | None:
    source = _source(root, relative)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _violation(
    rule: str,
    relative: str,
    detail: str,
    node: ast.AST | None = None,
) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else ""
    return node.module or ""


def _nodes(tree: ast.AST | None) -> Iterator[ast.AST]:
    if tree is not None:
        yield from ast.walk(tree)


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in _nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _class(tree: ast.AST | None, name: str) -> ast.ClassDef | None:
    for node in _nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _method(owner: ast.ClassDef | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if owner is None:
        return None
    for node in owner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _required_tree(
    root: Path,
    rule: str,
    relative: str,
    findings: list[ArchitectureViolation],
) -> ast.Module | None:
    tree = _tree(root, relative)
    if tree is None:
        findings.append(_violation(rule, relative, "required owner is missing, unreadable, or unparsable"))
    return tree


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_strings(node: ast.AST | None) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[str] = []
    for item in node.elts:
        value = _literal_string(item)
        if value is None:
            return None
        values.append(value)
    return tuple(values)


def _literal_dict(node: ast.AST | None) -> dict[str, ast.AST] | None:
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        name = _literal_string(key)
        if name is None or value is None:
            return None
        if name in result:
            return None
        result[name] = value
    return result


def _dict_nodes(node: ast.AST | None) -> Iterator[ast.Dict]:
    for item in _nodes(node):
        if isinstance(item, ast.Dict):
            yield item


def _dict_keys(node: ast.Dict) -> set[str] | None:
    keys: set[str] = set()
    for key in node.keys:
        value = _literal_string(key)
        if value is None:
            return None
        keys.add(value)
    return keys


def _assignment_names(node: ast.AST) -> Iterator[str]:
    for item in _nodes(node):
        if not isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets: list[ast.AST] = []
        if isinstance(item, ast.Assign):
            targets.extend(item.targets)
        else:
            targets.append(item.target)
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id
            elif isinstance(target, ast.Attribute):
                yield target.attr


def _has_call(node: ast.AST | None, names: set[str]) -> bool:
    return any(
        isinstance(item, ast.Call)
        and (_qualified_name(item.func) in names or _qualified_name(item.func).rsplit(".", 1)[-1] in names)
        for item in _nodes(node)
    )


def _imported_names(tree: ast.AST | None, module: str) -> set[str]:
    names: set[str] = set()
    for node in _nodes(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == module:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _contains_raw_context_delimiter(node: ast.AST | None) -> ast.Constant | None:
    markers = (
        "<untrusted",
        "</untrusted",
        "<workspace_context",
        "</workspace_context",
        "```",
    )
    for item in _nodes(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            if any(marker in item.value.casefold() for marker in markers):
                return item
    return None


def _check_s1(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    production = root / "agent"
    try:
        paths = sorted(
            (path for path in production.rglob("*.py") if path.is_file()),
            key=lambda path: _relative(path, root),
        )
    except OSError:
        paths = []
    if not paths:
        return [_violation("W13-S1", "agent", "production Python owners are missing or unreadable")]
    for path in paths:
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation("W13-S1", relative, "production owner is missing, unreadable, or unparsable"))
            continue
        for node in _nodes(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.casefold()
                if "wave13" in lowered or "w13" in lowered:
                    findings.append(
                        _violation(
                            "W13-S1",
                            relative,
                            "production owner name contains the W13 roadmap name",
                            node,
                        )
                    )
    return findings


def _projection_name_is_authority(name: str) -> bool:
    lowered = name.casefold()
    return (
        ("trusted" in lowered and "system" in lowered)
        or ("system" in lowered and ("addition" in lowered or "authority" in lowered))
        or lowered in {"trusted_message", "trusted_prompt", "system_authority"}
    )


def _check_s2(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    tree = _required_tree(root, "W13-S2", "agent/llm/context_projection.py", findings)
    owner = _class(tree, "ModelContextProjection")
    if owner is None:
        findings.append(_violation("W13-S2", "agent/llm/context_projection.py", "ModelContextProjection owner is missing"))
        return findings
    for name in _assignment_names(owner):
        if _projection_name_is_authority(name):
            findings.append(
                _violation(
                    "W13-S2",
                    "agent/llm/context_projection.py",
                    "ModelContextProjection contains a trusted-system authority field/path",
                    owner,
                )
            )
    for node in _nodes(owner):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _projection_name_is_authority(node.name):
            findings.append(
                _violation(
                    "W13-S2",
                    "agent/llm/context_projection.py",
                    "ModelContextProjection exposes a trusted-system authority path",
                    node,
                )
            )
    return findings


def _check_s3(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/llm/context_projection.py"
    tree = _required_tree(root, "W13-S3", relative, findings)
    if tree is None:
        return findings
    forbidden_prefixes = (
        "agent.approval",
        "agent.code.validation",
        "agent.code.workflow",
        "agent.skills",
        "agent.tools",
        "subprocess",
    )
    forbidden_imported = {
        "AgentApplication",
        "ApprovalPort",
        "ChangeSetTransaction",
        "GitSkill",
        "ProjectValidator",
        "RepositoryStateSkill",
        "TaskRunner",
        "ToolInvocationGateway",
        "run_bounded_process",
    }
    forbidden_calls = {
        "ApprovalPort",
        "ChangeSetTransaction",
        "GitSkill",
        "ProjectValidator",
        "RepositoryStateSkill",
        "ToolInvocationGateway",
        "TaskRunner",
        "run_bounded_process",
        "approve",
        "commit",
        "execute_validation",
        "grant_capability",
        "waive_effect",
    }
    for node in _nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if module == "subprocess" or module.startswith(forbidden_prefixes):
                findings.append(_violation("W13-S3", relative, "auxiliary projector imports a forbidden authority/execution owner", node))
            if any(alias.name in forbidden_imported for alias in node.names):
                findings.append(_violation("W13-S3", relative, "auxiliary projector imports a forbidden owner", node))
        elif isinstance(node, ast.Call):
            qualified = _qualified_name(node.func)
            if qualified.startswith("subprocess.") or qualified.rsplit(".", 1)[-1] in forbidden_calls:
                findings.append(_violation("W13-S3", relative, "auxiliary projector calls a forbidden authority/execution owner", node))
    return findings


def _check_s4(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    owners = (
        ("agent/code/workflow_proposal.py", "_proposal_request"),
        ("agent/code/outcome_verifier.py", "verify"),
    )
    for relative, function_name in owners:
        tree = _required_tree(root, "W13-S4", relative, findings)
        if tree is None:
            continue
        owner: ast.AST | None = _function(tree, function_name)
        if relative.endswith("outcome_verifier.py"):
            owner = _method(_class(tree, "CodeOutcomeVerifier"), function_name)
        if owner is None:
            findings.append(_violation("W13-S4", relative, f"request builder {function_name} is missing"))
            continue
        codec_imported = {
            alias.name
            for node in _nodes(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "agent.llm.context_projection"
            for alias in node.names
        }
        if "render_untrusted_context_envelope" not in codec_imported:
            findings.append(_violation("W13-S4", relative, "request builder does not import the shared data codec"))
        else:
            aliases = {
                alias.asname or alias.name
                for node in _nodes(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "agent.llm.context_projection"
                for alias in node.names
                if alias.name == "render_untrusted_context_envelope"
            }
            if not _has_call(owner, aliases | {"render_untrusted_context_envelope"}):
                findings.append(_violation("W13-S4", relative, "request builder does not call the shared data codec", owner))
        if not _has_call(owner, {"fit_contextual_request"}):
            findings.append(_violation("W13-S4", relative, "request builder bypasses canonical context fitting", owner))
        raw = _contains_raw_context_delimiter(owner)
        if raw is not None:
            findings.append(_violation("W13-S4", relative, "request builder interpolates a raw XML/markdown context delimiter", raw))
    return findings


def _check_s5(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/code/outcome_verifier.py"
    tree = _required_tree(root, "W13-S5", relative, findings)
    owner = _class(tree, "CodeOutcomeVerifier")
    verify = _method(owner, "verify")
    if owner is None or verify is None:
        findings.append(_violation("W13-S5", relative, "CodeOutcomeVerifier.verify owner is missing"))
        return findings
    forbidden_modules = (
        "agent.approval",
        "agent.tools",
        "agent.code.changes",
        "agent.planning.task_semantics",
        "agent.application",
    )
    forbidden_names = {
        "ApprovalPort",
        "ChangeSetTransaction",
        "TaskSemantics",
        "ToolInvocationGateway",
        "waive_effect",
        "waive_write",
    }
    for node in _nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if module.startswith(forbidden_modules) or any(alias.name in forbidden_names for alias in node.names):
                findings.append(_violation("W13-S5", relative, "verifier imports an authority, approval, or execution owner", node))
        elif isinstance(node, ast.Call):
            qualified = _qualified_name(node.func)
            tail = qualified.rsplit(".", 1)[-1]
            if tail in {
                "ApprovalPort",
                "ChangeSetTransaction",
                "ToolInvocationGateway",
                "TaskSemantics",
                "approve",
                "commit",
                "grant_capability",
                "set_authority",
                "waive_effect",
                "waive_write",
            }:
                findings.append(_violation("W13-S5", relative, "verifier calls an authority, approval, or mutation owner", node))
        elif isinstance(node, ast.Attribute) and node.attr in {
            "approve",
            "commit",
            "grant_capability",
            "set_authority",
            "waive_effect",
            "waive_write",
        }:
            findings.append(_violation("W13-S5", relative, "verifier reaches an authority or mutation method", node))
    return findings


def _get_schema_return(owner: ast.ClassDef | None) -> ast.Dict | None:
    method = _method(owner, "get_schema")
    if method is None:
        return None
    returns = [node.value for node in _nodes(method) if isinstance(node, ast.Return) and node.value is not None]
    if len(returns) != 1 or not isinstance(returns[0], ast.Dict):
        return None
    return returns[0]


def _check_s6_property(
    relative: str,
    property_name: str,
    property_schema: ast.AST,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    lowered = property_name.casefold()
    if lowered in {"validation_scope", "pytest_scope", "test_scope"}:
        findings.append(_violation("W13-S6", relative, "code_task exposes a model-facing validation scope", property_schema))
    for node in _nodes(property_schema):
        if isinstance(node, ast.Dict):
            entries = _literal_dict(node)
            if entries is not None and "enum" in entries:
                values = _literal_strings(entries["enum"])
                if values is None:
                    if "validation" in lowered or "scope" in lowered:
                        findings.append(_violation("W13-S6", relative, "validation enum is dynamic and cannot be proven free of FULL", node))
                elif any(value.casefold() == "full" for value in values):
                    findings.append(_violation("W13-S6", relative, "model-facing FULL validation enum is exposed", node))
        if isinstance(node, ast.Attribute) and node.attr == "FULL":
            findings.append(_violation("W13-S6", relative, "model-facing FULL validation enum is exposed", node))
        if isinstance(node, ast.Name) and node.id.casefold() == "validationscope":
            findings.append(_violation("W13-S6", relative, "model-facing ValidationScope is exposed", node))
    return findings


def _check_s6_properties(
    relative: str,
    properties: dict[str, ast.AST],
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    include_tests = _literal_dict(properties.get("include_tests"))
    if include_tests is None or _literal_string(include_tests.get("type")) != "boolean":
        findings.append(_violation("W13-S6", relative, "include_tests is not exposed as a boolean"))
    for property_name, property_schema in properties.items():
        findings.extend(_check_s6_property(relative, property_name, property_schema))
    return findings


def _check_s6(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/skills/code_task.py"
    tree = _required_tree(root, "W13-S6", relative, findings)
    schema = _get_schema_return(_class(tree, "CodeTaskSkill"))
    if schema is None:
        findings.append(_violation("W13-S6", relative, "code_task public schema is missing or not a closed literal mapping"))
        return findings
    schema_entries = _literal_dict(schema)
    if schema_entries is None:
        findings.append(_violation("W13-S6", relative, "code_task schema is not a literal mapping", schema))
        return findings
    properties = _literal_dict(schema_entries.get("properties"))
    if properties is None:
        findings.append(_violation("W13-S6", relative, "code_task schema properties are missing or dynamic", schema))
        return findings
    findings.extend(_check_s6_properties(relative, properties))
    return findings

def _skill_spec_call(tree: ast.AST | None) -> ast.Call | None:
    for node in _nodes(tree):
        if isinstance(node, ast.Call) and _qualified_name(node.func).rsplit(".", 1)[-1] == "SkillSpec":
            positional = node.args
            if len(positional) >= 3:
                return node
    return None


def _capability_names(node: ast.AST | None) -> set[str] | None:
    if not isinstance(node, ast.Call) or _qualified_name(node.func).rsplit(".", 1)[-1] not in {"frozenset", "set"}:
        return None
    if len(node.args) != 1 or not isinstance(node.args[0], (ast.Set, ast.Tuple, ast.List)):
        return None
    values: set[str] = set()
    for item in node.args[0].elts:
        if not isinstance(item, ast.Attribute) or _qualified_name(item.value).rsplit(".", 1)[-1] != "C":
            return None
        values.add(item.attr)
    return values


def _find_repository_state_spec(
    catalog: ast.AST | None,
    relative: str,
    findings: list[ArchitectureViolation],
) -> ast.Call | None:
    for node in _nodes(catalog):
        if not isinstance(node, ast.Call) or _qualified_name(node.func).rsplit(".", 1)[-1] != "SkillSpec":
            continue
        if len(node.args) < 3:
            continue
        identity = tuple(_literal_string(item) for item in node.args[:3])
        if identity == (
            "agent.skills.repository_state",
            "RepositoryStateSkill",
            "repository_state",
        ):
            return node
    for node in _nodes(catalog):
        if not isinstance(node, ast.Call) or _qualified_name(node.func).rsplit(".", 1)[-1] != "SkillSpec":
            continue
        if len(node.args) >= 3 and _literal_string(node.args[2]) == "repository_state":
            findings.append(_violation("W13-S7", relative, "repository_state descriptor does not identify its canonical owner", node))
            return None
    findings.append(_violation("W13-S7", relative, "repository_state SkillSpec descriptor is missing"))
    return None


def _check_s7_descriptor(
    matching: ast.Call,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    keywords = {keyword.arg: keyword.value for keyword in matching.keywords if keyword.arg is not None}
    capabilities = _capability_names(keywords.get("capabilities"))
    if capabilities != {"READ", "VCS_READ", "PROCESS"}:
        findings.append(_violation("W13-S7", relative, "repository_state descriptor capabilities are not exactly READ+VCS_READ+PROCESS", matching))
    if _literal_string(keywords.get("category")) != "READ":
        findings.append(_violation("W13-S7", relative, "repository_state descriptor is not categorized as read-only", matching))
    public_fields = keywords.get("public_invocation_fields")
    if public_fields is not None:
        values = _literal_strings(public_fields)
        if values is None or values:
            findings.append(_violation("W13-S7", relative, "repository_state descriptor exposes model arguments", matching))
    return findings


def _check_s7_schema(
    skill: ast.AST | None,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    schema = _get_schema_return(_class(skill, "RepositoryStateSkill"))
    if schema is None:
        findings.append(_violation("W13-S7", relative, "repository_state schema is missing or dynamic"))
        return findings
    entries = _literal_dict(schema)
    if entries is None:
        findings.append(_violation("W13-S7", relative, "repository_state schema is not a closed literal mapping", schema))
        return findings
    properties = _literal_dict(entries.get("properties"))
    required = _literal_strings(entries.get("required"))
    if properties != {} or required != () or _literal_string(entries.get("additionalProperties")) is not None:
        additional = entries.get("additionalProperties")
        if properties != {} or required != () or not (isinstance(additional, ast.Constant) and additional.value is False):
            findings.append(_violation("W13-S7", relative, "repository_state schema accepts model arguments", schema))
    return findings


def _check_s7(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    catalog_relative = "agent/skills/catalog.py"
    skill_relative = "agent/skills/repository_state.py"
    catalog = _required_tree(root, "W13-S7", catalog_relative, findings)
    skill = _required_tree(root, "W13-S7", skill_relative, findings)
    matching = _find_repository_state_spec(catalog, catalog_relative, findings)
    if matching is None:
        return findings
    findings.extend(_check_s7_descriptor(matching, catalog_relative))
    findings.extend(_check_s7_schema(skill, skill_relative))
    return findings

def _assigned_value(tree: ast.AST | None, name: str) -> ast.AST | None:
    for node in _nodes(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def _check_s8(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    process_relative = "agent/skills/process_safety.py"
    git_relative = "agent/skills/git.py"
    shell_relative = "agent/skills/shell.py"
    process = _required_tree(root, "W13-S8", process_relative, findings)
    git = _required_tree(root, "W13-S8", git_relative, findings)
    _required_tree(root, "W13-S8", shell_relative, findings)
    allowed = _literal_strings(_assigned_value(process, "ALLOWED_SHELL_COMMANDS"))
    if allowed is None:
        findings.append(_violation("W13-S8", process_relative, "Git/Shell allowlist is missing or dynamic"))
    else:
        if "git log" not in allowed:
            findings.append(_violation("W13-S8", process_relative, "model-actionable Git no longer has the local-history log surface"))
        if any(value.casefold() in {"git status", "git diff"} for value in allowed):
            findings.append(_violation("W13-S8", process_relative, "model-actionable Git allowlist exposes status/diff"))
    history = _function(process, "local_history_arguments")
    if history is None or not any(
        isinstance(node, ast.Constant) and node.value == "log" for node in _nodes(history)
    ):
        findings.append(_violation("W13-S8", process_relative, "local_history_arguments does not structurally restrict Git to log"))
    git_owner = _class(git, "GitSkill")
    validated = _method(git_owner, "_validated_command")
    if validated is None:
        findings.append(_violation("W13-S8", git_relative, "GitSkill command validator is missing"))
    else:
        has_log_guard = any(
            isinstance(node, ast.Compare)
            and any(isinstance(comparator, ast.Constant) and comparator.value == "log" for comparator in node.comparators)
            for node in _nodes(validated)
        )
        if not has_log_guard:
            findings.append(_violation("W13-S8", git_relative, "GitSkill no longer guards the command name with log", validated))
        for node in _nodes(validated):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.casefold() in {"status", "diff"}:
                findings.append(_violation("W13-S8", git_relative, "GitSkill command validator contains a status/diff command", node))
    return findings


def _class_field_names(owner: ast.ClassDef | None) -> set[str]:
    if owner is None:
        return set()
    names: set[str] = set()
    for node in owner.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


def _method_dict_keys(owner: ast.ClassDef | None, method_name: str) -> list[set[str]]:
    method = _method(owner, method_name)
    if method is None:
        return []
    result: list[set[str]] = []
    for node in _dict_nodes(method):
        keys = _dict_keys(node)
        if keys is not None:
            result.append(keys)
    return result


def _check_s9(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/skills/repository_state.py"
    tree = _required_tree(root, "W13-S9", relative, findings)
    snapshot = _class(tree, "RepositoryStateSnapshot")
    entry = _class(tree, "RepositoryStateEntry")
    if snapshot is None or entry is None:
        findings.append(_violation("W13-S9", relative, "repository snapshot contract owner is missing"))
        return findings
    expected_snapshot = {
        "available",
        "branch",
        "head",
        "entries",
        "truncated",
        "complete",
        "total_entries_observed",
        "reason_code",
    }
    expected_entry = {"path", "index_status", "worktree_status", "untracked"}
    if _class_field_names(snapshot) != expected_snapshot:
        findings.append(_violation("W13-S9", relative, "repository snapshot fields drifted", snapshot))
    if _class_field_names(entry) != expected_entry:
        findings.append(_violation("W13-S9", relative, "repository entry fields drifted", entry))
    to_dict_keys = _method_dict_keys(snapshot, "to_dict")
    if not to_dict_keys or to_dict_keys[0] != expected_snapshot:
        findings.append(_violation("W13-S9", relative, "repository snapshot serialization contract drifted", snapshot))
    forbidden = {"diff", "patch", "content", "raw_diff", "raw_patch", "raw_content"}
    for owner in (snapshot, entry):
        for method_name in ("to_dict", "to_context_dict", "from_dict"):
            for keys in _method_dict_keys(owner, method_name):
                if keys & forbidden:
                    findings.append(_violation("W13-S9", relative, "repository-state snapshot exposes raw diff/patch/content", owner))
    return findings


def _function_return_list(function: ast.FunctionDef | ast.AsyncFunctionDef | None) -> ast.List | None:
    if function is None:
        return None
    returns = [node.value for node in _nodes(function) if isinstance(node, ast.Return) and node.value is not None]
    if len(returns) != 1 or not isinstance(returns[0], ast.List):
        return None
    return returns[0]


def _check_s10_argv(
    tree: ast.AST | None,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    argv_owner = _function(tree, "_fixed_status_argv")
    argv = _function_return_list(argv_owner)
    required = {"git", "status", "--porcelain=v2", "--branch", "-z", "--no-renames", "--no-ahead-behind"}
    values = _literal_strings(argv)
    if argv_owner is None or argv is None or values is None:
        findings.append(_violation("W13-S10", relative, "repository-state command argv is missing, dynamic, or unparsable"))
    elif not required <= set(values):
        findings.append(_violation("W13-S10", relative, "fixed repository-state no-rename status argv is incomplete", argv))
    if argv_owner is None:
        return findings
    arguments = argv_owner.args
    if arguments.posonlyargs or arguments.args or arguments.kwonlyargs or arguments.vararg or arguments.kwarg:
        findings.append(_violation("W13-S10", relative, "fixed status argv accepts user/model arguments", argv_owner))
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"append", "extend", "insert"}
        for node in _nodes(argv_owner)
    ):
        findings.append(_violation("W13-S10", relative, "fixed status argv is extended dynamically", argv_owner))
    return findings


def _check_s10_status_calls(
    tree: ast.AST | None,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    observe = _method(_class(tree, "RepositoryStateSkill"), "_observe")
    status_calls = [
        node
        for node in _nodes(observe)
        if isinstance(node, ast.Call) and _qualified_name(node.func).rsplit(".", 1)[-1] == "run_bounded_process"
    ]
    if not status_calls:
        findings.append(_violation("W13-S10", relative, "repository-state observation does not use the bounded process owner", observe))
    for call in status_calls:
        if not call.args or not isinstance(call.args[0], ast.Call) or _qualified_name(call.args[0].func).rsplit(".", 1)[-1] != "_fixed_status_argv":
            findings.append(_violation("W13-S10", relative, "repository-state observation bypasses the fixed status argv", call))
    return findings


def _check_s10_noarg_calls(
    tree: ast.AST | None,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for node in _nodes(tree):
        if isinstance(node, ast.Call) and _qualified_name(node.func).rsplit(".", 1)[-1] == "_fixed_status_argv" and (node.args or node.keywords):
            findings.append(_violation("W13-S10", relative, "fixed status argv is called with user/model arguments", node))
    return findings


def _check_s10(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/skills/repository_state.py"
    findings: list[ArchitectureViolation] = []
    tree = _required_tree(root, "W13-S10", relative, findings)
    findings.extend(_check_s10_argv(tree, relative))
    findings.extend(_check_s10_status_calls(tree, relative))
    findings.extend(_check_s10_noarg_calls(tree, relative))
    return findings

def _check_s11(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/health/online_model.py"
    tree = _required_tree(root, "W13-S11", relative, findings)
    if tree is None:
        return findings
    forbidden_prefixes = (
        "agent.application",
        "agent.orchestrator",
        "agent.orchestration",
        "agent.planning",
        "agent.runtime.model_call",
        "agent.runtime.task_runner",
        "agent.skills",
        "agent.tools",
    )
    forbidden_names = {
        "AgentApplication",
        "TaskRunner",
        "ToolInvocationGateway",
        "Planner",
        "ProjectValidator",
    }
    for node in _nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if module.startswith(forbidden_prefixes) or any(alias.name in forbidden_names for alias in node.names):
                findings.append(_violation("W13-S11", relative, "online health imports task/planner/tool runtime", node))
        elif isinstance(node, (ast.Name, ast.Attribute)):
            if _qualified_name(node).rsplit(".", 1)[-1] in forbidden_names:
                findings.append(_violation("W13-S11", relative, "online health references task/planner/tool runtime", node))
    return findings


def _has_false_default(function: ast.FunctionDef | ast.AsyncFunctionDef | None, name: str) -> bool:
    if function is None:
        return False
    positional = [*function.args.posonlyargs, *function.args.args]
    defaults = [None] * (len(positional) - len(function.args.defaults)) + list(function.args.defaults)
    for parameter, default in zip(positional, defaults, strict=True):
        if parameter.arg == name and isinstance(default, ast.Constant) and default.value is False:
            return True
    for parameter, default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True):
        if parameter.arg == name and isinstance(default, ast.Constant) and default.value is False:
            return True
    return False


def _walk_with_ancestors(node: ast.AST, ancestors: tuple[ast.AST, ...] = ()) -> Iterator[tuple[ast.AST, tuple[ast.AST, ...]]]:
    yield node, ancestors
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_ancestors(child, (*ancestors, node))


def _is_docstring_constant(node: ast.AST, ancestors: tuple[ast.AST, ...]) -> bool:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or len(ancestors) < 2:
        return False
    expression = ancestors[-1]
    owner = ancestors[-2]
    return (
        isinstance(expression, ast.Expr)
        and expression.value is node
        and isinstance(owner, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and bool(owner.body)
        and owner.body[0] is expression
    )


def _is_online_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    return any(isinstance(item, ast.Name) and item.id == "online" for item in _nodes(node.test))


def _check_s12(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    standalone_relative = "agent/health/standalone.py"
    health_relative = "agent/health_check.py"
    maintenance_relative = "agent/interfaces/cli/maintenance.py"
    standalone = _required_tree(root, "W13-S12", standalone_relative, findings)
    health = _required_tree(root, "W13-S12", health_relative, findings)
    maintenance = _required_tree(root, "W13-S12", maintenance_relative, findings)
    standalone_owner = _function(standalone, "run_standalone_health_check")
    probe_calls = [
        (node, ancestors)
        for node, ancestors in _walk_with_ancestors(standalone_owner) if isinstance(node, ast.Call)
        and _qualified_name(node.func).rsplit(".", 1)[-1] == "run_online_model_health_probe"
    ] if standalone_owner is not None else []
    if not probe_calls:
        findings.append(_violation("W13-S12", standalone_relative, "explicit online probe dispatch is missing", standalone_owner))
    for call, ancestors in probe_calls:
        if not any(_is_online_guard(ancestor) for ancestor in ancestors):
            findings.append(_violation("W13-S12", standalone_relative, "plain doctor path can call the online probe unconditionally", call))
    if not _has_false_default(_function(health, "run_health_check"), "online"):
        findings.append(_violation("W13-S12", health_relative, "health-check online mode does not default to offline", _function(health, "run_health_check")))
    if not _has_false_default(_function(maintenance, "run_doctor"), "online"):
        findings.append(_violation("W13-S12", maintenance_relative, "doctor online mode does not default to offline", _function(maintenance, "run_doctor")))
    return findings


def _production_python_paths(root: Path) -> list[Path]:
    try:
        return sorted(
            (path for path in (root / "agent").rglob("*.py") if path.is_file()),
            key=lambda path: _relative(path, root),
        )
    except OSError:
        return []


def _check_s13(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    paths = _production_python_paths(root)
    if not paths:
        return [_violation("W13-S13", "agent", "production Python owners are missing or unreadable")]
    for path in paths:
        relative = _relative(path, root)
        if relative.startswith("agent/evaluation/"):
            continue
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation("W13-S13", relative, "production owner is missing, unreadable, or unparsable"))
            continue
        for node, ancestors in _walk_with_ancestors(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and not _is_docstring_constant(node, ancestors):
                lowered = node.value.casefold()
                if "pv1-" in lowered or "practical-v1" in lowered:
                    findings.append(_violation("W13-S13", relative, "PRACTICAL-V1 fixture ID/sentinel escaped evaluation ownership", node))
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.casefold()
                if "pv1" in lowered or "practical_v1" in lowered:
                    findings.append(_violation("W13-S13", relative, "PRACTICAL-V1 fixture owner escaped evaluation ownership", node))
    return findings


def _check_s14_canonical(
    root: Path,
    relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    canonical = _required_tree(root, "W13-S14", relative, findings)
    required = {"semantic_candidate_manifest", "semantic_manifest_hash", "semantic_candidate_fingerprint", "candidate_identity"}
    defined = {
        node.name
        for node in _nodes(canonical)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not required <= defined:
        findings.append(_violation("W13-S14", relative, "canonical candidate identity owner is incomplete"))
    return findings


def _check_s14_imports(
    root: Path,
    paths: tuple[str, ...],
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    practical = _required_tree(root, "W13-S14", paths[0], findings)
    imported = _imported_names(practical, "agent.evaluation.evaluation_identity")
    practical_identity_imports = {
        "candidate_identity",
        "candidate_identity_string",
        "semantic_candidate_manifest",
        "semantic_manifest_hash",
    }
    if practical_identity_imports - imported:
        missing = practical_identity_imports - imported
        findings.append(_violation("W13-S14", paths[0], f"practical evaluation bypasses canonical identity imports: {', '.join(sorted(missing))}"))
    return findings


def _check_s14_tree(
    relative: str,
    tree: ast.AST,
    practical_relative: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for node, ancestors in _walk_with_ancestors(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.casefold()
            if (
                ("candidate" in lowered and ("identity" in lowered or "fingerprint" in lowered or "manifest" in lowered))
                or "semanticcandidate" in lowered
            ):
                findings.append(_violation("W13-S14", relative, "local candidate/semantic identity algorithm duplicates the canonical owner", node))
        if isinstance(node, ast.Call) and _qualified_name(node.func) == "hashlib.sha256":
            enclosing = next(
                (
                    item.name
                    for item in reversed(ancestors)
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                "",
            ).casefold()
            if relative == practical_relative or "candidate" in enclosing or "semantic" in enclosing or "fingerprint" in enclosing:
                findings.append(_violation("W13-S14", relative, "local SHA-256 candidate identity bypasses the canonical hash primitive", node))
    if relative == practical_relative:
        for node in _nodes(tree):
            if isinstance(node, ast.Import) and any(alias.name == "hashlib" for alias in node.names):
                findings.append(_violation("W13-S14", relative, "practical evaluation imports a local hash implementation", node))
    return findings


def _check_s14_duplicates(
    root: Path,
    paths: tuple[str, ...],
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in paths:
        tree = _required_tree(root, "W13-S14", relative, findings)
        if tree is None:
            continue
        findings.extend(_check_s14_tree(relative, tree, paths[0]))
    return findings


def _check_s14(root: Path) -> list[ArchitectureViolation]:
    canonical_relative = "agent/evaluation/evaluation_identity.py"
    paths = (
        "agent/evaluation/practical.py",
        "agent/evaluation/practical_gateway_logic.py",
        "agent/evaluation/practical_scenarios.py",
        "agent/evaluation/scripted_gateway.py",
        "agent/evaluation/runner.py",
    )
    findings = _check_s14_canonical(root, canonical_relative)
    findings.extend(_check_s14_imports(root, paths))
    findings.extend(_check_s14_duplicates(root, paths))
    return findings

def _check_s15(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    paths = _production_python_paths(root)
    if not paths:
        return [_violation("W13-S15", "agent", "production Python owners are missing or unreadable")]
    for path in paths:
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation("W13-S15", relative, "production owner is missing, unreadable, or unparsable"))
            continue
        scoped_nodes: list[ast.AST] = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        for node in scoped_nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                continue
            strings = {
                item.value.casefold()
                for item, ancestors in _walk_with_ancestors(node)
                if isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and not _is_docstring_constant(item, ancestors)
            }
            has_pytest = any("pytest" in value for value in strings)
            explicit_full_pytest = any(
                "full pytest" in value or ("full" in value and "pytest" in value)
                for value in strings
            )
            has_w13_phase_marker = any(
                any(marker in value for marker in ("w13", "wave13", "p6", "phase"))
                for value in strings
            )
            if explicit_full_pytest or (has_pytest and has_w13_phase_marker):
                findings.append(_violation("W13-S15", relative, "production module hard-codes full-pytest as a W13/phase requirement", node))
    return findings


_CHECKS: tuple[Callable[[Path], list[ArchitectureViolation]], ...] = (
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
    _check_s12,
    _check_s13,
    _check_s14,
    _check_s15,
)


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    """Return deterministic static findings for one repository root."""

    resolved = Path(root).expanduser().resolve()
    findings = [finding for check in _CHECKS for finding in check(resolved)]
    return sorted(
        findings,
        key=lambda item: (item.rule_id, item.path, item.line or 0, item.detail),
    )


find_violations = check_architecture
check_wave13_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Wave 13 static architecture boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W13 architecture checker: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
