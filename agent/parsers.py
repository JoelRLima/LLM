import json
import re
from typing import Optional, Any, Dict, Tuple

def extract_json(text: str) -> Optional[dict]:
    """Tenta extrair um objeto JSON de uma string."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text)
    start = cleaned.find("{")
    if start == -1:
        return None
    balance = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if not in_string:
            if c == '{':
                balance += 1
            elif c == '}':
                balance -= 1
                if balance == 0:
                    end = i
                    break
    if end == -1:
        return None
    json_str = cleaned[start:end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None

def stringify(obj: Any) -> str:
    """Converte um objeto para string JSON amigável, se possível."""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(obj)

def validate_decision(decision: Any) -> Tuple[bool, Optional[str]]:
    """Valida se a decisão do agente está no formato esperado."""
    if not isinstance(decision, dict):
        return False, "Decisão não é um dicionário."
    action = decision.get("action")
    if action not in ("tool", "final"):
        return False, f"Ação inválida: {action}"
    if action == "tool":
        if "tool" not in decision:
            return False, "Falta o campo 'tool'."
        if not isinstance(decision.get("tool"), str) or not decision.get("tool").strip():
            return False, "'tool' deve ser uma string não vazia."
        args = decision.get("args", {})
        if args is not None and not isinstance(args, dict):
            return False, "'args' deve ser um dicionário."
    if action == "final":
        if "answer" not in decision:
            return False, "Falta o campo 'answer'."
        if not isinstance(decision.get("answer"), str):
            return False, "'answer' deve ser uma string."
    return True, None

def normalize_tool_result(result: Any, error_patterns: list) -> Dict[str, Any]:
    """Garante que o resultado da ferramenta siga o contrato estrito."""
    if isinstance(result, dict):
        ok = result.get("ok") is True
        done = result.get("done") is True
        if not ok:
            done = False
        normalized = {"ok": ok, "done": done, "data": result.get("data"), "error": result.get("error"), "message": result.get("message")}
        for k, v in result.items():
            if k not in normalized:
                normalized[k] = v
        return normalized
    if result is None:
        return {"ok": False, "done": False, "data": None, "error": "Tool retornou None.", "message": "Retorno vazio da ferramenta."}
    if isinstance(result, str):
        lower = result.strip().lower()
        if any(pattern in lower for pattern in error_patterns):
            return {"ok": False, "done": False, "data": None, "error": result, "message": "A ferramenta retornou uma mensagem de erro."}
        return {"ok": True, "done": True, "data": result, "error": None, "message": None}
    return {"ok": True, "done": True, "data": result, "error": None, "message": None}

def extract_json_from_end(text: str) -> Optional[Dict]:
    """Tenta extrair o último objeto JSON válido no texto."""
    import re
    # Procura por { ... } que seja um JSON válido
    matches = list(re.finditer(r'\{.*?\}', text, re.DOTALL))
    for match in reversed(matches):
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            continue
    return None