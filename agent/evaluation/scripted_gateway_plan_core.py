"""Plan payloads for the deterministic H-series gateway."""

from __future__ import annotations

from typing import Any


def _scalar_plan(file_name: str) -> dict[str, Any]:
    return {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": file_name}}, {"tool": "grep", "args": {"path": "."}, "bindings": {"pattern": {"from_step": 1, "path": []}}}]}


CORE_PLAN_PAYLOADS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "H4_DUPLICATE_REJECTED",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "fonte_h4.txt"}},
                {
                    "tool": "grep",
                    "args": {"pattern": "H4_VALUE", "path": "."},
                    "bindings": {"pattern": {"from_step": 1, "path": []}},
                },
            ],
        },
    ),
    ("H1_DIRECT", {"action": "direct_response", "answer": "abacaxi azul"}),
    (
        "H1_WORKSPACE",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "h1_observation.txt"}}]},
    ),
    ("H2", _scalar_plan("fonte_h2.txt")),
    (
        "H3",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "grep", "args": {"pattern": "H3_SOURCE_MARKER", "path": "."}},
                {
                    "tool": "grep",
                    "args": {"path": "."},
                    "bindings": {"pattern": {"from_step": 1, "path": [0, "content"]}},
                },
            ],
        },
    ),
    ("H4", _scalar_plan("fonte_h4.txt")),
    (
        "H5",
        {
            "action": "continue_after_plan",
            "plan": [{"tool": "file_reader", "args": {"file_path": "h5_first.txt"}}],
        },
    ),
    (
        "H6",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": 123}}]},
    ),
    (
        "H7",
        {"action": "use_tools", "plan": [{"tool": "grep", "args": {"pattern": "H7_EMPTY_SENTINEL", "path": "."}}]},
    ),
    (
        "H8",
        {"action": "use_tools", "plan": [{"tool": "grep", "args": {"pattern": "[", "path": "."}}]},
    ),
    (
        "H9",
        {
            "action": "use_tools",
            "plan": [
                {
                    "tool": "grep",
                    "args": {"pattern": "H9_TRUNCATED_SENTINEL", "path": ".", "max_results": 1},
                }
            ],
        },
    ),
    (
        "H10",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h10_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H10_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "modify",
                            "objective": "H10_EFFECT: altere h10_condition.txt para H10_EFFECT",
                            "targets": ["h10_condition.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H11_MISSING",
        {
            "action": "use_tools",
            "plan": [{"tool": "file_reader", "args": {"file_path": "../h11_missing.txt"}}],
        },
    ),
    (
        "H11_PRESENT",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "h11_present.txt"}}]},
    ),
    (
        "H12",
        {
            "action": "use_tools",
            "plan": [
                {
                    "tool": "code_task",
                    "args": {
                        "action": "modify",
                        "objective": "H12: altere h12_module.py para retornar 2",
                        "targets": ["h12_module.py"],
                    },
                }
            ],
        },
    ),
    ("H13_SOURCE", {"action": "direct_response", "answer": "Resumo gerado a partir de foo.py; nenhuma escrita foi solicitada."}),
    (
        "H13_DEST",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "generate",
                    "objective": "H13_DEST",
                    "targets": ["resumo.md"],
                },
            }],
        },
    ),
    (
        "H13_MIXED",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "generate",
                    "objective": "H13_MIXED",
                    "targets": ["resumo.md"],
                },
            }],
        },
    ),
    (
        "H14_PT",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_PT",
                    "targets": ["permitido.txt"],
                },
            }],
        },
    ),
    (
        "H14_EN",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_EN",
                    "targets": ["allowed.txt"],
                },
            }],
        },
    ),
    (
        "H14_MIXED",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_MIXED",
                    "targets": ["permitido.txt"],
                },
            }],
        },
    ),
    ("H14_COPULA", {"action": "direct_response", "answer": "foo.py permaneceu intacto."}),
    ("H14_FORBIDDEN", {"action": "direct_response", "answer": "foo.py permaneceu intacto."}),
    (
        "H14_SCOPE",
        {"action": "use_tools", "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H14_SCOPE", "targets": ["permitido.txt"]}}]},
    ),

)

__all__ = ["CORE_PLAN_PAYLOADS"]
