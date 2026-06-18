import ast
import os
from pathlib import Path
from .base import BaseSkill

class CodeAnalyzerSkill(BaseSkill):
    name = "code_analyzer"
    description = "Analisa arquivos Python e gera mapa estrutural com dependências de chamadas. Suporta modo compacto para visão geral."

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self):
        return {
            "target": {
                "type": "string",
                "description": "Caminho relativo do arquivo ou diretório a ser analisado."
            },
            "mode": {
                "type": "string",
                "description": "'file' para um único arquivo, 'directory' para um diretório inteiro. Padrão: 'file'."
            },
            "include_code": {
                "type": "boolean",
                "description": "Se true, inclui o código fonte completo de cada função/método. Padrão: false."
            },
            "compact": {
                "type": "boolean",
                "description": "Se true, retorna apenas nomes de classes e funções (sem detalhes, imports, docstrings). Ideal para visão geral de diretórios grandes. Padrão: false."
            }
        }

    def execute(self, args: dict) -> dict:
        target = args.get("target", "")
        mode = args.get("mode", "file")
        include_code = args.get("include_code", False)
        compact = args.get("compact", False)

        if not target:
            return {"ok": False, "done": True, "error": "alvo vazio", "message": "Nenhum caminho fornecido."}

        try:
            requested = (self.base_dir / target).resolve()
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": f"Caminho inválido: {target}"}

        if not str(requested).startswith(str(self.base_dir)):
            return {"ok": False, "done": True, "error": "acesso negado", "message": f"Fora do diretório seguro: {target}"}

        if mode == "file":
            return self._analyze_file(requested, include_code, compact)
        elif mode == "directory":
            return self._analyze_directory(requested, include_code, compact)
        else:
            return {"ok": False, "done": True, "error": "modo inválido", "message": "Use 'file' ou 'directory'."}

    def _analyze_file(self, file_path: Path, include_code: bool = False, compact: bool = False) -> dict:
        if not file_path.is_file():
            return {"ok": False, "done": True, "error": "não é arquivo", "message": f"'{file_path}' não é um arquivo."}
        if file_path.suffix != ".py":
            return {"ok": False, "done": True, "error": "tipo não suportado", "message": "Apenas arquivos .py são analisados."}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except SyntaxError as e:
            return {"ok": False, "done": True, "error": str(e), "message": f"Erro de sintaxe no arquivo."}
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao ler/parsear o arquivo."}

        # Modo compacto: apenas nomes de classes e funções
        if compact:
            functions = []
            classes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    functions.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    methods = [item.name for item in node.body if isinstance(item, ast.FunctionDef)]
                    classes.append({"name": node.name, "methods": methods})
            return {
                "ok": True,
                "done": True,
                "data": {
                    "file": str(file_path.relative_to(self.base_dir)),
                    "classes": classes,
                    "functions": functions
                },
                "error": None,
                "message": f"{len(functions)} funções, {len(classes)} classes (modo compacto)."
            }

        # Modo completo (código existente)
        imports = []
        functions = []
        classes = []
        calls = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}" if module else alias.name)
            elif isinstance(node, ast.FunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "args": [arg.arg for arg in node.args.args],
                    "docstring": ast.get_docstring(node) or "",
                    "calls": []
                }
                functions.append(func_info)
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.append({
                            "name": item.name,
                            "line": item.lineno,
                            "end_line": item.end_lineno,
                            "args": [arg.arg for arg in item.args.args],
                            "docstring": ast.get_docstring(item) or ""
                        })
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "methods": methods,
                    "docstring": ast.get_docstring(node) or ""
                })

        class CallVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_function = None
                self.calls = {}

            def visit_FunctionDef(self, node):
                old = self.current_function
                self.current_function = node.name
                if self.current_function not in self.calls:
                    self.calls[self.current_function] = []
                self.generic_visit(node)
                self.current_function = old

            def visit_Call(self, node):
                if self.current_function:
                    if isinstance(node.func, ast.Name):
                        called = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        called = node.func.attr
                    else:
                        called = None
                    if called:
                        self.calls[self.current_function].append(called)
                self.generic_visit(node)

        visitor = CallVisitor()
        visitor.visit(tree)

        for func in functions:
            func["calls"] = list(set(visitor.calls.get(func["name"], [])))

        if include_code:
            source_lines = source.splitlines(keepends=True)
            for func in functions:
                if "end_line" in func:
                    func["source"] = "".join(source_lines[func["line"]-1:func["end_line"]])
            for cls in classes:
                if "end_line" in cls:
                    cls["source"] = "".join(source_lines[cls["line"]-1:cls["end_line"]])
                for method in cls["methods"]:
                    if "end_line" in method:
                        method["source"] = "".join(source_lines[method["line"]-1:method["end_line"]])

        return {
            "ok": True,
            "done": True,
            "data": {
                "file": str(file_path.relative_to(self.base_dir)),
                "imports": imports,
                "functions": functions,
                "classes": classes,
                "call_graph": visitor.calls
            },
            "error": None,
            "message": f"Analisado: {len(functions)} funções, {len(classes)} classes, {len(imports)} imports."
        }

    def _analyze_directory(self, dir_path: Path, include_code: bool = False, compact: bool = False) -> dict:
        if not dir_path.is_dir():
            return {"ok": False, "done": True, "error": "não é diretório", "message": f"'{dir_path}' não é um diretório."}

        project_map = {}
        dependencies = {}
        total_files = 0

        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "venv", "env", "node_modules", "build", "dist")]
            
            for file in files:
                if file.endswith(".py"):
                    file_path = Path(root) / file
                    rel_path = str(file_path.relative_to(self.base_dir))
                    result = self._analyze_file(file_path, include_code=include_code, compact=compact)
                    if result["ok"]:
                        project_map[rel_path] = result["data"]
                        # Só coleta dependências se não for modo compacto (para manter leve)
                        if not compact:
                            for imp in result["data"].get("imports", []):
                                base = imp.split(".")[0]
                                if base not in dependencies:
                                    dependencies[base] = []
                                dependencies[base].append(rel_path)
                        total_files += 1

        response = {
            "ok": True,
            "done": True,
            "data": {
                "files": project_map,
                "total_files": total_files
            },
            "error": None,
            "message": f"Mapa gerado com {total_files} arquivos (modo {'compacto' if compact else 'completo'})."
        }

        if not compact:
            response["data"]["dependencies"] = dependencies
            response["message"] += f" e {len(dependencies)} módulos dependentes."

        return response