import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Optional

from agent.parsers import sanitize_error


class AutoCoder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def generate_tests(self, code: str, file_path: str) -> Optional[str]:
        """
        Gere testes unitários para o código fornecido.
        Retorna o código de teste pronto para execução.
        """
        prompt = (
            f"Gere testes unitários em Python para o seguinte código do arquivo '{file_path}':\n\n"
            f"```python\n{code[:4000]}\n```\n\n"
            "Regras:\n"
            "- Use apenas bibliotecas padrão (unittest ou pytest).\n"
            "- Cubra os casos principais e casos de borda.\n"
            "- NÃO inclua mocks de arquivos ou rede.\n"
            "- NÃO use bibliotecas externas.\n"
            "- Retorne APENAS o código Python dos testes, pronto para ser executado."
        )
        decision = self.orchestrator.context_manager.ask_model(prompt, step_type="tool_decision",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric)
        if isinstance(decision, dict):
            content = decision.get("content") or decision.get("answer") or decision.get("code") or ""
            return content.strip() if content.strip() else None
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return None

    def correct_code(self, original_code: str, file_path: str, test_code: str, error_msg: str) -> Optional[str]:
        """
        Corrige o código original com base no erro de teste.
        Retorna o código corrigido.
        """
        prompt = (
            f"O seguinte código Python do arquivo '{file_path}' falhou nos testes:\n\n"
            f"```python\n{original_code[:4000]}\n```\n\n"
            f"Testes executados:\n```python\n{test_code[:2000]}\n```\n\n"
            f"Erro reportado:\n{sanitize_error(error_msg)}\n\n"
            "Corrija APENAS o código original para que os testes passem. "
            "Retorne APENAS o código corrigido completo (incluindo imports)."
        )
        decision = self.orchestrator.context_manager.ask_model(prompt, step_type="tool_decision",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric)
        if isinstance(decision, dict):
            content = decision.get("content") or decision.get("answer") or decision.get("code") or ""
            return content.strip() if content.strip() else None
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return None

    def test_and_correct(self, file_path: str, objective: str) -> bool:
        """
        Ciclo teste-correção automático.
        Retorna True se os testes passaram (ou não foram necessários),
        False se falhou após todas as tentativas.
        """
        if not file_path.endswith(".py"):
            return True  # só testa arquivos Python

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()
        except Exception:
            return True

        if "def " not in code and "class " not in code:
            return True

        max_attempts = 3
        current_code = code
        test_code = None

        for attempt in range(max_attempts):
            if self.orchestrator.verbose:
                print(f"🧪 [TEST] Tentativa {attempt + 1}/{max_attempts} para '{file_path}'")

            test_code = self.generate_tests(current_code, file_path)
            if not test_code:
                if attempt == 0:
                    if self.orchestrator.verbose:
                        print("   ⚠️ Não foi possível gerar testes, pulando.")
                    return True
                if self.orchestrator.verbose:
                    print("   ⚠️ Não foi possível gerar testes para validar a correção. Abortando ciclo.")
                break

            test_file = None
            try:
                combined = f"{current_code}\n\n# --- TESTES ---\n{test_code}"
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
                    tmp.write(combined)
                    test_file = tmp.name

                result = subprocess.run(
                    [sys.executable, test_file],
                    capture_output=True, text=True, timeout=15,
                    cwd=os.path.dirname(os.path.abspath(file_path)) or "."
                )
                output = result.stdout + result.stderr

                if result.returncode == 0 and "FAILED" not in output and "Error" not in output:
                    if self.orchestrator.verbose:
                        print(f"   ✅ Testes passaram na tentativa {attempt + 1}!")
                    if attempt > 0:
                        try:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write(current_code)
                        except Exception:
                            pass
                    return True

                if attempt < max_attempts - 1:
                    corrected = self.correct_code(current_code, file_path, test_code, output)
                    if corrected:
                        current_code = corrected
                        if self.orchestrator.verbose:
                            print("   🔄 Código corrigido, retentando...")
                        self.orchestrator.context_manager.purge_stale_context()
                    else:
                        if self.orchestrator.verbose:
                            print("   ⚠️ Não foi possível corrigir o código.")
                        break

            except subprocess.TimeoutExpired:
                if self.orchestrator.verbose:
                    print("   ⏱️ Timeout na execução dos testes.")
            except Exception as e:
                if self.orchestrator.verbose:
                    print(f"   ❌ Erro ao executar testes: {e}")
            finally:
                if test_file and os.path.exists(test_file):
                    try:
                        os.remove(test_file)
                    except Exception:
                        pass

        self.orchestrator.fail_task()
        self.orchestrator._emit("error", {"step": self.orchestrator.agent_state.plan_step, "error": "Ciclo teste-correção falhou após todas as tentativas"})
        return False

    def generate_content(self, tool: str, args: dict, objective: str) -> Optional[str]:
        """
        Gera o conteúdo a ser escrito por file_writer usando o LLM.
        Tenta extrair o conteúdo do texto completo da resposta.
        """
        prompt = (
            f"Objetivo: {objective}\n\n"
            f"Ferramenta: {tool}\n"
            f"Argumentos: {json.dumps({k: v for k, v in args.items() if k != 'content'}, ensure_ascii=False)}\n\n"
            "Retorne APENAS o conteúdo a ser escrito no arquivo, sem formatação extra. "
            "Não use markdown, blocos de código ou explicações."
        )
        decision = self.orchestrator.context_manager.ask_model(prompt, step_type="tool_decision",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric)

        full_text = ""

        if isinstance(decision, dict):
            for key in ["content", "answer", "text", "code", "raw_response"]:
                val = decision.get(key, "")
                if val and len(str(val)) > 10:
                    full_text = str(val)
                    break
            if not full_text:
                parts = []
                for v in decision.values():
                    if isinstance(v, str) and len(v) > 10:
                        parts.append(v)
                full_text = "\n".join(parts)
        elif isinstance(decision, str) and len(decision) > 10:
            full_text = decision

        if not full_text:
            return None

        cleaned = full_text.strip()
        cleaned = re.sub(r'```[a-z]*\s*\n?', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'```', '', cleaned)
        cleaned = re.sub(r'^\*\*.*?\*\*\s*:?\n?', '', cleaned)
        cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^(Aqui está|Segue|Abaixo| Eis|O conteúdo|Conteúdo:|A poesia).*?\n', '', cleaned, flags=re.IGNORECASE)

        result = cleaned.strip()
        return result if len(result) > 10 else None