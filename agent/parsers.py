import json
import re
from typing import Any, Dict, List, Optional, Tuple, cast

from agent.contracts import ToolResult


def _find_balanced_json_end(text: str, start: int) -> Optional[int]:
    """
    A partir do índice de um '{' em `text`, encontra o índice do '}' que
    fecha esse objeto, respeitando strings (e escapes dentro delas).
    Retorna None se o objeto nunca fecha.
    """
    balance = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
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
                    return i
    return None


def extract_json(text: str) -> Optional[dict]:
    """Tenta extrair um objeto JSON de uma string."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text)
    start = cleaned.find("{")
    if start == -1:
        return None
    end = _find_balanced_json_end(cleaned, start)
    if end is None:
        return None
    json_str = cleaned[start:end + 1]
    try:
        return cast(Dict[Any, Any], json.loads(json_str))
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
        tool = decision.get("tool")
        if not isinstance(tool, str) or not tool.strip():
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

def normalize_tool_result(result: Any, error_patterns: List[str]) -> ToolResult:
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
        return cast(ToolResult, normalized)
    if result is None:
        return {"ok": False, "done": False, "data": None, "error": "Tool retornou None.", "message": "Retorno vazio da ferramenta."}
    if isinstance(result, str):
        lower = result.strip().lower()
        if any(pattern in lower for pattern in error_patterns):
            return {"ok": False, "done": False, "data": None, "error": result, "message": "A ferramenta retornou uma mensagem de erro."}
        return {"ok": True, "done": True, "data": result, "error": None, "message": None}
    return {"ok": True, "done": True, "data": result, "error": None, "message": None}

def extract_json_from_end(text: str) -> Optional[Dict]:
    """
    Tenta extrair o último objeto JSON válido no texto, varrendo todas as
    ocorrências de '{' e usando balanceamento real de chaves (respeitando
    strings) para encontrar o fechamento correto de cada candidato.
    """
    if not text:
        return None

    last_valid = None
    search_from = 0
    while True:
        start = text.find("{", search_from)
        if start == -1:
            break
        end = _find_balanced_json_end(text, start)
        if end is not None:
            candidate = text[start:end + 1]
            try:
                last_valid = json.loads(candidate)
            except json.JSONDecodeError:
                pass
            search_from = start + 1
        else:
            search_from = start + 1

    return last_valid

def _validate_required(args: Dict[str, Any], required: List[str]) -> List[str]:
    return [
        f"Campo obrigatório ausente: '{field}'"
        for field in required
        if field not in args or args[field] is None
    ]


def _type_error(field: str, value: Any, expected_type: str) -> Optional[str]:
    expected_types: Dict[str, Any] = {
        "string": str,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    expected_python = expected_types.get(expected_type)
    if expected_python is None or isinstance(value, expected_python):
        return None
    labels = {"string": "string", "number": "número", "boolean": "booleano", "object": "objeto", "array": "array"}
    return f"'{field}': esperado {labels[expected_type]}, recebido {type(value).__name__}"


def _property_errors(field: str, value: Any, prop: Dict[str, Any]) -> List[str]:
    expected_type = str(prop.get("type", "string"))
    errors: List[str] = []
    if error := _type_error(field, value, expected_type):
        errors.append(error)
    allowed = prop.get("enum")
    if allowed and value not in allowed:
        errors.append(f"'{field}': valor '{value}' não está entre os permitidos: {allowed}")
    if expected_type == "number" and isinstance(value, (int, float)):
        minimum = prop.get("minimum")
        maximum = prop.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"'{field}': valor {value} é menor que o mínimo {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"'{field}': valor {value} é maior que o máximo {maximum}")
    return errors


def _tool_specific_errors(tool_name: str, args: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if tool_name == "file_reader":
        start, end = args.get("start_line"), args.get("end_line")
        if start is not None and end is not None and start > end:
            errors.append(f"'start_line' ({start}) não pode ser maior que 'end_line' ({end})")
    if tool_name == "file_writer" and args.get("action", "write") == "ast_patch":
        if not args.get("target"):
            errors.append("Campo 'target' obrigatório para ast_patch")
        if not args.get("new_code"):
            errors.append("Campo 'new_code' obrigatório para ast_patch")
    return errors


def validate_tool_args(tool_name: str, args: Dict[str, Any], skills: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Valida argumentos contra o schema exportado pela ferramenta."""
    skill = skills.get(tool_name)
    if not skill:
        return True, None  # ferramenta desconhecida, deixa executar e falhar depois

    schema = skill.get_schema()
    if not schema or not isinstance(schema, dict):
        return True, None  # sem schema, não valida

    required = schema.get("required", [])
    properties = schema.get("properties", {})
    errors = _validate_required(args, required)
    for field, value in args.items():
        prop = properties.get(field)
        if not prop:
            continue
        errors.extend(_property_errors(field, value, prop))
    errors.extend(_tool_specific_errors(tool_name, args))

    if errors:
        return False, "; ".join(errors)
    return True, None
