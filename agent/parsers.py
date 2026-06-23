import json
import os
import re
from typing import Any, Dict, Optional, Tuple


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

def sanitize_error(error_message: str) -> str:
    """
    Extrai apenas o tipo do erro, a mensagem essencial e a linha relevante
    de um stack trace ou mensagem de erro, economizando tokens.
    """
    if not error_message:
        return ""

    # Remove quebras de linha duplicadas e espaços excessivos
    cleaned = re.sub(r'\n{3,}', '\n\n', error_message.strip())

    # Tenta extrair a última linha relevante de um traceback Python
    lines = cleaned.split('\n')
    error_type = ""
    error_msg = ""
    relevant_line = ""

    # Procura por padrões de traceback
    for i, line in enumerate(lines):
        # Detecta a linha do erro (ex: "TypeError: ...")
        if re.match(r'^[A-Za-z_]\w*Error:', line):
            error_type = line.split(':')[0].strip()
            error_msg = line
            # Tenta pegar a linha seguinte como contexto
            if i + 1 < len(lines) and lines[i+1].strip():
                relevant_line = lines[i+1].strip()[:200]
            break

    # Se não encontrou padrão de traceback, retorna versão curta
    if not error_type:
        # Pega apenas as primeiras e últimas linhas
        if len(lines) > 10:
            cleaned = '\n'.join(lines[:3] + ['...'] + lines[-3:])
        return cleaned[:600]

    # Monta versão sanitizada
    sanitized = f"{error_msg}"
    if relevant_line:
        sanitized += f"\n  → {relevant_line}"

    # Adiciona dica de linha se disponível (ex: "line 42")
    line_match = re.search(r'line (\d+)', error_msg)
    if line_match:
        sanitized += f" (linha {line_match.group(1)})"

    return sanitized[:500]

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

def validate_tool_args(tool_name: str, args: Dict[str, Any], skills: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Valida os argumentos de uma ferramenta contra o schema que ela exporta.
    Retorna (válido, mensagem_de_erro).
    """
    skill = skills.get(tool_name)
    if not skill:
        return True, None  # ferramenta desconhecida, deixa executar e falhar depois

    schema = skill.get_schema()
    if not schema or not isinstance(schema, dict):
        return True, None  # sem schema, não valida

    required = schema.get("required", [])
    properties = schema.get("properties", {})
    errors = []

    # 1. Verifica campos obrigatórios
    for field in required:
        if field not in args or args[field] is None:
            errors.append(f"Campo obrigatório ausente: '{field}'")

    # 2. Verifica tipos e valores permitidos
    for field, value in args.items():
        prop = properties.get(field)
        if not prop:
            continue  # campo extra, ignoramos (poderia ser erro, mas é permissivo)

        expected_type = prop.get("type", "string")
        actual_type = type(value).__name__

        # Validação de tipo
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"'{field}': esperado string, recebido {actual_type}")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"'{field}': esperado número, recebido {actual_type}")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"'{field}': esperado booleano, recebido {actual_type}")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"'{field}': esperado objeto, recebido {actual_type}")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"'{field}': esperado array, recebido {actual_type}")

        # Validação de enum (valores permitidos)
        allowed = prop.get("enum")
        if allowed and value not in allowed:
            errors.append(f"'{field}': valor '{value}' não está entre os permitidos: {allowed}")

        # Validação de range numérico
        if expected_type == "number" and isinstance(value, (int, float)):
            minimum = prop.get("minimum")
            maximum = prop.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(f"'{field}': valor {value} é menor que o mínimo {minimum}")
            if maximum is not None and value > maximum:
                errors.append(f"'{field}': valor {value} é maior que o máximo {maximum}")

    # 3. Validações específicas por ferramenta
    if tool_name == "file_reader":
        start = args.get("start_line")
        end = args.get("end_line")
        if start is not None and end is not None:
            if start > end:
                errors.append(f"'start_line' ({start}) não pode ser maior que 'end_line' ({end})")
        file_path = args.get("file_path", "")
        if file_path and not os.path.exists(file_path):
            errors.append(f"Arquivo não encontrado: '{file_path}'")

    if tool_name == "file_writer":
        action = args.get("action", "write")
        if action == "ast_patch":
            if not args.get("target"):
                errors.append("Campo 'target' obrigatório para ast_patch")
            if not args.get("new_code"):
                errors.append("Campo 'new_code' obrigatório para ast_patch")

    if errors:
        return False, "; ".join(errors)
    return True, None
