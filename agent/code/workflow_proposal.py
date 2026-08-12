from __future__ import annotations

import json
import time
from typing import Any, Dict, Sequence

from agent.code.changes import ChangeSet, changeset_from_dict
from agent.llm.contracts import ModelMessage, ModelRequest
from agent.llm.structured_output import StructuredOutputStrategy, parse_structured_response

CHANGESET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["changes"],
    "properties": {
        "objective": {"type": "string"},
        "rationale": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "kind"],
                "properties": {
                    "path": {"type": "string"},
                    "kind": {"type": "string", "enum": ["create", "modify", "edit", "delete", "move"]},
                    "content": {"type": "string"},
                    "base_hash": {"type": "string"},
                    "destination_path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["operation", "start_line"],
                            "properties": {
                                "operation": {"type": "string", "enum": ["replace", "insert_before", "insert_after", "delete"]},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                                "expected_text": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def propose_changes(service: Any, objective: str, target_files: Sequence[str]) -> ChangeSet:
    strategy = StructuredOutputStrategy(service.context.model_gateway.capabilities)
    structured = strategy.select(schema=CHANGESET_SCHEMA)
    selected = service.context_selector.select(
        objective, target_files,
        max_chars=max(2000, service.context.limits.max_output_tokens * 6),
    )
    service.context.emit("code_context_selected", {
        "files": [item.path for item in selected.files], "truncated": selected.truncated,
    })
    prompt = _prompt(objective, target_files, selected.text, structured.instruction)
    request = ModelRequest(
        messages=(
            ModelMessage("system", "Você propõe mudanças revisáveis. Não escreva no filesystem."),
            ModelMessage("user", prompt),
        ),
        model=str(service.context.metadata.get("model", "default")),
        temperature=0.1,
        max_output_tokens=service.context.limits.max_output_tokens,
        structured_output=structured,
    )
    response, call_number = _complete(service, request)
    parsed = parse_structured_response(response.content, CHANGESET_SCHEMA)
    return changeset_from_dict(parsed, objective=objective)


def _prompt(objective: str, targets: Sequence[str], context: str, instruction: str | None) -> str:
    prompt = (
        f"Objetivo de engenharia: {objective}\nTargets: {json.dumps(list(targets), ensure_ascii=False)}\n"
        "Proponha o menor ChangeSet suficiente. Preserve APIs, não instale dependências e não altere arquivos fora do objetivo. "
        "Prefira kind=edit com faixas pequenas, expected_text e base_hash; use modify integral apenas quando necessário. "
        f"Não invente hashes ou linhas.\nContexto selecionado:{context}"
    )
    if instruction:
        prompt += f"\n{instruction}\nSchema esperado:\n{json.dumps(CHANGESET_SCHEMA, ensure_ascii=False)}"
    return prompt


def _complete(service: Any, request: ModelRequest) -> tuple[Any, int]:
    service.context.emit("model_call_started", {"operation": "propose_changes"})
    call_number = service.context.consume_model_call()
    started_at = time.monotonic()
    try:
        with service.context.model_slot():
            response = service.context.model_gateway.complete(request)
    except Exception:
        service.context.record_metric("model_call", {
            "operation": "propose_changes",
            "provider": service.context.model_gateway.provider_name,
            "call_number": call_number,
            "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
            "success": False,
        })
        raise
    usage = getattr(response, "usage", None)
    data = {
        "operation": "propose_changes",
        "provider": service.context.model_gateway.provider_name,
        "call_number": call_number,
        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
        "success": True,
    }
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, field, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            data[field] = int(value)
    service.context.record_metric("model_call", data)
    service.context.emit("model_call_completed", {
        "operation": "propose_changes",
        "provider": service.context.model_gateway.provider_name,
        "tokens": getattr(usage, "total_tokens", None),
    })
    return response, call_number
