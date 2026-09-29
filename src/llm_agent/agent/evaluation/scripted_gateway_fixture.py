"""Bind product-like objectives into deterministic H-series tool plans."""

from __future__ import annotations

import copy


def bind_code_task_objective(payload: object, objective: str) -> object:
    bound = copy.deepcopy(payload)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get("tool") == "code_task" and isinstance(value.get("args"), dict):
                value["args"]["objective"] = objective
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(bound)
    return bound


__all__ = ["bind_code_task_objective"]
