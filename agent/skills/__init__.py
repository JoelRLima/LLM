import importlib
import pkgutil
import inspect
from pathlib import Path
from .base import BaseSkill          # ← ESSA LINHA É OBRIGATÓRIA

SKILL_CONFIG = {
    "FileReaderSkill": {"base_dir": "."},
    "DirectoryListerSkill": {"base_dir": "."},
    "GrepSkill": {"base_dir": "."},
    "CodeAnalyzerSkill": {"base_dir": "."},
    "SessionMemorySkill": {"orchestrator": None},
    "PythonExecutorSkill": {"timeout_seconds": 10},
}

def load_all_skills():
    skills = []
    package_path = Path(__file__).parent

    for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
        if module_name in ("__init__", "base"):
            continue
        try:
            module = importlib.import_module(f".{module_name}", package=__package__)
        except Exception as e:
            print(f"⚠️  Erro ao carregar skill '{module_name}': {e}")
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseSkill) or obj is BaseSkill:
                continue
            kwargs = SKILL_CONFIG.get(name, {})
            try:
                skill_instance = obj(**kwargs)
                skills.append(skill_instance)
            except Exception as e:
                print(f"⚠️  Erro ao instanciar skill '{name}': {e}")
                continue

    return skills