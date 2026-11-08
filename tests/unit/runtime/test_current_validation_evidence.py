from __future__ import annotations

from types import SimpleNamespace

from llm_agent.agent.reporting.run_projection_facts import build_run_projection_facts
from llm_agent.agent.reporting.run_receipt_builder import build_run_receipt
from llm_agent.agent.runtime.outcome_taxonomy import OperationalStatus
from llm_agent.agent.runtime.validation_evidence import (
    _result_explicitly_succeeded,
    current_validation_evidence,
)
from llm_agent.agent.state import AgentState
from llm_agent.agent.tools.contracts import ToolResult, ToolStatus
from llm_agent.agent.tools.result_adapter import result_explicitly_succeeded


def _artifact(path, validation):
    return {"metadata": {
        "mutation_occurred": True,
        "applied": True,
        "final_state": "applied",
        "affected_files": [path],
        "validation_status": validation,
    }}


def _result(path="a.py", validation="passed"):
    return ToolResult(
        invocation_id="test-validation",
        status=OperationalStatus.SUCCEEDED,
        artifacts=(_artifact(path, validation),),
    )


def test_runtime_success_predicate_does_not_accept_legacy_mappings():
    legacy_mappings = (
        {"ok": True, "status": "success"},
        {"ok": True, "status": "failed"},
        {"ok": False, "status": "succeeded"},
        {"status": "succeeded"},
        {"ok": True},
    )

    for result in legacy_mappings:
        assert not _result_explicitly_succeeded(result)

    attribute_result = SimpleNamespace(ok=True, status=ToolStatus.SUCCEEDED)
    assert _result_explicitly_succeeded(attribute_result)
    assert _result_explicitly_succeeded(attribute_result) is result_explicitly_succeeded(attribute_result)
    typed = _result()
    assert _result_explicitly_succeeded(typed)
    assert _result_explicitly_succeeded(typed) is result_explicitly_succeeded(typed)


def test_later_observation_supersedes_only_the_resources_it_touches():
    evidence = current_validation_evidence([
        _result("a.py", "failed"), _result("b.py"), _result("a.py"),
    ])

    assert evidence.satisfied
    assert {item.resource: item.observation_index for item in evidence.resources} == {"a.py": 2, "b.py": 1}


def test_validation_metadata_cannot_infer_success_from_incomplete_legacy_result():
    for missing in ("ok", "status"):
        result = {"ok": True, "status": "succeeded", "artifacts": [_artifact("a.py", "passed")]}
        result.pop(missing)
        evidence = current_validation_evidence([result])
        assert not evidence.satisfied
        assert evidence.summary()["outcome"] == "unverified"


def test_conflicting_artifacts_in_one_invocation_are_order_independent():
    passed, missing = _artifact("a.py", "passed"), _artifact("a.py", None)
    for artifacts in ([passed, missing], [missing, passed]):
        result = ToolResult(
            invocation_id="test-conflicting-artifacts",
            status=OperationalStatus.SUCCEEDED,
            artifacts=tuple(artifacts),
        )
        evidence = current_validation_evidence([result])
        assert not evidence.satisfied
        assert evidence.summary()["outcome"] == "unverified"


def test_unknown_mutation_scope_cannot_borrow_later_unrelated_validation():
    unknown = {"status": "succeeded", "metadata": {
        "mutation_occurred": True, "applied": True, "final_state": "applied", "validation_status": "passed",
    }}

    evidence = current_validation_evidence([unknown, _result()])

    assert evidence.unknown_footprint
    assert not evidence.satisfied
    assert evidence.summary()["outcome"] == "unverified"


def test_receipt_and_snapshot_do_not_report_stale_validation(tmp_path):
    state = AgentState()
    state.tool_history = [
        {"tool": "code_task", "result": _result()},
        {"tool": "code_task", "result": _result(validation=None)},
    ]

    facts = build_run_projection_facts(state, observed_at="2026-10-26T00:00:00Z")
    receipt = build_run_receipt(tmp_path, state, "blocked", None)

    assert dict(facts.validation) == receipt["validation"] == {"ran": True, "outcome": "unverified"}


def test_validation_considers_history_beyond_the_bounded_presentation_sample():
    state = AgentState()
    state.tool_history = [{"tool": "code_task", "result": _result()}]
    state.tool_history.extend({"tool": "reader", "result": {"status": "succeeded"}} for _ in range(50))
    state.tool_history.append({"tool": "code_task", "result": _result(validation=None)})

    facts = build_run_projection_facts(state, observed_at="2026-10-26T00:00:00Z")

    assert len(facts.tools) == 50
    assert facts.validation["outcome"] == "unverified"


def test_projection_uses_latest_facts_beyond_bounded_history_sample():
    history = [
        {
            "tool": "code_task",
            "args": {"action": "inspect"},
            "result": {
                "ok": True,
                "status": "succeeded",
                "data": {
                    "metadata": {
                        "total_chars": 1,
                        "code_outcome": {"kind": "OLD"},
                        "validation_metadata": {"effective_status": "old"},
                    }
                },
            },
        }
        for _ in range(50)
    ]
    latest_data = {
        "metadata": {
            "total_chars": 987,
            "truncated": True,
            "code_outcome": {
                "kind": "APPLIED",
                "verification": "SUPPORTED",
                "cited_evidence_ids": ["final-evidence"],
            },
            "validation_metadata": {
                "execution_status": "passed",
                "effective_status": "passed",
                "tests_requested": True,
                "test_coverage": "full",
                "plan_fingerprint": "final-plan",
                "selections": [
                    {
                        "command_kind": "pytest",
                        "scope": "changed_files",
                        "targets": ["final.py"],
                        "status": "passed",
                    }
                ],
            },
        }
    }
    latest_result = {
        "ok": True,
        "status": "succeeded",
        "data": latest_data,
    }
    history.append(
        {
            "tool": "code_task",
            "args": {"action": "repair"},
            "result": latest_result,
        }
    )
    state = AgentState()
    state.tool_history = history

    facts = build_run_projection_facts(state, observed_at="2026-10-26T00:00:00Z")

    assert len(facts.tools) == len(facts.report_steps) == 50
    assert facts.repair_count == 1
    assert facts.output_chars == 987
    assert facts.output_truncated is True
    assert facts.code_outcome["kind"] == "APPLIED"
    assert facts.code_outcome["evidence_ids"] == ("final-evidence",)
    assert facts.validation_detail["effective_status"] == "passed"
    assert facts.validation_detail["plan_fingerprint"] == "final-plan"

    latest_data["metadata"]["code_outcome"]["kind"] = "CHANGED"
    latest_data["metadata"]["validation_metadata"]["plan_fingerprint"] = "changed"
    assert facts.code_outcome["kind"] == "APPLIED"
    assert facts.validation_detail["plan_fingerprint"] == "final-plan"
