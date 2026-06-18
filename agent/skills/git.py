from .base import BaseSkill
from typing import Any, Dict
import subprocess
import shlex
from logger import logger

class GitSkill(BaseSkill):
    name = "git_reader"
    description = "Executa comandos básicos do git (status, log, diff) para inspecionar o repositório."

    def get_schema(self) -> Dict[str, Any]:
        return {
            "command": "string (apenas 'status', 'log', 'diff' permitidos)",
            "args": "string (argumentos extras opcionais, ex: '--oneline -n 5' para o log)"
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        cmd = args.get("command")
        if not cmd or cmd not in ["status", "log", "diff"]:
            return {"ok": False, "done": False, "error": "Apenas comandos 'status', 'log', e 'diff' são permitidos por segurança."}
            
        extra_args = args.get("args", "")
        
        full_cmd = ["git", cmd]
        if extra_args:
            full_cmd.extend(shlex.split(extra_args))
            
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                return {"ok": False, "done": False, "error": result.stderr or "Git command failed."}
                
            saida = result.stdout
            if not saida.strip():
                saida = "(sem saída/vazio)"
                
            return {"ok": True, "done": True, "data": saida}
            
        except FileNotFoundError:
            return {"ok": False, "done": False, "error": "O git não está instalado ou não foi encontrado no PATH."}
        except Exception as e:
            logger.error(f"GitSkill error: {e}", exc_info=True)
            return {"ok": False, "done": False, "error": str(e)}
