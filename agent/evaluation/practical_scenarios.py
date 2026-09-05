"""The versioned practical diagnostic fixtures used by the evaluation owners."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.contracts import CapabilityScenario, FileExpectation, ScenarioExpectation
from agent.evaluation.practical_fixture_preparation import (
    prepare_practical_application,
    prepare_practical_scenario,
    prepare_practical_workspace,
)
from agent.llm.identity import canonical_json, sha256_digest

PRACTICAL_SET_VERSION = "PRACTICAL-V1"


def _expectation(
    *,
    success: bool = True,
    files: tuple[FileExpectation, ...] = (),
    unchanged: tuple[str, ...] = (),
    allowed_changed: tuple[str, ...] = (),
    answer_contains: tuple[str, ...] = (),
    answer_not_contains: tuple[str, ...] = (),
) -> ScenarioExpectation:
    return ScenarioExpectation(
        success=success,
        files=files,
        unchanged_files=unchanged,
        allowed_changed_files=allowed_changed,
        answer_contains=answer_contains,
        answer_not_contains=answer_not_contains,
    )


def _scenario(
    scenario_id: str,
    objective: str,
    initial_files: Mapping[str, str],
    expectation: ScenarioExpectation,
    *,
    preparation: str = "none",
    oracle: Mapping[str, Any],
) -> CapabilityScenario:
    return CapabilityScenario(
        scenario_id=scenario_id,
        capability="practical-diagnostic",
        objective=objective,
        initial_files=dict(initial_files),
        expectation=expectation,
        metadata={
            "practical_set_version": PRACTICAL_SET_VERSION,
            "approval_mode": "explicit",
            "preparation": preparation,
            "oracle": dict(oracle),
        },
    )


PRACTICAL_V1: tuple[CapabilityScenario, ...] = (
    _scenario(
        "PV1-01",
        "Corrija `clamp` em `calculator.py` para limitar `value` ao intervalo inclusivo `[low, high]`.",
        {"calculator.py": 'def clamp(value: int, low: int, high: int) -> int:\n    return max(low, min(value, high))\n'},
        _expectation(
            unchanged=("calculator.py",),
            allowed_changed=("calculator.py",),
        ),
        oracle={
            "proposal": "NO_CHANGE",
            "verifier": "SUPPORTED",
            "requested_write_waiver": "evidence_backed",
            "byte_identical": "calculator.py",
            "mutation": "none",
        },
    ),
    _scenario(
        "PV1-02",
        "Corrija `normalize_mode` em `parser.py` para remover espaços externos e normalizar sem diferenciar maiúsculas/minúsculas usando `casefold()`.",
        {"parser.py": 'def normalize_mode(value: str) -> str:\n    return value.lower()\n'},
        _expectation(
            files=(FileExpectation(
                "parser.py",
                exact_content='def normalize_mode(value: str) -> str:\n    return value.strip().casefold()\n',
            ),),
            allowed_changed=("parser.py",),
        ),
        oracle={
            "forbidden_proposal": "NO_CHANGE",
            "required_content": 'def normalize_mode(value: str) -> str:\n    return value.strip().casefold()\n',
            "mutation_scope": ("parser.py",),
        },
    ),
    _scenario(
        "PV1-03",
        "Configure `CACHE_BACKEND` em `config.py` para o backend que eu devo usar em produção. As opções válidas são `memory` e `disk`; eu não forneci preferência.",
        {"config.py": 'SUPPORTED_CACHE_BACKENDS = ("memory", "disk")\nCACHE_BACKEND = "unset"\n'},
        _expectation(
            success=False,
            unchanged=("config.py",),
        ),
        oracle={
            "proposal": "NEEDS_INPUT",
            "reason": "USER_CHOICE_REQUIRED",
            "terminal_status": "blocked",
            "failure_code": "CODE_INPUT_REQUIRED",
            "no_checkpoint_state": True,
        },
    ),
    _scenario(
        "PV1-04",
        "Atualize `DEFAULT_TIMEOUT` de 10 para 15 no arquivo em que ele é definido.",
        {
            "src/settings.py": "DEFAULT_TIMEOUT = 10\n",
            "src/worker.py": "from .settings import DEFAULT_TIMEOUT\n\n\ndef timeout() -> int:\n    return DEFAULT_TIMEOUT\n",
        },
        _expectation(
            files=(FileExpectation("src/settings.py", exact_content="DEFAULT_TIMEOUT = 15\n"),),
            unchanged=("src/worker.py",),
            allowed_changed=("src/settings.py",),
        ),
        oracle={
            "forbidden_failure_code": "CODE_INPUT_REQUIRED",
            "grounded_target": "src/settings.py",
            "mutation_scope": ("src/settings.py",),
        },
    ),
    _scenario(
        "PV1-05",
        "Leia `feature.py` e informe o valor atual de `MODE`.",
        {"feature.py": 'MODE = "new"\n'},
        _expectation(
            unchanged=("feature.py",),
            answer_contains=("new",),
            answer_not_contains=("MODE atual é old",),
        ),
        preparation="stale_memory",
        oracle={
            "answer_value": "new",
            "forbidden_current_value": "old",
            "stale_summary_absent": True,
            "stale_current_record": False,
            "mutation": "none",
        },
    ),
    _scenario(
        "PV1-06",
        "Adicione `double(value: int) -> int` em `src/module.py`, retornando o dobro de `value`.",
        {
            "AGENTS.md": "ROOT_GUIDANCE_SENTINEL\nUse snake_case for Python names.\n",
            "src/AGENTS.md": "SRC_GUIDANCE_SENTINEL\nPreserve explicit type hints in src/.\n",
            "tests/AGENTS.md": "UNRELATED_TEST_GUIDANCE_SENTINEL\nUse pytest fixtures in tests/.\n",
            "src/module.py": "VALUE = 1\n",
        },
        _expectation(
            files=(FileExpectation(
                "src/module.py",
                exact_content="VALUE = 1\n\n\ndef double(value: int) -> int:\n    return value * 2\n",
            ),),
            unchanged=("AGENTS.md", "src/AGENTS.md", "tests/AGENTS.md"),
            allowed_changed=("src/module.py",),
        ),
        oracle={
            "guidance_records": {
                "required": ("ROOT_GUIDANCE_SENTINEL", "SRC_GUIDANCE_SENTINEL"),
                "omitted": ("UNRELATED_TEST_GUIDANCE_SENTINEL",),
                "trust": "UNTRUSTED_PROJECT_GUIDANCE",
            },
            "trusted_system_additions": False,
            "mutation_scope": ("src/module.py",),
        },
    ),
    _scenario(
        "PV1-07",
        "Antes de alterar `app.py`, verifique o estado do repositório. Depois mude `VALUE` de 1 para 2 sem tocar em mudanças pré-existentes.",
        {"app.py": "VALUE = 1\n", "notes.txt": "committed\n"},
        _expectation(
            files=(FileExpectation("app.py", exact_content="VALUE = 2\n"),),
            unchanged=("notes.txt",),
            allowed_changed=("app.py",),
        ),
        preparation="dirty_repository",
        oracle={
            "repository_state_owner": "repository_state",
            "branch": "main",
            "preexisting_dirty_path": "notes.txt",
            "final_dirty_bytes": "preexisting local edit\n",
            "mutation_scope": ("app.py",),
            "raw_diff": False,
        },
    ),
    _scenario(
        "PV1-08",
        "Corrija `increment` em `src/math_ops.py` para retornar `value + 1` e valide com os testes relevantes.",
        {
            "src/math_ops.py": "def increment(value: int) -> int:\n    return value\n",
            "tests/test_math_ops.py": "from src.math_ops import increment\n\n\ndef test_increment() -> None:\n    assert increment(2) == 3\n",
            "tests/test_unrelated.py": "def test_unrelated() -> None:\n    assert True\n",
        },
        _expectation(
            files=(FileExpectation(
                "src/math_ops.py",
                exact_content="def increment(value: int) -> int:\n    return value + 1\n",
            ),),
            unchanged=("tests/test_math_ops.py", "tests/test_unrelated.py"),
            allowed_changed=("src/math_ops.py",),
        ),
        oracle={
            "include_tests": True,
            "selected_tests": ("tests/test_math_ops.py",),
            "excluded_tests": ("tests/test_unrelated.py",),
            "scope": "TARGETED_TESTS",
            "not_scope": "FULL",
        },
    ),
)


def _json_expectation(expectation: ScenarioExpectation) -> dict[str, Any]:
    return {
        "success": expectation.success,
        "files": [
            {
                "path": item.path,
                "exists": item.exists,
                "exact_content": item.exact_content,
                "contains": list(item.contains),
                "not_contains": list(item.not_contains),
            }
            for item in expectation.files
        ],
        "unchanged_files": list(expectation.unchanged_files),
        "allowed_changed_files": list(expectation.allowed_changed_files),
        "answer_contains": list(expectation.answer_contains),
        "answer_not_contains": list(expectation.answer_not_contains),
        "max_steps": expectation.max_steps,
    }


def practical_fixture_identity(
    scenarios: tuple[CapabilityScenario, ...] = PRACTICAL_V1,
) -> str:
    """Hash the exact practical definitions with the existing hash primitive."""

    payload = {
        "set_version": PRACTICAL_SET_VERSION,
        "common_setup": {
            "approval_mode": "explicit",
            "git_global_config": "disabled",
            "setup_before_measured_task": True,
            "harness": "CapabilityEvaluator/AgentApplicationScenarioExecutor",
        },
        "scenarios": [
            {
                "scenario_id": scenario.scenario_id,
                "capability": scenario.capability,
                "objective": scenario.objective,
                "initial_files": dict(sorted(scenario.initial_files.items())),
                "expectation": _json_expectation(scenario.expectation),
                "metadata": scenario.metadata,
            }
            for scenario in scenarios
        ],
    }
    return sha256_digest(canonical_json(payload))




__all__ = [
    "PRACTICAL_SET_VERSION",
    "PRACTICAL_V1",
    "practical_fixture_identity",
    "prepare_practical_scenario",
    "prepare_practical_workspace",
    "prepare_practical_application",
]
