"""Observable security-fact extraction for Python source files."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

from .python_source_analysis import CallGraphVisitor
from .security_symbols import symbols_for

MAX_SNIPPET = 120
STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", sys.builtin_module_names))


def dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


class SecurityFactVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str]) -> None:
        self.source_lines = source_lines
        self.imports: dict[str, list[str]] = {"standard": [], "third_party": [], "local": []}
        self.decorators: list[dict[str, Any]] = []
        self.user_controlled_sources: list[dict[str, Any]] = []
        self.interesting_calls: list[dict[str, Any]] = []
        self.filesystem_access: list[dict[str, Any]] = []
        self.network_calls: list[dict[str, Any]] = []
        self.crypto_usage: list[dict[str, Any]] = []
        self.auth_usage: list[dict[str, Any]] = []
        self.functions_defined: list[str] = []
        self.classes_defined: list[str] = []
        self.source_symbols = symbols_for("source")
        self.execution_symbols = symbols_for("execution")
        self.category_targets = {
            "filesystem": (symbols_for("filesystem"), self.filesystem_access),
            "network": (symbols_for("network"), self.network_calls),
            "crypto": (symbols_for("crypto"), self.crypto_usage),
            "auth": (symbols_for("auth"), self.auth_usage),
        }

    def _snippet(self, line_number: int) -> str:
        if not 1 <= line_number <= len(self.source_lines):
            return ""
        line = self.source_lines[line_number - 1].strip()
        return line if len(line) <= MAX_SNIPPET else line[: MAX_SNIPPET - 3] + "..."

    def _record_decorators(self, node: ast.FunctionDef | ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = dotted_name(target) or "<desconhecido>"
            fact = {"name": name, "line": decorator.lineno, "snippet": self._snippet(decorator.lineno)}
            self.decorators.append(fact)
            if any(marker in name.lower() for marker in ("login_required", "auth", "permission")):
                self.auth_usage.append({**fact, "type": "decorator", "symbol": name})

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bucket = "standard" if alias.name.split(".")[0] in STDLIB_MODULES else "third_party"
            self.imports[bucket].append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if node.level:
                self.imports["local"].append(f"{'.' * node.level}{module + '.' if module else ''}{alias.name}")
                continue
            full_name = f"{module}.{alias.name}" if module else alias.name
            bucket = "standard" if module.split(".")[0] in STDLIB_MODULES else "third_party"
            self.imports[bucket].append(full_name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions_defined.append(node.name)
        self._record_decorators(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes_defined.append(node.name)
        self._record_decorators(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        symbol = dotted_name(node)
        if symbol in self.source_symbols:
            self.user_controlled_sources.append({
                "type": "attribute",
                "line": node.lineno,
                "symbol": symbol,
                "snippet": self._snippet(node.lineno),
            })
        self.generic_visit(node)

    @staticmethod
    def _execution_args(node: ast.Call) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
                extra["shell"] = keyword.value.value
            elif keyword.arg == "Loader" and isinstance(keyword.value, ast.Attribute):
                extra["loader"] = dotted_name(keyword.value)
        return extra

    def _record_call_categories(self, symbol: str, line: int, snippet: str) -> None:
        for category, (symbols, destination) in self.category_targets.items():
            if symbol in symbols:
                destination.append({"type": category, "line": line, "symbol": symbol, "snippet": snippet})

    def visit_Call(self, node: ast.Call) -> None:
        symbol = dotted_name(node.func)
        if symbol is None:
            self.generic_visit(node)
            return
        line = node.lineno
        snippet = self._snippet(line)
        if symbol in self.source_symbols:
            self.user_controlled_sources.append({"type": "call", "line": line, "symbol": symbol, "snippet": snippet})
        if symbol in self.execution_symbols:
            self.interesting_calls.append({
                "type": "execution",
                "line": line,
                "symbol": symbol,
                "snippet": snippet,
                "extra_args": self._execution_args(node),
            })
        self._record_call_categories(symbol, line, snippet)
        self.generic_visit(node)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imports": self.imports,
            "decorators": self.decorators,
            "user_controlled_sources": self.user_controlled_sources,
            "interesting_calls": self.interesting_calls,
            "filesystem_access": self.filesystem_access,
            "network_calls": self.network_calls,
            "crypto_usage": self.crypto_usage,
            "auth_usage": self.auth_usage,
            "functions_defined": self.functions_defined,
            "classes_defined": self.classes_defined,
        }


def analyze_security_file(file_path: Path, base_dir: Path) -> dict[str, Any]:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    visitor = SecurityFactVisitor(source.splitlines())
    visitor.visit(tree)
    call_visitor = CallGraphVisitor()
    call_visitor.visit(tree)
    data = visitor.to_dict()
    data["file"] = str(file_path.relative_to(base_dir))
    data["calls"] = {name: sorted(set(callees)) for name, callees in call_visitor.calls.items()}
    fact_keys = (
        "interesting_calls", "user_controlled_sources", "filesystem_access",
        "network_calls", "crypto_usage", "auth_usage",
    )
    data["total_facts"] = sum(len(data[key]) for key in fact_keys)
    return data
