import json
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple, cast

from agent.runtime.argument_contract import validate_operation_arguments
from agent.runtime.schema_validation import normalize_argument_schema, validate_schema_arguments
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from agent.tools.result_adapter import ensure_canonical_result


def _find_balanced_json_end(text: str, start: int) -> Optional[int]:
    """Find the closing brace for an object while respecting JSON strings."""

    balance = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        char = text[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                balance += 1
            elif char == "}":
                balance -= 1
                if balance == 0:
                    return i
    return None


def extract_json(text: str) -> Optional[dict]:
    """Try to extract one JSON object from text."""

    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text)
    start = cleaned.find("{")
    if start == -1:
        return None
    end = _find_balanced_json_end(cleaned, start)
    if end is None:
        return None
    try:
        return cast(Dict[Any, Any], json.loads(cleaned[start : end + 1]))
    except json.JSONDecodeError:
        return None


def stringify(obj: Any) -> str:
    """Convert an object to a readable JSON string where possible."""

    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(obj)


def validate_decision(decision: Any) -> Tuple[bool, Optional[str]]:
    """Validate the model decision envelope."""

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
    """Normalize an older adapter value into the canonical runtime result."""

    if isinstance(result, ToolResult):
        return result
    if isinstance(result, Mapping):
        ok = result.get("ok") is True
        done = result.get("done") is True
        if not ok:
            done = False
        normalized = {
            "ok": ok,
            "done": done,
            "data": result.get("data"),
            "error": result.get("error"),
            "message": result.get("message"),
        }
        for key, value in result.items():
            if key not in normalized:
                normalized[key] = value
        return ensure_canonical_result(normalized)
    if result is None:
        return ToolResult(
            invocation_id="parser:none",
            status=ToolStatus.FAILED,
            data=None,
            error=ToolError("EMPTY_RESULT", "Tool retornou None."),
            message="Retorno vazio da ferramenta.",
            executed=False,
            done_override=False,
        )
    if isinstance(result, str):
        lower = result.strip().lower()
        if any(pattern in lower for pattern in error_patterns):
            return ToolResult(
                invocation_id="parser:string-error",
                status=ToolStatus.FAILED,
                data=None,
                error=ToolError("TOOL_ERROR", result),
                message="A ferramenta retornou uma mensagem de erro.",
                executed=True,
                done_override=False,
            )
        return ToolResult(
            invocation_id="parser:string",
            status=ToolStatus.SUCCEEDED,
            data=result,
            executed=True,
        )
    return ToolResult(
        invocation_id="parser:value",
        status=ToolStatus.SUCCEEDED,
        data=result,
        executed=True,
    )


def extract_json_from_end(text: str) -> Optional[Dict]:
    """Return the last valid JSON object embedded in text."""

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
            try:
                last_valid = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        search_from = start + 1
    return last_valid


def validate_tool_args(
    tool_name: str,
    args: Dict[str, Any],
    skills: Dict[str, Any],
    bound_fields: set[str] | None = None,
) -> Tuple[bool, Optional[str]]:
    """Validate one skill through the shared schema and operation contracts."""

    skill = skills.get(tool_name)
    if not skill:
        return True, None
    schema_provider = getattr(skill, "get_schema", None)
    schema = schema_provider() if callable(schema_provider) else getattr(skill, "schema", {})
    try:
        if schema:
            if not isinstance(schema, Mapping):
                raise ValueError("schema must be an object")
            effective_schema = normalize_argument_schema(schema)
            validate_schema_arguments(
                effective_schema,
                args,
                bound_fields=bound_fields,
                planning=True,
            )
        validate_operation_arguments(
            skill,
            args,
            bound_fields=bound_fields,
            planning=True,
        )
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    return True, None
