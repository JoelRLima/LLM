"""Remaining deterministic H-series plan payloads."""

from __future__ import annotations

from agent.evaluation.scripted_gateway_plan_core import CORE_PLAN_PAYLOADS
from agent.evaluation.structured_proof_fixtures import H19_PLAN_PAYLOADS

SCRIPTED_PLAN_PAYLOADS = CORE_PLAN_PAYLOADS + (
(
        "H15_UNRESOLVED",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt", "start_line": 1, "end_line": 1}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_TRUE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_FALSE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_FALSE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_NEGATIVE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {"kind": "deferred_condition", "observation_ref": 1,
                 "predicate": {"op": "equals", "value": "H15_FALSE"},
                 "on_true": {"tool": "code_task", "args": {"action": "generate", "objective": "H15_NEGATIVE", "targets": ["h15_target.txt"]}},
                 "on_false": {"waive_effect": "write"}},
            ],
        },
    ),
    (
        "H15_NEGPROHIB",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_FALSE"},
                    "on_true": {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_TRUE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_TRUE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H16_LICENSE1",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]},
    ),
    (
        "H16_LICENSE2",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]},
    ),
    ("H16_DEPENDENCIES", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_SUMMARY", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_ARBITRARY", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "config.toml"}}]}),
    ("H16_CONTENT", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_ENGLISH", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "package.json"}}]}),
    ("H16_CONCEPT", {"action": "direct_response", "answer": "Arquivo de configuração de projetos Python."}),
    (
        "H17_AUTONOMOUS",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_AUTO", "targets": ["notes.md"]}}],
        },
    ),
    (
        "H17_EXPLICIT",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_EXPLICIT", "targets": ["settings.json"]}}],
        },
    ),
    (
        "H17_EXTENSION",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_EXTENSION", "targets": ["extension.md"]}}],
        },
    ),
    ("H17_NEGATIVE", {"action": "direct_response", "answer": "protected.py permaneceu intacto por falta de autorização positiva."}),
    ("H17_AMBIGUOUS", {"action": "direct_response", "answer": "A alteração de uncertain.py não foi autorizada sem uma solicitação inequívoca."}),
    ("H18_NETWORK", {"action": "direct_response", "answer": "H18_SENTINEL foi tratado sem alterar arquivos."}),
) + H19_PLAN_PAYLOADS

__all__ = ["SCRIPTED_PLAN_PAYLOADS"]
