"""Small independent-oracle tasks for weak-model evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any

from llm_agent.agent.evaluation.contracts import CapabilityScenario, FileExpectation, ScenarioExpectation
from llm_agent.agent.evaluation.practical_scenarios import PRACTICAL_V1
from llm_agent.agent.evaluation.real_model_readiness import real_model_readiness_scenarios
from llm_agent.agent.evaluation.scenario_contracts import H_SERIES
from llm_agent.agent.evaluation.scripted_gateway import ScriptedEvaluationGateway
from llm_agent.agent.llm.contracts import ModelResponse
from llm_agent.agent.llm.decision_contract import ModelRequestContract

WEAK_MODEL_FIXTURE_VERSION = "weak-model-v2"

_BY_ID = {scenario.scenario_id: scenario for scenario in PRACTICAL_V1}
# Reuse these frozen, independently graded tasks because the canonical
# scripted backend has explicit plans for each one. This keeps offline runs
# useful for plumbing and evidence checks without claiming model accuracy.
_PRACTICAL_FIXTURES = tuple(
    _BY_ID[scenario_id] for scenario_id in (
        "PV1-01", "PV1-02", "PV1-03", "PV1-04", "PV1-05", "PV1-06", "PV1-08"
    )
)
_READINESS_FIXTURES = tuple(
    replace(
        scenario,
        metadata={**scenario.metadata, "oracle": {
            **scenario.metadata.get("oracle", {}),
            "kind": "capability_expectation",
            "source_fixture": scenario.metadata["source_fixture_id"],
            "oracle_validity": "deterministic_capability_evaluator",
        }},
    )
    for scenario in real_model_readiness_scenarios()
)
_H1_DIRECT_SOURCE = next(item for item in H_SERIES if item.h_id == "H1")
_H1_DIRECT = next(item for item in _H1_DIRECT_SOURCE.arms if item.arm_id == "direct").to_capability_scenario("H1")
_H1_DIRECT = replace(
    _H1_DIRECT,
    scenario_id="h1-direct-no-tool",
    metadata={
        **_H1_DIRECT.metadata,
        "oracle": {**_H1_DIRECT.metadata.get("oracle", {}),
                   "kind": "capability_expectation", "source_fixture": _H1_DIRECT_SOURCE.fixture_id,
                   "oracle_validity": "deterministic_capability_evaluator",
                   "forbidden_tools": ("file_reader", "grep")},
    },
)
_H11_SOURCE = next(item for item in H_SERIES if item.h_id == "H11")
_H11_PARTIAL = next(item for item in _H11_SOURCE.arms if item.arm_id == "partial-failure").to_capability_scenario("H11")
_H11_PARTIAL = replace(
    _H11_PARTIAL,
    scenario_id="h11-partial-failure",
    metadata={
        **_H11_PARTIAL.metadata,
        "oracle": {**_H11_PARTIAL.metadata.get("oracle", {}),
                   "kind": "capability_expectation", "source_fixture": _H11_SOURCE.fixture_id,
                   "oracle_validity": "deterministic_capability_evaluator",
                   "required_terminal_status": "failed"},
    },
)
WEAK_MODEL_FIXTURES: tuple[CapabilityScenario, ...] = _PRACTICAL_FIXTURES + _READINESS_FIXTURES + (_H1_DIRECT, _H11_PARTIAL) + (
    CapabilityScenario(
        "WM-JSON", "json-transform",
        "Set enabled to true in `settings.json`, preserve every other value, and validate it with the supplied test.",
        {
            "settings.json": '{"enabled":false,"retries":2}\n',
            "tests/test_settings.py": (
                "import json\nfrom pathlib import Path\n\ndef test_settings_transform() -> None:\n"
                "    data = json.loads((Path(__file__).parents[1] / 'settings.json').read_text())\n"
                "    assert data == {'enabled': True, 'retries': 2}\n"
            ),
        },
        ScenarioExpectation(
            files=(FileExpectation("settings.json"),),
            unchanged_files=("tests/test_settings.py",),
            allowed_changed_files=("settings.json",),
        ),
        {"approval_mode": "explicit", "oracle": {"file": "settings.json", "enabled": True, "retries": 2}},
    ),
    CapabilityScenario(
        "WM-CSV", "csv-transform",
        "Add a passed column to `scores.csv`: true for scores at least 70, false otherwise, then run the supplied test.",
        {
            "scores.csv": "name,score\nAda,90\nLin,62\n",
            "tests/test_scores.py": (
                "import csv\nfrom pathlib import Path\n\ndef test_score_flags() -> None:\n"
                "    with (Path(__file__).parents[1] / 'scores.csv').open(newline='') as handle:\n"
                "        rows = list(csv.DictReader(handle))\n"
                "    assert [(row['name'], row['passed']) for row in rows] == [('Ada', 'true'), ('Lin', 'false')]\n"
            ),
        },
        ScenarioExpectation(
            files=(FileExpectation("scores.csv"),),
            unchanged_files=("tests/test_scores.py",),
            allowed_changed_files=("scores.csv",),
        ),
        {"approval_mode": "explicit", "oracle": {"file": "scores.csv", "threshold": 70, "rows": 2}},
    ),
    CapabilityScenario(
        "WM-REPORT", "report-artifact",
        "Create `summary.md` from `metrics.json` with passed, failed, total, rate, and suite, then validate it with the supplied test.",
        {
            "metrics.json": '{"passed":3,"failed":1,"suite":"unit"}\n',
            "tests/test_summary.py": (
                "from pathlib import Path\n\ndef test_summary_contains_reported_metrics() -> None:\n"
                "    report = (Path(__file__).parents[1] / 'summary.md').read_text()\n"
                "    for fact in ('passed: 3', 'failed: 1', 'total: 4', 'rate: 0.75', 'suite: unit'):\n"
                "        assert fact in report\n"
            ),
        },
        ScenarioExpectation(
            files=(FileExpectation("summary.md"),),
            unchanged_files=("metrics.json", "tests/test_summary.py"),
            allowed_changed_files=("summary.md",),
        ),
        {"approval_mode": "explicit", "oracle": {"file": "summary.md", "passed": 3, "failed": 1, "total": 4, "rate": 0.75, "suite": "unit"}},
    ),
)

_TASK_CLASSES = {
    "PV1-01": "valid_idempotent_no_change",
    "PV1-02": "valid_code_change",
    "PV1-03": "missing_arguments_need_clarification",
    "PV1-04": "valid_targeted_code_change",
    "PV1-05": "fresh_read_over_stale_context",
    "PV1-06": "guidance_scope_and_code_change",
    "PV1-08": "valid_targeted_test",
    "r1-": "multi_step_result_binding",
    "r2-": "multi_step_result_binding",
    "r3-": "exclusive_arguments_and_bindings",
    "r4-": "multi_step_continuation",
    "r5-": "unauthorized_scope",
    "r6-": "empty_observation_grounding",
    "r7-": "tool_failure_partial_progress",
    "r8-": "fresh_read_over_stale_context",
    "r9-": "tool_failure_partial_progress",
    "r10-": "tool_failure_no_effect_wrong_final_success",
    "h1-direct-no-tool": "direct_answer_no_irrelevant_tool",
    "h11-partial-failure": "partial_task_failure_public_truth",
}
WEAK_MODEL_FIXTURES = tuple(
    replace(
        scenario,
        metadata={
            **scenario.metadata,
            "task_class": next(
                (value for key, value in _TASK_CLASSES.items() if scenario.scenario_id == key or scenario.scenario_id.startswith(key)),
                "multi_step_result_binding" if scenario.scenario_id == "WM-REPORT" else "data_transform",
            ),
            "oracle_validity": "independent_data_oracle" if scenario.scenario_id.startswith("WM-") else "deterministic_capability_evaluator",
        },
    )
    for scenario in WEAK_MODEL_FIXTURES
)


class WeakModelScriptedGateway(ScriptedEvaluationGateway):
    """Canonical fixture model responses for tasks without legacy scripted plans."""

    _FILES = {
        "WM-JSON": ("settings.json", "modify", '{"enabled":true,"retries":2}\n'),
        "WM-CSV": ("scores.csv", "modify", "name,score,passed\nAda,90,true\nLin,62,false\n"),
        "WM-REPORT": (
            "summary.md", "create",
            "# Unit test summary\n\npassed: 3\nfailed: 1\ntotal: 4\nrate: 0.75\nsuite: unit\n",
        ),
    }

    def __init__(self, objective: str, *, fixture_marker: str | None = None) -> None:
        super().__init__(objective, fixture_marker=fixture_marker)
        self._reactive_calls = 0
        if self.fixture_marker in self._FILES:
            self.supports_semantic_intent = True

    def complete(self, request: Any) -> ModelResponse:
        contract = getattr(request.request_contract, "value", request.request_contract)
        if contract == ModelRequestContract.REACTIVE_TOOL_DECISION.value:
            self._reactive_calls += 1
            if not self.fixture_marker.startswith("PV1-") and self.fixture_marker not in self._FILES:
                return super()._complete_scripted(request)
            if self.fixture_marker == "PV1-05":
                content = (
                    '{"action":"tool","tool":"file_reader","args":{"file_path":"feature.py"}}'
                    if self._reactive_calls == 1
                    else '{"action":"final","answer":"O valor atual de MODE é new."}'
                )
            elif self.fixture_marker in self._FILES:
                path, kind, _ = self._FILES[self.fixture_marker]
                content = json.dumps({
                    "action": "tool", "tool": "code_task",
                    "args": {
                        "action": "generate" if kind == "create" else "modify",
                        "targets": [path], "objective": self.objective, "include_tests": True,
                    },
                })
            else:
                content = '{"action":"final","answer":"A observação solicitada foi concluída."}'
            self.calls.append(request)
            return ModelResponse(
                content=content,
                provider_metadata={"observed_provider_model_id": self.provider_model_id},
            )
        return super()._complete_scripted(request)

    def _plan_response(self, prompt: str) -> str:
        if self.fixture_marker not in self._FILES:
            return str(super()._plan_response(prompt))
        path = self._FILES[self.fixture_marker][0]
        return json.dumps({"action": "use_tools", "plan": [{
            "tool": "code_task",
            "args": {
                "action": "generate" if self._FILES[self.fixture_marker][1] == "create" else "modify",
                "targets": [path],
                "objective": self.objective,
                "include_tests": True,
            },
        }]})

    def _response(self, system: str, prompt: str) -> str:
        if self.fixture_marker not in self._FILES:
            return str(super()._response(system, prompt))
        if "You are a Router Agent" in system:
            return '{"persona":"coder"}'
        if "TOOL DISCOVERY" in prompt:
            from llm_agent.agent.evaluation.scripted_tool_guidance import selection_response
            return str(selection_response(prompt, required_tools=("code_task",)))
        if "Escolha exatamente uma das duas respostas JSON" in prompt:
            return self._plan_response(prompt)
        if "Objetivo de engenharia:" in prompt:
            path, kind, content = self._FILES[self.fixture_marker]
            if self.fixture_marker == "WM-JSON":
                change: dict[str, Any] = {
                    "path": path,
                    "kind": "edit",
                    "edits": [{"operation": "replace", "start_line": 1, "end_line": 1,
                               "content": content.rstrip("\n")}],
                }
            else:
                change = {"path": path, "kind": kind, "content": content}
            return json.dumps({
                "decision": "CHANGE", "rationale": "A alteração corresponde ao pedido e aos dados observados.",
                "reason_code": "NONE", "question": "", "changes": [change],
            })
        if "Uma fronteira sem" in prompt:
            return '{"action":"complete","reason":"os fatos observados bastam"}'
        if "Resultados das ferramentas executadas:" in prompt:
            return "A transformação solicitada foi concluída e conferida no workspace."
        return str(super()._response(system, prompt))


def fixture_fingerprint() -> str:
    payload = [
        {"id": item.scenario_id, "objective": item.objective,
         "files": item.initial_files, "expectation": asdict(item.expectation), "oracle": item.metadata}
        for item in WEAK_MODEL_FIXTURES
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def fixture_oracle(scenario: CapabilityScenario) -> dict[str, Any]:
    declared = scenario.metadata.get("oracle")
    if isinstance(declared, dict) and declared:
        result = dict(declared)
    else:
        result = {
            "kind": "capability_expectation",
            "source_fixture": scenario.metadata.get("source_fixture_id", scenario.scenario_id),
            "expectation": asdict(scenario.expectation),
            "oracle_validity": "deterministic_capability_evaluator",
        }
    result["task_class"] = scenario.metadata.get("task_class", "unclassified")
    return result
