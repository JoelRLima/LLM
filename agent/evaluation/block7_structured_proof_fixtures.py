"""Block 7 fixtures for structured-proof and full-consumption arms."""

from __future__ import annotations

from typing import Any


def _code_task(objective: str, target: str) -> dict[str, Any]:
    return {
        "action": "use_tools",
        "plan": [
            {
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": objective,
                    "targets": [target],
                },
            }
        ],
    }


H19_PLAN_PAYLOADS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "H19_POSITIVE",
        _code_task(
            "H19_POSITIVE: edite h19_target.txt para conter H19_DONE.",
            "h19_target.txt",
        ),
    ),
    (
        "H19_SUFFIX",
        _code_task(
            "H19_SUFFIX: edite h19_suffix.txt para conter H19_SUFFIX_DONE apos confirmacao futura.",
            "h19_suffix.txt",
        ),
    ),
    (
        "H19_PREFIX",
        _code_task(
            "H19_PREFIX: talvez edite h19_prefix.txt para conter H19_PREFIX_DONE.",
            "h19_prefix.txt",
        ),
    ),
    (
        "H19_QUOTED",
        _code_task(
            'H19_QUOTED: explique o exemplo "edite h19_quoted.txt" sem modificar arquivos.',
            "h19_quoted.txt",
        ),
    ),
    (
        "H19_CONTRAST",
        _code_task(
            "H19_CONTRAST: edite h19_contrast.txt, mas isso e proibido.",
            "h19_contrast.txt",
        ),
    ),
    (
        "H19_MULTIPLE",
        _code_task(
            "H19_MULTIPLE: edite h19_first.txt; edite h19_second.txt depois de eu aprovar.",
            "h19_first.txt",
        ),
    ),
)

H19_FINAL_ANSWERS: tuple[tuple[str, str], ...] = (
    ("H19_POSITIVE", "A escrita positiva foi executada, mas permaneceu unverified."),
)


__all__ = ["H19_FINAL_ANSWERS", "H19_PLAN_PAYLOADS"]
