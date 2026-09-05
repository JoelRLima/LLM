"""Scripted practical responses; this module is evaluation data, not runtime policy."""

from __future__ import annotations

import json
from typing import Any


def practical_plan_payload(marker: str) -> dict[str, Any] | None:
    plans: dict[str, dict[str, Any]] = {
        "PV1-01": {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {"action": "modify", "targets": ["calculator.py"]},
            }],
        },
        "PV1-02": {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {"action": "modify", "targets": ["parser.py"]},
            }],
        },
        "PV1-03": {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "config.py"}},
                {
                    "tool": "code_task",
                    "args": {"action": "modify", "targets": ["config.py"]},
                },
            ],
        },
        "PV1-04": {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "src/worker.py"}},
                {
                    "tool": "code_task",
                    "args": {"action": "modify", "targets": ["src/settings.py"]},
                },
            ],
        },
        "PV1-05": {
            "action": "use_tools",
            "plan": [{"tool": "file_reader", "args": {"file_path": "feature.py"}}],
        },
        "PV1-06": {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {"action": "modify", "targets": ["src/module.py"]},
            }],
        },
        "PV1-07": {
            "action": "use_tools",
            "plan": [
                {"tool": "repository_state", "args": {}},
                {
                    "tool": "code_task",
                    "args": {"action": "modify", "targets": ["app.py"]},
                },
            ],
        },
        "PV1-08": {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "targets": ["src/math_ops.py"],
                    "include_tests": True,
                },
            }],
        },
    }
    value = plans.get(marker)
    return json.loads(json.dumps(value)) if value is not None else None


def practical_engineering_response(marker: str, _objective: str) -> str | None:
    changes: dict[str, dict[str, Any]] = {
        "PV1-02": {
            "path": "parser.py",
            "kind": "modify",
            "content": 'def normalize_mode(value: str) -> str:\n    return value.strip().casefold()\n',
        },
        "PV1-04": {
            "path": "src/settings.py",
            "kind": "modify",
            "content": "DEFAULT_TIMEOUT = 15\n",
        },
        "PV1-06": {
            "path": "src/module.py",
            "kind": "modify",
            "content": "VALUE = 1\n\n\ndef double(value: int) -> int:\n    return value * 2\n",
        },
        "PV1-07": {
            "path": "app.py",
            "kind": "modify",
            "content": "VALUE = 2\n",
        },
        "PV1-08": {
            "path": "src/math_ops.py",
            "kind": "modify",
            "content": "def increment(value: int) -> int:\n    return value + 1\n",
        },
    }
    if marker == "PV1-01":
        return json.dumps({
            "decision": "NO_CHANGE",
            "rationale": "A implementação observada já limita o intervalo inclusivo.",
            "reason_code": "NONE",
            "question": "",
            "changes": [],
        }, ensure_ascii=False)
    if marker == "PV1-03":
        return json.dumps({
            "decision": "NEEDS_INPUT",
            "rationale": "memory e disk são opções materialmente válidas e não há preferência fornecida.",
            "reason_code": "USER_CHOICE_REQUIRED",
            "question": "Qual backend deve ser usado em produção: memory ou disk?",
            "changes": [],
        }, ensure_ascii=False)
    change = changes.get(marker)
    return json.dumps({
        "decision": "CHANGE",
        "rationale": "A menor mudança necessária foi identificada na evidência do workspace.",
        "reason_code": "NONE",
        "question": "",
        "changes": [change],
    }, ensure_ascii=False) if change is not None else None


def practical_final_answer(marker: str) -> str | None:
    answers = {
        "PV1-01": "calculator.py já limita o valor ao intervalo inclusivo; nenhuma mudança foi necessária.",
        "PV1-02": "normalize_mode agora remove espaços externos e usa casefold().",
        "PV1-04": "DEFAULT_TIMEOUT foi atualizado para 15 em src/settings.py.",
        "PV1-05": "O valor atual de MODE é new.",
        "PV1-06": "double foi adicionado a src/module.py com a orientação aplicável ao src.",
        "PV1-07": "app.py foi atualizado; a alteração pré-existente em notes.txt foi preservada.",
        "PV1-08": "increment foi corrigido e a validação selecionou o teste relevante.",
    }
    return answers.get(marker)


__all__ = [
    "practical_engineering_response",
    "practical_final_answer",
    "practical_plan_payload",
]
