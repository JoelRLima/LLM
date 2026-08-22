"""Versioned H1-H12 scenario definitions for Block 7."""

from __future__ import annotations

from typing import Any, Sequence

from agent.evaluation.block7 import HSeriesArm, HSeriesScenario
from agent.evaluation.block7_scenario_h12 import h12_scenario
from agent.evaluation.contracts import ScenarioExpectation


def _expectation(
    *,
    success: bool = True,
    unchanged: Sequence[str] = (),
    files: Sequence[Any] = (),
    answer_contains: Sequence[str] = (),
    answer_not_contains: Sequence[str] = (),
    max_steps: int | None = None,
) -> ScenarioExpectation:
    return ScenarioExpectation(
        success=success,
        files=tuple(files),
        unchanged_files=tuple(unchanged),
        answer_contains=tuple(answer_contains),
        answer_not_contains=tuple(answer_not_contains),
        max_steps=max_steps,
    )


def _build_h_series() -> tuple[HSeriesScenario, ...]:
    from agent.evaluation.contracts import FileExpectation

    return (
        HSeriesScenario(
            "H1",
            "direct response vs workspace-dependent observation",
            "h1-direct-and-observed",
            (
                HSeriesArm(
                    "direct",
                    "H1_DIRECT: responda exatamente: abacaxi azul.",
                    expectation=_expectation(answer_contains=("abacaxi azul",), max_steps=0),
                    oracle={"required_tools": (), "forbidden_tools": ("file_reader", "grep")},
                ),
                HSeriesArm(
                    "workspace",
                    "H1_WORKSPACE: leia h1_observation.txt e informe H1_OBSERVED_EVIDENCE.",
                    initial_files={"h1_observation.txt": "H1_OBSERVED_EVIDENCE\n"},
                    expectation=_expectation(
                        unchanged=("h1_observation.txt",),
                        answer_contains=("H1_OBSERVED_EVIDENCE",),
                        max_steps=3,
                    ),
                    oracle={"required_tools": ("file_reader",), "forbidden_answer": ("h1-invented.txt",)},
                ),
            ),
            3,
            "The paired arms must not collapse direct response into unsupported workspace evidence.",
        ),
        HSeriesScenario(
            "H2",
            "historical scalar ResultBinding",
            "fonte_h2-scalar-binding",
            (
                HSeriesArm(
                    "scalar",
                    "H2: leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
                    initial_files={"fonte_h2.txt": "orion_584271"},
                    expectation=_expectation(
                        unchanged=("fonte_h2.txt",),
                        answer_contains=("orion_584271",),
                        max_steps=5,
                    ),
                    oracle={
                        "required_tools": ("file_reader", "grep"),
                        "binding_target": "pattern",
                        "binding_path": [],
                        "binding_target_absent_from_args": True,
                        "invalid_duplicate_must_not_execute": True,
                    },
                ),
            ),
            5,
            "Historical identity is intentionally preserved; filename is not the scalar result.",
        ),
        HSeriesScenario(
            "H3",
            "nested structured ResultBinding",
            "grep-nested-content-binding",
            (
                HSeriesArm(
                    "nested",
                    "H3: encontre H3_SOURCE_MARKER e use o texto observado para buscar a ocorrência correspondente.",
                    initial_files={
                        "h3_source.txt": "H3_SOURCE_MARKER H3_NESTED_VALUE\n",
                        "h3_target.txt": "H3_NESTED_VALUE\n",
                    },
                    expectation=_expectation(
                        unchanged=("h3_source.txt", "h3_target.txt"),
                        answer_contains=("H3_NESTED_VALUE",),
                        max_steps=5,
                    ),
                    oracle={
                        "required_tools": ("grep",),
                        "binding_target": "pattern",
                        "binding_path": [0, "content"],
                    },
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H4",
            "args and bindings exclusivity",
            "duplicate-args-bindings-fail-closed",
            (
                HSeriesArm(
                    "exclusive",
                    "H4: leia fonte_h4.txt e procure o valor observado sem duplicar args e bindings.",
                    initial_files={"fonte_h4.txt": "H4_VALUE"},
                    expectation=_expectation(
                        unchanged=("fonte_h4.txt",),
                        answer_contains=("H4_VALUE",),
                        max_steps=5,
                    ),
                    oracle={
                        "required_tools": ("file_reader", "grep"),
                        "binding_target": "pattern",
                        "binding_target_absent_from_args": True,
                        "invalid_duplicate_must_not_execute": True,
                    },
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H5",
            "semantic continuation after insufficient observation",
            "continuation-causal-evidence",
            (
                HSeriesArm(
                    "continuation",
                    "H5: leia h5_first.txt; a primeira observação não basta, então confirme h5_second.txt e responda.",
                    initial_files={
                        "h5_first.txt": "H5_FIRST_OBSERVATION_ONLY\n",
                        "h5_second.txt": "H5_FINAL_EVIDENCE\n",
                    },
                    expectation=_expectation(
                        unchanged=("h5_first.txt", "h5_second.txt"),
                        answer_contains=("H5_FINAL_EVIDENCE",),
                        max_steps=5,
                    ),
                    oracle={"required_tools": ("file_reader",), "minimum_tool_calls": 2},
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H6",
            "invalid repair fails closed",
            "invalid-repair-no-effect",
            (
                HSeriesArm(
                    "fail-closed",
                    "H6: leia h6.txt e complete a tarefa sem executar uma reparação inválida.",
                    initial_files={"h6.txt": "H6_SAFE_STATE\n"},
                    expectation=_expectation(
                        success=False,
                        unchanged=("h6.txt",),
                        answer_not_contains=("executado com sucesso", "H6_EFFECT"),
                        max_steps=5,
                    ),
                    oracle={"forbidden_tools": ("file_writer",), "invalid_repair": True},
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H7",
            "empty observation grounding",
            "empty-search-is-not-failure",
            (
                HSeriesArm(
                    "empty",
                    "H7: procure H7_EMPTY_SENTINEL no workspace e informe honestamente se nada foi encontrado.",
                    initial_files={"h7.txt": "H7_OTHER_VALUE\n"},
                    expectation=_expectation(
                        unchanged=("h7.txt",),
                        answer_contains=("H7_EMPTY_SENTINEL",),
                        answer_not_contains=("h7-invented.txt", "H7_SUCCESS_EFFECT"),
                        max_steps=3,
                    ),
                    oracle={
                        "required_tools": ("grep",),
                        "required_observation": {"present": True, "value": [], "truncated": False},
                        "empty_is_not_failure": True,
                        "grounding_kind": "empty",
                    },
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H8",
            "controlled tool failure grounding",
            "failed-tool-is-not-empty",
            (
                HSeriesArm(
                    "failure",
                    "H8: execute uma busca com regex [ inválida e relate a falha real sem inventar resultado.",
                    initial_files={"h8.txt": "H8_SAFE_VALUE\n"},
                    expectation=_expectation(
                        success=False,
                        unchanged=("h8.txt",),
                        answer_contains=(),
                        answer_not_contains=("H8_SUCCESS_SENTINEL",),
                        max_steps=3,
                    ),
                    oracle={
                        "required_tools": ("grep",),
                        "required_status": "failed",
                        "empty_is_not_failure": True,
                        "grounding_kind": "tool_failure",
                    },
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H9",
            "truncation and completeness grounding",
            "bounded-search-discloses-truncation",
            (
                HSeriesArm(
                    "truncated",
                    "H9: busque H9_TRUNCATED_SENTINEL, limite a observação e informe se ela foi truncada.",
                    initial_files={
                        "h9_one.txt": "H9_TRUNCATED_SENTINEL\n",
                        "h9_two.txt": "H9_TRUNCATED_SENTINEL\n",
                    },
                    expectation=_expectation(
                        unchanged=("h9_one.txt", "h9_two.txt"),
                        answer_contains=("trunc",),
                        answer_not_contains=("todas as ocorrências", "exhaustivo"),
                        max_steps=3,
                    ),
                    oracle={
                        "required_tools": ("grep",),
                        "required_observation": {"truncated": True, "complete": False},
                        "grounding_kind": "truncated",
                    },
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H10",
            "conditional effect with false predicate",
            "false-condition-no-effect",
            (
                HSeriesArm(
                    "false",
                    "H10: se h10_condition.txt contiver H10_TRUE, crie h10_effect.txt; caso contrário não altere nada.",
                    initial_files={"h10_condition.txt": "H10_FALSE\n"},
                    expectation=_expectation(
                        unchanged=("h10_condition.txt",),
                        files=(FileExpectation("h10_effect.txt", exists=False),),
                        answer_contains=("escrita",),
                        answer_not_contains=("H10_EFFECT",),
                        max_steps=4,
                    ),
                    oracle={"forbidden_effects": ("write",), "condition": False},
                ),
            ),
            3,
        ),
        HSeriesScenario(
            "H11",
            "hierarchical partial-failure public truth",
            "hierarchical-partial-failure",
            (
                HSeriesArm(
                    "partial-failure",
                    "H11: analise todos os arquivos e depois leia h11_missing.txt; relate a falha parcial como falha da tarefa.",
                    initial_files={"h11_present.txt": "H11_PRESENT\n"},
                    expectation=_expectation(
                        success=False,
                        unchanged=("h11_present.txt",),
                        answer_not_contains=("concluída com sucesso", "H11_FULL_SUCCESS"),
                        max_steps=6,
                    ),
                    oracle={"required_route": "hierarchical", "required_terminal_status": "failed"},
                ),
            ),
            3,
        ),
        h12_scenario(),
    )
H_SERIES: tuple[HSeriesScenario, ...] = _build_h_series()
