import importlib
import pkgutil
import inspect
from pathlib import Path
from typing import List, Any
from .base import BaseSkill          # ← ESSA LINHA É OBRIGATÓRIA
from logger import logger

SKILL_CONFIG = {
    "FileReaderSkill": {"base_dir": "."},
    "DirectoryListerSkill": {"base_dir": "."},
    "GrepSkill": {"base_dir": "."},
    "CodeAnalyzerSkill": {"base_dir": "."},
    "SessionMemorySkill": {"orchestrator": None},
    "SummarizeSkill": {"orchestrator": None},
    "PythonExecutorSkill": {"timeout_seconds": 10},
    "WebSearchSkill": {},
    "GitSkill": {},
    "FileWriterSkill": {"base_dir": "."},
    "ShellSkill": {"base_dir": ".", "timeout": 30},
}

def load_all_skills() -> List[Any]:
    skills: List[Any] = []
    package_path = Path(__file__).parent

    for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
        if module_name in ("__init__", "base"):
            continue
        try:
            module = importlib.import_module(f".{module_name}", package=__package__)
        except Exception as e:
            logger.warning(f"Erro ao carregar skill '{module_name}': {e}")
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseSkill) or obj is BaseSkill:
                continue
            kwargs = SKILL_CONFIG.get(name, {})
            try:
                skill_instance = obj(**kwargs)
                skills.append(skill_instance)
            except Exception as e:
                logger.warning(f"Erro ao instanciar skill '{name}': {e}")
                continue

    return skills