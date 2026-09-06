"""Projection and deterministic oracle helpers for practical evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.contracts import ExecutionObservation
from agent.evaluation.scripted_gateway import ScriptedEvaluationGateway
from agent.tools.result_adapter import result_data


def _walk(value: Any, depth: int = 0) -> Sequence[Mapping[str, Any]]:
    if depth > 32:
        return ()
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        found.append(value)
        for child in list(value.values())[:128]:
            found.extend(_walk(child, depth + 1))
    elif isinstance(value, (list, tuple)):
        for child in list(value)[:128]:
            found.extend(_walk(child, depth + 1))
    return tuple(found)


def _named_records(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        candidate
        for candidate in _walk(value)
        if isinstance(candidate.get(name), Mapping)
    )


def _first_named(value: Any, name: str) -> Mapping[str, Any] | None:
    records = _named_records(value, name)
    return records[0].get(name) if records else None


def _invocations(observation: ExecutionObservation) -> tuple[Mapping[str, Any], ...]:
    raw = observation.evidence.get("invocation_evidence", ())
    return tuple(item for item in raw if isinstance(item, Mapping))


def _tool_data(invocation: Mapping[str, Any]) -> Any:
    result = invocation.get("result")
    return result_data(result) if isinstance(result, Mapping) else None


def _all_text(value: Any) -> str:
    pieces: list[str] = []
    for record in _walk(value):
        for item in record.values():
            if isinstance(item, str):
                pieces.append(item)
    return "\n".join(pieces)


def _prompt_projection(gateway: ScriptedEvaluationGateway | None) -> dict[str, Any]:
    if gateway is None:
        return {
            "request_count": 0,
            "untrusted_project_guidance_present": False,
            "root_guidance_present": False,
            "src_guidance_present": False,
            "tests_guidance_present": False,
            "trusted_system_contains_guidance": False,
            "stale_memory_text_present": False,
            "current_memory_text_present": False,
        }
    requests = tuple(gateway.calls)
    all_messages = [
        str(message.content)
        for request in requests
        for message in request.messages
    ]
    all_text = "\n".join(all_messages)
    system_text = "\n".join(
        str(request.messages[0].content)
        for request in requests
        if request.messages
    )
    return {
        "request_count": len(requests),
        "untrusted_project_guidance_present": "UNTRUSTED_PROJECT_GUIDANCE" in all_text,
        "root_guidance_present": "ROOT_GUIDANCE_SENTINEL" in all_text,
        "src_guidance_present": "SRC_GUIDANCE_SENTINEL" in all_text,
        "tests_guidance_present": "UNRELATED_TEST_GUIDANCE_SENTINEL" in all_text,
        "trusted_system_contains_guidance": any(
            token in system_text
            for token in (
                "ROOT_GUIDANCE_SENTINEL",
                "SRC_GUIDANCE_SENTINEL",
                "UNRELATED_TEST_GUIDANCE_SENTINEL",
            )
        ),
        "stale_memory_text_present": "MODE atual é old" in all_text,
        "current_memory_text_present": "MODE = \"new\"" in all_text,
    }


def _validation_projection(observation: ExecutionObservation) -> dict[str, Any]:
    record = observation.evidence.get("validation_detail")
    if isinstance(record, Mapping):
        selections = record.get("selections")
        return {
            "execution_status": record.get("execution_status"),
            "effective_status": record.get("effective_status"),
            "tests_requested": record.get("tests_requested") is True,
            "test_coverage": record.get("test_coverage"),
            "selections": [
                dict(item) for item in selections if isinstance(item, Mapping)
            ]
            if isinstance(selections, (list, tuple))
            else [],
            "plan_fingerprint": record.get("plan_fingerprint"),
        }
    return {"tests_requested": False, "test_coverage": None, "selections": []}


def _code_projection(observation: ExecutionObservation) -> dict[str, Any]:
    code_outcome = observation.evidence.get("code_outcome")
    if not isinstance(code_outcome, Mapping):
        code_outcome = {}
    reason_code = code_outcome.get("reason_code")
    failure_code = code_outcome.get("failure_code")
    mutation_values = [
        {
            key: code_outcome.get(key)
            for key in (
                "mutation_attempted",
                "mutation_occurred",
                "persisted_mutation",
                "surviving_mutation",
                "affected_files",
            )
            if key in code_outcome
        }
    ] if any(
        key in code_outcome
        for key in ("mutation_attempted", "mutation_occurred", "persisted_mutation")
    ) else []
    return {
        "proposal_kind": code_outcome.get("kind"),
        "verifier_verdict": code_outcome.get("verification"),
        "proposal_reason_codes": [reason_code] if isinstance(reason_code, str) else [],
        "failure_codes": [failure_code] if isinstance(failure_code, str) else [],
        "mutation_records": mutation_values[:16],
    }


def _repository_projection(observation: ExecutionObservation) -> dict[str, Any]:
    invocations = _invocations(observation)
    repository = next(
        (
            _tool_data(item)
            for item in invocations
            if item.get("tool") == "repository_state"
        ),
        None,
    )
    return {
        "tool_observed": any(item.get("tool") == "repository_state" for item in invocations),
        "data": repository if isinstance(repository, Mapping) else None,
        "raw_diff_exposed": "diff" in _all_text(repository).casefold(),
    }


def _oracle_pv1_01(code: Mapping[str, Any], report: Any) -> list[str]:
    failures: list[str] = []
    if code["proposal_kind"] != "NO_CHANGE":
        failures.append("proposal_not_no_change")
    if code["verifier_verdict"] != "SUPPORTED":
        failures.append("verifier_not_supported")
    if report.changed_files:
        failures.append("resolved_issue_mutated_workspace")
    if not any(
        record.get("mutation_occurred") is False
        and record.get("persisted_mutation") is False
        and record.get("surviving_mutation") is False
        for record in code["mutation_records"]
    ):
        failures.append("no_change_mutation_evidence_missing")
    return failures


def _oracle_pv1_02(code: Mapping[str, Any], report: Any) -> list[str]:
    failures: list[str] = []
    if code["proposal_kind"] == "NO_CHANGE":
        failures.append("partial_fix_falsely_abstained")
    if report.changed_files != ("parser.py",):
        failures.append("partial_fix_scope_invalid")
    return failures


def _oracle_pv1_03(
    code: Mapping[str, Any],
    observation: ExecutionObservation,
    report: Any,
) -> list[str]:
    failures: list[str] = []
    if code["proposal_kind"] != "NEEDS_INPUT":
        failures.append("preference_did_not_request_input")
    if "USER_CHOICE_REQUIRED" not in code["proposal_reason_codes"]:
        failures.append("preference_reason_invalid")
    if code["verifier_verdict"] != "SUPPORTED":
        failures.append("preference_verifier_not_supported")
    if observation.measurement.get("status") != "blocked":
        failures.append("preference_not_blocked")
    if "CODE_INPUT_REQUIRED" not in code["failure_codes"] and observation.error != "CODE_INPUT_REQUIRED":
        failures.append("preference_failure_code_missing")
    if report.changed_files:
        failures.append("preference_mutated_workspace")
    return failures


def _oracle_pv1_04(observation: ExecutionObservation, report: Any) -> list[str]:
    failures: list[str] = []
    if "CODE_INPUT_REQUIRED" in _code_projection(observation)["failure_codes"] or observation.error == "CODE_INPUT_REQUIRED":
        failures.append("repository_answer_requested_input")
    if report.changed_files != ("src/settings.py",):
        failures.append("repository_answer_scope_invalid")
    return failures


def _oracle_pv1_05(
    observation: ExecutionObservation,
    report: Any,
    prompt: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    if "new" not in observation.answer.casefold() or "old" in observation.answer.casefold():
        failures.append("stale_memory_answer_invalid")
    if prompt.get("stale_memory_text_present"):
        failures.append("stale_memory_projected_as_current")
    if report.changed_files:
        failures.append("read_task_mutated_workspace")
    return failures


def _oracle_pv1_06(report: Any, prompt: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if not prompt.get("untrusted_project_guidance_present"):
        failures.append("scoped_guidance_missing")
    if not prompt.get("root_guidance_present") or not prompt.get("src_guidance_present"):
        failures.append("scoped_guidance_records_incomplete")
    if prompt.get("tests_guidance_present"):
        failures.append("unrelated_guidance_included")
    if prompt.get("trusted_system_contains_guidance"):
        failures.append("guidance_entered_trusted_system")
    if report.changed_files != ("src/module.py",):
        failures.append("scoped_guidance_mutation_scope_invalid")
    return failures


def _oracle_pv1_07(repository: Mapping[str, Any], report: Any) -> list[str]:
    failures: list[str] = []
    if not repository["tool_observed"]:
        failures.append("repository_state_not_observed")
    if report.changed_files != ("app.py",):
        failures.append("dirty_repository_mutation_scope_invalid")
    if repository["raw_diff_exposed"]:
        failures.append("raw_diff_exposed")
    return failures


def _oracle_pv1_08(validation: Mapping[str, Any], report: Any) -> list[str]:
    failures: list[str] = []
    if report.changed_files != ("src/math_ops.py",):
        failures.append("targeted_validation_mutation_scope_invalid")
    if validation.get("tests_requested") is not True:
        failures.append("targeted_validation_not_requested")
    selections = validation.get("selections", ())
    pytest_selections = [item for item in selections if item.get("command_kind") == "pytest"]
    if len(pytest_selections) != 1:
        failures.append("pytest_selection_missing")
    else:
        selected = pytest_selections[0]
        if selected.get("scope") != "targeted_tests":
            failures.append("validation_scope_not_targeted")
        if selected.get("targets") != ["tests/test_math_ops.py"]:
            failures.append("relevant_test_not_selected")
        if "tests/test_unrelated.py" in selected.get("targets", ()):
            failures.append("unrelated_test_selected")
    return failures


__all__ = ["_prompt_projection"]
