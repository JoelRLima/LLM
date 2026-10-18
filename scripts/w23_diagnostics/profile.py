"""Standalone source-root profiler; deliberately imports no tooling scripts package."""
from __future__ import annotations

import argparse
import ast
import faulthandler
import functools
import hashlib
import importlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, cast

RULES = frozenset({
    "check", "_policy_errors", "_retired_compatibility_reference_errors", "_compatibility_projection_errors",
    "_dynamic_errors", "_legacy_layer_errors", "_neutral_shape_errors", "_application_surface_errors",
    "_model_safe_mcp_errors", "_c14_logging_boundary_errors", "_task_execution_boundary_errors",
    "_c19_evaluation_errors", "_c21_engineering_errors", "_c22_boundary_errors", "_c23_home_operation_errors",
    "_workspace_recents_consumer_errors", "_current_broker_graph_errors", "_current_owner_graph_errors",
    "_current_check_group_errors",
})


def save(path: Path, value: Any) -> None:
    pending = path.with_suffix(".pending")
    with pending.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def hashes(root: Path) -> dict[str, str]:
    result = {}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name != ".git")
        for name in [*names, *files]:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("reparse point in source")
        for name in sorted(files):
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class Probe:
    def __init__(self, module: Any, output: Path) -> None:
        self.module, self.output = module, output
        self.stack: list[str] = []
        self.parse_calls: Counter[str] = Counter()
        self.walk_calls: Counter[str] = Counter()
        self.dynamic_calls: Counter[str] = Counter()
        self.original_parse, self.original_walk = ast.parse, ast.walk
        self.original_dynamic = module._dynamic_import_calls
        self.original_rules: dict[str, Any] = {}
        self.rules: list[dict[str, Any]] = []
        self.parse_seconds = 0.0

    def event(self, value: dict[str, Any]) -> None:
        with (self.output / "rules.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
            stream.flush()
        print(value["event"], flush=True)

    def parse(self, source: Any, filename: str = "<unknown>", mode: str = "exec", **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return self.original_parse(source, filename, mode, **kwargs)
        finally:
            self.parse_calls[self.stack[-1] if self.stack else "<graph>"] += 1
            self.parse_seconds += time.perf_counter() - started

    def walk(self, node: ast.AST) -> Any:
        self.walk_calls[self.stack[-1] if self.stack else "<graph>"] += 1
        return self.original_walk(node)

    def dynamic(self, tree: ast.Module) -> Any:
        self.dynamic_calls[self.stack[-1] if self.stack else "<graph>"] += 1
        return self.original_dynamic(tree)

    def wrapper(self, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            wall, cpu, parses = time.perf_counter(), time.process_time(), sum(self.parse_calls.values())
            self.stack.append(name)
            self.event({"event": "enter", "rule": name, "pid": os.getpid()})
            try:
                return function(*args, **kwargs)
            finally:
                self.stack.pop()
                row = {"event": "exit", "rule": name, "wall": time.perf_counter() - wall,
                       "cpu": time.process_time() - cpu, "ast_parse_calls": sum(self.parse_calls.values()) - parses,
                       "inclusive": True}
                self.rules.append(row)
                self.event(row)
        return wrapped

    def install(self) -> None:
        # Match ast.parse's version-dependent overloads only for this temporary patch.
        ast.parse = cast(Any, self.parse)
        ast.walk = self.walk
        self.module._dynamic_import_calls = self.dynamic
        for name in RULES:
            function = getattr(self.module, name)
            self.original_rules[name] = function
            setattr(self.module, name, self.wrapper(name, function))

    def restore(self) -> None:
        ast.parse, ast.walk = self.original_parse, self.original_walk
        self.module._dynamic_import_calls = self.original_dynamic
        for name, function in self.original_rules.items():
            setattr(self.module, name, function)


def load_checker(root: Path) -> Any:
    if "scripts" in sys.modules:
        raise RuntimeError("source scripts package was already loaded")
    sys.path[:0] = [str(root), str(root / "src")]
    module = importlib.import_module("scripts.check_current_architecture")
    module_file = module.__file__
    if module_file is None:
        raise RuntimeError("checker module has no source file")
    if Path(module_file).resolve() != root / "scripts/check_current_architecture.py" or module.ROOT != root:
        raise RuntimeError("checker loaded from the wrong source tree")
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.source.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"status": "UNKNOWN", "python": sys.version, "pid": os.getpid(),
                              "requested_python": "3.10.11", "actual_python": list(sys.version_info[:3])}
    save(args.output / "result.json", result)
    if os.name != "nt" or sys.version_info[:3] != (3, 10, 11):
        return 1
    before = hashes(root)
    save(args.output / "before.json", before)
    probe: Probe | None = None
    try:
        module = load_checker(root)
        probe = Probe(module, args.output)
        probe.install()
        faulthandler.dump_traceback_later(60, repeat=True)
        wall, cpu = time.perf_counter(), time.process_time()
        errors, counts = module.check(root)
        result.update(wall=time.perf_counter() - wall, cpu=time.process_time() - cpu,
                      errors=[error.replace(str(root), "<SOURCE>") for error in errors], counts=counts,
                      rules=probe.rules, ast_parse_calls=sum(probe.parse_calls.values()),
                      ast_walk_calls_by_rule=dict(probe.walk_calls), ast_parse_calls_by_rule=dict(probe.parse_calls),
                      dynamic_import_analyses_by_rule=dict(probe.dynamic_calls), parse_seconds=probe.parse_seconds)
        result["status"] = "FAIL" if errors else "PASS"
    except BaseException as exc:
        result.update(status="UNKNOWN", exception_type=type(exc).__name__)
    finally:
        faulthandler.cancel_dump_traceback_later()
        if probe is not None:
            probe.restore()
        after = hashes(root)
        save(args.output / "after.json", after)
        result["inputs_unchanged"] = before == after
        if before != after:
            result["status"] = "FAIL"
        save(args.output / "result.json", result)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
