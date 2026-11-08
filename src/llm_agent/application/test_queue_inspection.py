"""Test-queue inspection and report publication; no model or process startup."""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from llm_agent.application.test_queue import TestQueue
from llm_agent.application.test_queue_evidence import read_case_evidence
from llm_agent.application.test_queue_manifest import TestCase
from llm_agent.filesystem.path_safety import assert_no_link_ancestors
from llm_agent.storage.json_persistence import write_text_atomic


def export_report(queue: TestQueue, content: str, output: Path | None = None) -> Path:
    """Publish an interface-rendered report without overwriting test artifacts."""
    destination = (output or queue.root / "report.html").absolute()
    assert_no_link_ancestors(destination)
    destination = destination.resolve()
    if destination.suffix.lower() != ".html":
        raise ValueError("Report output must end in .html")
    if destination.is_relative_to(queue.root) and destination != queue.root / "report.html" and not destination.is_relative_to(queue.root / "reports"):
        raise ValueError("Inside a queue, reports must use report.html or the reports/ directory")
    write_text_atomic(destination, content)
    return destination


def _valid_case_summaries(state: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    summaries = state.get("case_summaries")
    order = state.get("order")
    results = state.get("results")
    if not isinstance(summaries, dict) or not isinstance(order, list) or not isinstance(results, dict):
        return None
    if set(summaries) != set(order) or set(results) != set(order):
        return None
    normalized: dict[str, dict[str, int]] = {}
    for case_id in order:
        row = summaries.get(case_id)
        if not isinstance(case_id, str) or not isinstance(row, dict):
            return None
        values = (row.get("initial_files"), row.get("checks"), row.get("python_verifiers"))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            return None
        initial_files, checks, python_verifiers = (cast(int, value) for value in values)
        if python_verifiers > checks:
            return None
        normalized[case_id] = {
            "initial_files": initial_files,
            "checks": checks,
            "python_verifiers": python_verifiers,
        }
    return normalized


def inspect_queue(
    queue: TestQueue,
    *,
    include_details: bool = False,
    validate_manifest: bool = True,
) -> dict[str, Any]:
    state = queue.status()
    summaries = None if validate_manifest else _valid_case_summaries(state)
    manifest_validation = "deferred_to_action_or_execution" if summaries is not None else "verified"
    definitions: dict[str, TestCase] = {}
    if summaries is None:
        if validate_manifest:
            cases, _validated = queue._cases_for_state(state), True
        else:
            cases, validated = queue.status_cases(state)
            if not validated:
                manifest_validation = "deferred_to_action_or_execution"
        definitions = {case.case_id: case for case in cases}
    counts: dict[str, int] = {}
    rows = []
    for case_id in state["order"]:
        row = state["results"][case_id]
        summary = summaries.get(case_id) if summaries is not None else None
        if summary is None:
            case = definitions[case_id]
            summary = {
                "initial_files": len(case.files),
                "checks": len(case.checks),
                "python_verifiers": sum(check["kind"] == "python" for check in case.checks),
            }
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        rows.append({"id": case_id, "status": row["status"], **summary})
    result = {"queue": str(queue.root), "status": state["status"], "runner_active": state["runner_active"],
            "elapsed_seconds": state["elapsed_seconds"], "counts": counts, "cases": rows,
            "without_checks": [row["id"] for row in rows if row["checks"] == 0],
            "not_imported": state.get("not_imported", []), "identity": state["identity"],
            "manifest_hash": state["manifest_hash"], "execution_options": state.get("execution_options"),
            "manifest_validation": manifest_validation}
    if include_details:
        result["results"] = [_evidence(queue, case_id, state["results"][case_id]) for case_id in state["order"]]
    return result


def case_details(queue: TestQueue, case_id: str) -> dict[str, Any]:
    """Read only canonical evidence paths; never follow paths supplied in JSON."""
    definitions = {case.case_id: case for case in queue.cases()}
    if case_id not in definitions:
        raise ValueError("Unknown test case")
    state = queue.status()
    return _evidence(queue, case_id, state["results"][case_id])


def _evidence(queue: TestQueue, case_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {"id": case_id, **read_case_evidence(queue.root, case_id, row)}
