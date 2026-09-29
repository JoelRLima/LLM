"""Build a deterministic migration-surface inventory from a Git baseline.

The report records paths, symbols, and line numbers rather than source excerpts,
so it remains useful without copying prompts or credentials into local evidence.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BASELINE = "09cefa483f83cd56e01aa187649dca1ec1cb5a6c"
W21_AUTHORITY_ROOT = Path(".agent-local/wave21/authority")
FROZEN_W21_HASHES = {
    "architecture/target-architecture.json": "6fd6dea242455d8b173ba9fcd88d468c880a736d5ee4c02cb16cb71e41e95fdb",
    "architecture/compatibility-bridges.json": "5a5e52af7c3200ac0399771d4fc5739b02587377c0f545c51d829b5769efe274",
    "architecture/transition-violations.json": "d09e71ab2e75514351491e7ab8e2c16ab5e9983b06009ae91021e4d60fa9458d",
    "characterization/CHAR-PATH-001.json": "ad100adc710cfa3b58a55b8dca66d59c9d39aa4313a5f7b87ae263e27e3923e3",
    "discovery/current-dependency-graph.json": "3e04ec2f40a1a8bd7e4a176b2652dcdf60d80325cc1bcdee8a0465e1151b7b14",
}
AGENT_TOKEN = re.compile(r"\bagent(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
DURABLE_WORDS = re.compile(
    r"checkpoint|persist|durable|state|sqlite|database|qualified|module.path|"
    r"class.path|type.path|schema|serialization|deserialize|import.path",
    re.IGNORECASE,
)
PROCESS_CALLS = {
    "subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell", "os.system", "multiprocessing.Process",
}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".txt", ".ps1", ".yml", ".yaml", ".cfg", ".ini"}
VERSION_REFERENCE = re.compile(r"agent\._version|agent\.__version__|from\s+agent\s+import\s+__version__")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args), check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def _baseline_blobs(root: Path, commit: str, paths: list[str]) -> dict[str, str]:
    requests = "".join(f"{commit}:{path}\n" for path in paths)
    result = subprocess.run(
        ("git", "-C", str(root), "cat-file", "--batch"), check=True,
        capture_output=True, input=requests.encode("utf-8"),
    )
    output = result.stdout
    position = 0
    blobs: dict[str, str] = {}
    for path in paths:
        header_end = output.find(b"\n", position)
        if header_end < 0:
            raise ValueError(f"incomplete Git object response for {path}")
        header = output[position:header_end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"baseline object is not a blob: {path}")
        size = int(header[2])
        start = header_end + 1
        end = start + size
        blobs[path] = output[start:end].decode("utf-8")
        if output[end : end + 1] != b"\n":
            raise ValueError(f"malformed Git object response for {path}")
        position = end + 1
    return blobs


def _module_name(path: str) -> str:
    value = path[:-3].replace("/", ".")
    return value[:-9] if value.endswith(".__init__") else value


def _package_name(path: str) -> str:
    parts = path[:-3].replace("/", ".").split(".")
    if parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return ".".join(parts)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _import_target(path: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = _package_name(path).split(".") if _package_name(path) else []
    if node.level > 1:
        parts = parts[: max(0, len(parts) - node.level + 1)]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _historical_path(path: str) -> bool:
    lowered = path.casefold()
    return (
        lowered.startswith(("docs/", "examples/", ".github/"))
        or "compatibility_ledger" in lowered
        or "/history/" in lowered
        or "/archive/" in lowered
    )


def _disposition(path: str) -> str:
    if path.startswith("agent/tools/stdio_"):
        return "long-lived stdio/session protocol; preserve session ownership"
    if path == "agent/process/tree.py":
        return "generic process lifecycle/termination candidate"
    if path.startswith("agent/skills/"):
        return "Agent-authorized operation; separate policy from process mechanics"
    if path.startswith("agent/code/"):
        return "Agent validation semantics; separate process mechanics"
    if path.startswith("agent/application_services/"):
        return "product/application query candidate; verify authority and ownership"
    if path.startswith("agent/evaluation/"):
        return "Agent evaluation semantics; keep subprocess use bounded"
    if path.startswith("agent/engineering/"):
        return "Agent engineering semantics; inspect backend operation"
    return "production launcher requires explicit owner review"


def _old_root_module(module: str) -> bool:
    return module == "agent" or module.startswith("agent.")


def _record_import_node(path: str, source_module: str, node: ast.Import, data: dict[str, Any]) -> None:
    for alias in node.names:
        module = alias.name
        if _old_root_module(module):
            data["imports"].append({"path": path, "line": node.lineno, "module": module})
        if path.startswith("agent/"):
            data["module_edges"].append((source_module, module))


def _record_import_from_node(
    path: str,
    source_module: str,
    node: ast.ImportFrom,
    data: dict[str, Any],
) -> None:
    module = _import_target(path, node)
    if node.level == 0 and _old_root_module(module):
        data["imports"].append({"path": path, "line": node.lineno, "module": module})
    if path.startswith("agent/") and module:
        data["module_edges"].append((source_module, module))
    if (
        path.startswith("agent/")
        and not path.startswith("agent/interfaces/")
        and (module == "agent.interfaces" or module.startswith("agent.interfaces."))
    ):
        data["interfaces_inward"].append({"path": path, "line": node.lineno, "module": module})
    if node.level:
        data["relative_imports"].append({
            "path": path,
            "line": node.lineno,
            "level": node.level,
            "resolved_module": module,
        })


def _record_call_node(path: str, node: ast.Call, data: dict[str, Any]) -> None:
    name = _dotted(node.func)
    if name in {"importlib.import_module", "__import__", "import_module"}:
        literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        data["dynamic"].append({"path": path, "line": node.lineno, "call": name, "literal": literal})
    if name in PROCESS_CALLS or name.startswith(("os.exec", "os.spawn")):
        if path.startswith("agent/"):
            data["launches"].append({
                "path": path,
                "line": node.lineno,
                "call": name,
                "disposition": _disposition(path),
            })


def _collect_ast_inventory(py_paths: list[str], blobs: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "imports": [], "dynamic": [], "interfaces_inward": [], "launches": [],
        "module_edges": [], "relative_imports": [], "parse_failures": [],
    }
    for path in py_paths:
        try:
            tree = ast.parse(blobs[path], filename=path)
        except SyntaxError:
            data["parse_failures"].append(path)
            continue
        source_module = _module_name(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                _record_import_node(path, source_module, node, data)
            elif isinstance(node, ast.ImportFrom):
                _record_import_from_node(path, source_module, node, data)
            elif isinstance(node, ast.Call):
                _record_call_node(path, node, data)
    return data


def _record_text_line(path: str, number: int, line: str, data: dict[str, Any]) -> None:
    tokens = sorted(set(AGENT_TOKEN.findall(line)))
    for token in tokens:
        record = {"path": path, "line": number, "token": token}
        if _historical_path(path):
            data["historical_refs"].append(record)
        elif path.startswith("tests/"):
            data["test_refs"].append(record)
        else:
            data["namespace_refs"].append(record)
        if DURABLE_WORDS.search(line):
            data["durable_refs"].append(record)
    if VERSION_REFERENCE.search(line):
        data["version_refs"].append({"path": path, "line": number})
    if re.search(r"importlib\.resources|pkg_resources|agent\.resources|default_config\.json", line):
        data["resource_strings"].append({"path": path, "line": number, "kind": "resource API or path"})
    if _w21_path_assumption(path, line):
        data["w21_assumptions"].append({"path": path, "line": number, "assumption": line.strip()[:140]})


def _w21_path_assumption(path: str, line: str) -> bool:
    checker = path.startswith("scripts/w21_architecture/") or path in {
        "scripts/check_wave21_architecture.py", "scripts/check_wave21_projection_equivalence.py",
        "scripts/check_w21_compatibility.py", "scripts/check_w21_runtime_identity.py",
        "scripts/check_w21_scope.py",
    }
    return checker and bool(re.search(r"Path\(__file__\)|parents\[|agent/|agent\\|startswith\(['\"]agent", line))


def _collect_text_inventory(text_paths: list[str], blobs: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "namespace_refs": [], "test_refs": [], "durable_refs": [], "historical_refs": [],
        "version_refs": [], "resource_strings": [], "w21_assumptions": [],
    }
    for path in text_paths:
        for number, line in enumerate(blobs[path].splitlines(), start=1):
            _record_text_line(path, number, line, data)
    return data


def _candidate_groups(production: list[str]) -> dict[str, list[str]]:
    runtime_groups = {
        "runtime_mixed": [path for path in production if path.startswith("agent/runtime/")],
        "process_lifecycle": [path for path in production if path.startswith("agent/process/")],
        "cancellation_owner": [path for path in production if path in {"agent/cancellation.py", "agent/cancellation/__init__.py"}],
        "filesystem_primitives": [path for path in production if path.endswith(("filesystem_primitives.py", "path_safety.py"))],
        "storage_primitives": [path for path in production if re.search(r"storage|state_migration|file_lock", path)],
    }
    service_groups = {
        "resources_split_question": [path for path in production if path.startswith("agent/resources/")],
        "agent_application_default_owner": [path for path in production if re.fullmatch(r"agent/application[^/]*\.py", path)],
        "application_services_candidate": [path for path in production if path.startswith("agent/application_services/")],
        "agent_tools_mixed": [path for path in production if path.startswith("agent/tools/")],
        "extension_transport_and_registry": [path for path in production if path.startswith(("agent/tools/stdio", "agent/tools/extension"))],
        "health_lifecycle": [path for path in production if path.startswith("agent/health/")],
        "interface_leaf_boundary": [path for path in production if path.startswith("agent/interfaces/")],
        "interface_task_directives": [path for path in production if path == "agent/interfaces/task_directives.py"],
    }
    return {**runtime_groups, **service_groups}


def _candidate_connectivity(
    module_edges: list[tuple[str, str]], candidate_groups: dict[str, list[str]]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    inbound: dict[str, set[str]] = defaultdict(set)
    outbound: dict[str, set[str]] = defaultdict(set)
    module_groups = {
        name: [_module_name(member) for member in members]
        for name, members in candidate_groups.items()
    }
    for source, destination in module_edges:
        for group, member_modules in module_groups.items():
            if any(destination == member or destination.startswith(member + ".") for member in member_modules):
                inbound[group].add(source)
            if any(source == member or source.startswith(member + ".") for member in member_modules):
                outbound[group].add(destination)
    return inbound, outbound


def _path_partitions(paths: list[str]) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    text_paths = [path for path in paths if Path(path).suffix.lower() in TEXT_SUFFIXES]
    py_paths = [path for path in paths if path.endswith(".py")]
    production = [path for path in py_paths if path.startswith("agent/")]
    tests = [path for path in py_paths if path.startswith("tests/")]
    scripts = [path for path in py_paths if path.startswith("scripts/")]
    return text_paths, py_paths, production, tests, scripts


def _resource_records(paths: list[str]) -> list[dict[str, str]]:
    return [
        {"path": path, "kind": "package data"}
        for path in paths
        if path.startswith("agent/resources/") and path.endswith(".json")
    ]


def _packaging_records(paths: list[str], blobs: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    pyproject = blobs["pyproject.toml"].splitlines()
    packaging_keys = (
        "pythonpath", "testpaths", "include =", "dynamic =", "version = {attr",
        "package-data", "llm-agent =", "files = [", '"agent',
    )
    packaging = [
        {"line": number, "text": line.strip()}
        for number, line in enumerate(pyproject, start=1)
        if any(key in line for key in packaging_keys)
    ]
    architecture_paths = [
        path for path in paths
        if path.startswith("scripts/") and re.search(r"wave(18|19|20|21)|w21|architecture", path, re.I)
    ]
    return packaging, architecture_paths


def _authority_records(root: Path) -> list[dict[str, str]]:
    records = []
    for relative, expected in sorted(FROZEN_W21_HASHES.items()):
        authority_file = root / W21_AUTHORITY_ROOT / relative
        digest = hashlib.sha256(authority_file.read_bytes()).hexdigest() if authority_file.is_file() else "MISSING"
        records.append({
            "path": (W21_AUTHORITY_ROOT / relative).as_posix(), "sha256": digest, "expected": expected,
        })
    return records


def build_inventory(root: Path, baseline: str = BASELINE) -> dict[str, Any]:
    root = root.resolve()
    head = _git(root, "rev-parse", baseline)
    paths = sorted(filter(None, _git(root, "ls-tree", "-r", "--name-only", head).splitlines()))
    text_paths, py_paths, production, tests, scripts = _path_partitions(paths)
    blobs = _baseline_blobs(root, head, text_paths)

    ast_data = _collect_ast_inventory(py_paths, blobs)
    text_data = _collect_text_inventory(text_paths, blobs)
    imports = ast_data["imports"]
    dynamic = ast_data["dynamic"]
    interfaces_inward = ast_data["interfaces_inward"]
    launches = ast_data["launches"]
    relative_imports = ast_data["relative_imports"]
    parse_failures = ast_data["parse_failures"]
    namespace_refs = text_data["namespace_refs"]
    test_refs = text_data["test_refs"]
    durable_refs = text_data["durable_refs"]
    historical_refs = text_data["historical_refs"]
    version_refs = text_data["version_refs"]
    resource_strings = text_data["resource_strings"]
    w21_assumptions = text_data["w21_assumptions"]
    resources = _resource_records(paths)
    packaging, architecture_paths = _packaging_records(paths, blobs)

    policy = json.loads(blobs["quality/architecture_policy.json"])
    compatibility = json.loads(blobs["quality/architecture_compatibility.json"])
    baseline_doc = json.loads(blobs["quality/architecture_baseline.json"])
    sys.path.insert(0, str(root))
    from scripts.w21_architecture import RepositorySource, build_graph

    current_graph = build_graph(RepositorySource(root))
    current_graph_signatures = current_graph.signatures()
    module_edges = [
        (str(edge["source_module"]), str(edge["destination_module"]))
        for edge in current_graph.architecture_union_edges
    ]
    w21_authority_files = _authority_records(root)

    candidate_groups = _candidate_groups(production)
    inbound, outbound = _candidate_connectivity(module_edges, candidate_groups)

    return {
        "schema_version": 1,
        "baseline": {"commit": head, "branch_at_capture": _git(root, "branch", "--show-current")},
        "counts": {
            "tracked_files": len(paths), "production_python_modules": len(production),
            "test_python_modules": len(tests), "script_python_modules": len(scripts),
            "top_level_tracked_roots": sorted({path.split("/", 1)[0] for path in paths}),
            "python_parse_failures": sorted(parse_failures),
        },
        "packaging_and_tooling": {
            "pyproject_bindings": packaging, "architecture_checker_paths": architecture_paths,
            "w21_source_root_assumptions": w21_assumptions,
            "w21_policy_rule_count": len(policy.get("rules", [])),
            "w21_compatibility_bridge_count": len(compatibility.get("bridges", [])),
            "w21_adapter_edge_count": len(compatibility.get("adapter_edges", [])),
            "w21_baseline_module_count": baseline_doc.get("graph_signatures", {}).get("production_module_count"),
            "w21_frozen_cross_package_pairs": baseline_doc.get("baseline_cross_package_pair_count"),
            "w21_frozen_transition_violations": baseline_doc.get("frozen_transition_violation_count"),
        },
        "LIVE_NAMESPACE_SURFACES": {
            "absolute_imports": sorted(imports, key=lambda x: (x["path"], x["line"], x["module"])),
            "production_absolute_imports": sorted(
                [item for item in imports if item["path"].startswith("agent/")],
                key=lambda x: (x["path"], x["line"], x["module"]),
            ),
            "installed_verifier_agent_imports": sorted(
                [item for item in imports if item["path"].startswith(("scripts/", "installer/"))],
                key=lambda x: (x["path"], x["line"], x["module"]),
            ),
            "relative_imports": sorted(relative_imports, key=lambda x: (x["path"], x["line"])),
            "dynamic_import_calls": sorted(dynamic, key=lambda x: (x["path"], x["line"])),
            "w21_literal_dynamic_edges": list(current_graph.literal_dynamic_edges),
            "w21_declarative_module_edges": list(current_graph.declarative_edges),
            "executable_agent_token_references": sorted(namespace_refs, key=lambda x: (x["path"], x["line"], x["token"])),
            "test_agent_token_references": sorted(test_refs, key=lambda x: (x["path"], x["line"], x["token"])),
        },
        "HISTORICAL_OR_DOCUMENTARY_AGENT_STRINGS": sorted(historical_refs, key=lambda x: (x["path"], x["line"], x["token"])),
        "PERSISTED_OR_DURABLE_IDENTIFIERS": sorted(durable_refs, key=lambda x: (x["path"], x["line"], x["token"])),
        "DIRECT_PROCESS_LAUNCHERS": sorted(launches, key=lambda x: (x["path"], x["line"])),
        "INTERFACE_INWARD_IMPORTS": sorted(interfaces_inward, key=lambda x: (x["path"], x["line"], x["module"])),
        "W21_RULE_AND_BRIDGE_AUTHORITY": {
            "frozen_authority_baseline": baseline_doc.get("baseline"),
            "frozen_authority_hashes": w21_authority_files,
            "observed_worktree_graph_signatures": current_graph_signatures,
            "policy_rule_ids": [item.get("rule_id") for item in policy.get("rules", [])],
            "compatibility_bridge_ids": [item.get("bridge_id") for item in compatibility.get("bridges", [])],
            "adapter_edge_ids": [item.get("adapter_id") for item in compatibility.get("adapter_edges", [])],
            "baseline_projection_sha256": hashlib.sha256(
                blobs["quality/architecture_baseline.json"].encode("utf-8")
            ).hexdigest(),
        },
        "MIXED_OWNER_CANDIDATES": {
            name: {
                "modules": sorted(members), "module_count": len(members),
                "distinct_inbound_modules": len(inbound[name]), "distinct_outbound_modules": len(outbound[name]),
                "inbound_modules": sorted(inbound[name]), "outbound_modules": sorted(outbound[name]),
            }
            for name, members in candidate_groups.items()
        },
        "resource_ownership": {
            "package_data_files": resources, "resource_loading_surfaces": resource_strings,
            "semantic_resource_owner": "agent.resources.contracts (logical authorization/resource semantics)",
        },
        "product_version_ownership": {
            "current_source_owner": "llm_agent._version.VERSION",
            "version_reference_sites": version_refs,
            "installed_product_verifiers": [
                path for path in paths if path in {
                    "scripts/verify_installed_package.py", "scripts/verify_installed_product.py", "installer/install.ps1",
                }
            ],
        },
        "agent_application_ownership": {
            "existing_agent_application_modules_remain_agent_owned": candidate_groups["agent_application_default_owner"],
            "product_application_owner_is_a_separate_future_surface": "product-level application composition root",
        },
        "w21_source_root_assumptions": w21_assumptions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()
    rendered = json.dumps(build_inventory(args.root, args.baseline), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        destination = args.output if args.output.is_absolute() else args.root / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
