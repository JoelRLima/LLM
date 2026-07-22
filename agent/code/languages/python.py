from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from agent.code.contracts import (
    AnalysisLevel,
    CodeAnalysis,
    Diagnostic,
    DiagnosticSeverity,
    ImportEdge,
    Symbol,
)

_DANGEROUS_CALLS = {
    "eval": "PYSEC001",
    "exec": "PYSEC002",
    "compile": "PYSEC003",
}


def _call_name(node: ast.Call) -> Optional[str]:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.scope: list[str] = []
        self.symbols: list[Symbol] = []
        self.imports: list[ImportEdge] = []
        self.diagnostics: list[Diagnostic] = []

    def _qualified(self, name: str) -> str:
        return ".".join((*self.scope, name))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=self._qualified(node.name),
                kind="class",
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=self._qualified(node.name),
                kind="method" if self.scope else "function",
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=_signature(node),
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(ImportEdge(self.file_path, alias.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target = "." * node.level + (node.module or "")
        self.imports.append(ImportEdge(self.file_path, target, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name in _DANGEROUS_CALLS:
            self.diagnostics.append(
                Diagnostic(
                    code=_DANGEROUS_CALLS[name],
                    message=f"Uso de '{name}' exige revisão de entrada não confiável.",
                    severity=DiagnosticSeverity.SECURITY,
                    file_path=self.file_path,
                    line=node.lineno,
                    column=node.col_offset,
                    source="python_ast",
                )
            )
        self.generic_visit(node)


class PythonLanguageAdapter:
    name = "python"
    extensions = frozenset({".py", ".pyi"})

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    def analyze(self, path: Path, relative_path: str, source: str, content_hash: str) -> CodeAnalysis:
        del path
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            diagnostic = Diagnostic(
                code="PYSYNTAX",
                message=exc.msg,
                severity=DiagnosticSeverity.ERROR,
                file_path=relative_path,
                line=exc.lineno or 1,
                column=exc.offset or 0,
                source="python_ast",
            )
            return CodeAnalysis(
                file_path=relative_path,
                language=self.name,
                level=AnalysisLevel.SYNTACTIC,
                confidence=1.0,
                content_hash=content_hash,
                diagnostics=(diagnostic,),
            )
        visitor = _SymbolVisitor(relative_path)
        visitor.visit(tree)
        return CodeAnalysis(
            file_path=relative_path,
            language=self.name,
            level=AnalysisLevel.SEMANTIC,
            confidence=0.95,
            content_hash=content_hash,
            symbols=tuple(visitor.symbols),
            imports=tuple(visitor.imports),
            diagnostics=tuple(visitor.diagnostics),
        )
