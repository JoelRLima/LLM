# agent/skills/code_analyzer.py
import ast
import os
from pathlib import Path
from .base import BaseSkill

class CodeAnalyzerSkill(BaseSkill):
    name = "code_analyzer"
    description = "Analisa arquivos Python e gera mapa estrutural com dependências de chamadas."

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir).resolve()

    def get_schema(self):
        return {
            "target": {
                "type": "string",
                "description": "Caminho relativo do arquivo ou diretório."
            },
            "mode": {
                "type": "string",
                "description": "'file' para um único arquivo, 'directory' para diretório inteiro."
            }
        }

    def execute(self, args: dict) -> dict:
        target = args.get("target", "")
        mode = args.get("mode", "file")
        if not target:
            return {"ok": False, "done": True, "error": "alvo vazio", "message": "Nenhum caminho fornecido."}
        try:
            requested = (self.base_dir / target).resolve()
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": f"Caminho inválido: {target}"}
        if not str(requested).startswith(str(self.base_dir)):
            return {"ok": False, "done": True, "error": "acesso negado", "message": f"Fora do diretório seguro: {target}"}
        if mode == "file":
            return self._analyze_file(requested)
        elif mode == "directory":
            return self._analyze_directory(requested)
        else:
            return {"ok": False, "done": True, "error": "modo inválido", "message": "Use 'file' ou 'directory'."}

    def _analyze_file(self, file_path: Path) -> dict:
        if not file_path.is_file():
            return {"ok": False, "done": True, "error": "não é arquivo", "message": f"'{file_path}' não é um arquivo."}
        if file_path.suffix != ".py":
            return {"ok": False, "done": True, "error": "tipo não suportado", "message": "Apenas arquivos .py."}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao parsear."}

        imports = []
        functions = []
        classes = []
        # Mapa de chamadas: nome_da_função -> [funções chamadas]
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
                    "args": [arg.arg for arg in node.args.args],
                    "docstring": ast.get_docstring(node) or "",
                    "calls": []  # será preenchido depois
                }
                functions.append(func_info)
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.append({
                            "name": item.name,
                            "line": item.lineno,
                            "args": [arg.arg for arg in item.args.args],
                            "docstring": ast.get_docstring(item) or ""
                        })
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": methods,
                    "docstring": ast.get_docstring(node) or ""
                })

        # Segunda passada: identificar chamadas de função
        # Precisamos saber o escopo atual (dentro de qual função estamos)
        class CallVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_function = None
                self.calls = {}  # func_name -> [called_names]

            def visit_FunctionDef(self, node):
                old = self.current_function
                self.current_function = node.name
                if self.current_function not in self.calls:
                    self.calls[self.current_function] = []
                self.generic_visit(node)
                self.current_function = old

            def visit_Call(self, node):
                if self.current_function:
                    # Pega o nome da função chamada
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

        # Anexa as chamadas às funções
        for func in functions:
            func["calls"] = list(set(visitor.calls.get(func["name"], [])))

        return {
            "ok": True,
            "done": True,
            "data": {
                "file": str(file_path.relative_to(self.base_dir)),
                "imports": imports,
                "functions": functions,
                "classes": classes,
                "call_graph": visitor.calls  # mapa completo
            },
            "error": None,
            "message": f"Analisado: {len(functions)} funções, {len(classes)} classes."
        }

    def _analyze_directory(self, dir_path: Path) -> dict:
        if not dir_path.is_dir():
            return {"ok": False, "done": True, "error": "não é diretório", "message": f"'{dir_path}' não é um diretório."}
        project_map = {}
        dependencies = {}
        total_files = 0
        for root, _, files in os.walk(dir_path):
            for file in files:
                if file.endswith(".py"):
                    file_path = Path(root) / file
                    rel_path = str(file_path.relative_to(self.base_dir))
                    result = self._analyze_file(file_path)
                    if result["ok"]:
                        project_map[rel_path] = result["data"]
                        for imp in result["data"]["imports"]:
                            base = imp.split(".")[0]
                            if base not in dependencies:
                                dependencies[base] = []
                            dependencies[base].append(rel_path)
                        total_files += 1
        return {
            "ok": True,
            "done": True,
            "data": {
                "files": project_map,
                "dependencies": dependencies,
                "total_files": total_files
            },
            "error": None,
            "message": f"Mapa gerado com {total_files} arquivos."
        }