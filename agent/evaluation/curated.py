"""Curated Marco 3 capability scenarios.

These are intentionally small and declarative.  Runtime-specific preparation
(for example creating a local Git repository or registering an extension) is
provided by the AgentApplication executor, not duplicated in each scenario.
"""

from __future__ import annotations

from agent.evaluation.contracts import CapabilityScenario, FileExpectation, ScenarioExpectation

CURATED_CAPABILITY_SET: tuple[CapabilityScenario, ...] = (
    CapabilityScenario(
        scenario_id="cap-read",
        capability="read/search",
        objective="CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE.",
        initial_files={"notes.txt": "CAP_READ_EVIDENCE\n"},
        expectation=ScenarioExpectation(
            files=(FileExpectation("notes.txt", contains=("CAP_READ_EVIDENCE",)),),
            unchanged_files=("notes.txt",),
            answer_contains=("CAP_READ_EVIDENCE",),
            max_steps=3,
        ),
    ),
    CapabilityScenario(
        scenario_id="cap-search",
        capability="read/search",
        objective="CAP_SEARCH: busque CAP_SEARCH_EVIDENCE no workspace.",
        initial_files={"notes.txt": "CAP_SEARCH_EVIDENCE\n"},
        expectation=ScenarioExpectation(
            unchanged_files=("notes.txt",),
            answer_contains=("CAP_SEARCH_EVIDENCE",),
            max_steps=3,
        ),
    ),
    CapabilityScenario(
        scenario_id="cap-modify-validate",
        capability="modify/validate",
        objective="CAP_MODIFY: altere sample.py e valide a modificação.",
        initial_files={"sample.py": "value = 1\n"},
        expectation=ScenarioExpectation(
            files=(FileExpectation("sample.py", contains=("value = 2",), not_contains=("value = 1",)),),
            allowed_changed_files=("sample.py",),
            answer_contains=("valid",),
            max_steps=5,
        ),
    ),
    CapabilityScenario(
        scenario_id="cap-shell",
        capability="shell",
        objective="CAP_SHELL: inspecione o histórico local e informe CAP_SHELL_EVIDENCE.",
        initial_files={"README.md": "CAP_SHELL_EVIDENCE\n"},
        expectation=ScenarioExpectation(
            unchanged_files=("README.md",),
            answer_contains=("CAP_SHELL_EVIDENCE",),
            max_steps=3,
        ),
        metadata={"requires_git_repository": True},
    ),
    CapabilityScenario(
        scenario_id="cap-extension",
        capability="external stdio",
        objective="CAP_EXTENSION: use a extensão externa e informe CAP_EXTENSION_EVIDENCE.",
        expectation=ScenarioExpectation(
            answer_contains=("CAP_EXTENSION_EVIDENCE",),
            max_steps=3,
        ),
        metadata={"requires_external_stdio": True},
    ),
    CapabilityScenario(
        scenario_id="cap-no-tool",
        capability="no-tool",
        objective="oi",
        expectation=ScenarioExpectation(success=True, answer_contains=("olá",), max_steps=0),
    ),
    CapabilityScenario(
        scenario_id="cap-failure",
        capability="failure",
        objective="CAP_FAILURE: inspecione o histórico local em um workspace sem repositório.",
        expectation=ScenarioExpectation(success=False, answer_not_contains=("sucesso",), max_steps=3),
    ),
    CapabilityScenario(
        scenario_id="cap-denial-recovery",
        capability="denial/recovery",
        objective="CAP_DENIAL: leia o caminho fora do workspace e reporte a negação.",
        initial_files={"inside.txt": "protected\n"},
        expectation=ScenarioExpectation(
            success=False,
            unchanged_files=("inside.txt",),
            answer_contains=("neg",),
            max_steps=4,
        ),
    ),
    CapabilityScenario(
        scenario_id="cap-recovery",
        capability="recovery/rollback",
        objective="CAP_RECOVERY: aplique a alteração inválida e preserve o arquivo após rollback.",
        initial_files={"sample.py": "value = 1\n"},
        expectation=ScenarioExpectation(
            success=False,
            files=(FileExpectation("sample.py", contains=("value = 1",), not_contains=("def value(:",)),),
            unchanged_files=("sample.py",),
            answer_not_contains=("validado com sucesso",),
            max_steps=5,
        ),
    ),
)


__all__ = ["CURATED_CAPABILITY_SET"]
