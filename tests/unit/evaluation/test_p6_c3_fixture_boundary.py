from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent.code.outcome_verifier import (
    MAX_VERIFIER_EVIDENCE_IDS,
    CodeEvidenceManifest,
    CodeEvidenceRecord,
    CodeOutcomeVerdict,
    CodeOutcomeVerifier,
    CodeProposalKind,
    CodeProposalReasonCode,
)
from agent.evaluation.agent_executor import snapshot_evaluation_projection
from agent.evaluation.contracts import CapabilityScenario, ExecutionObservation, ScenarioExpectation
from agent.evaluation.practical_scenarios import (
    PRACTICAL_V1,
    prepare_practical_application,
    prepare_practical_workspace,
)
from agent.evaluation.runner import CapabilityEvaluator
from agent.evaluation.scripted_gateway import (
    ScriptedEvaluationGateway,
    _select_verifier_evidence,
)
from agent.evaluation.scripted_gateway_logic import scripted_required_tools, scripted_response
from agent.evaluation.scripted_tool_guidance import selection_response
from agent.llm.context_projection import render_untrusted_context_envelope
from agent.planning.plan_model import Plan
from agent.reporting.metrics import project_run_metrics
from agent.reporting.run_snapshot import build_canonical_run_snapshot
from agent.runtime.correlation import RunCorrelation
from agent.runtime.paths import AppPaths
from agent.state import AgentState


class _BoundaryExecutor:
    def __init__(self) -> None:
        self.prepared: list[str] = []

    def prepare_workspace(self, objective: str, workspace: Path) -> None:
        self.prepared.append(objective)
        (workspace / "fixture.txt").write_bytes(b"fixture established before measurement\n")

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        del objective
        (workspace / "agent.txt").write_bytes(b"agent mutation\n")
        return ExecutionObservation(success=True)


def test_workspace_fixture_preparation_is_before_snapshot_and_agent_change_is_detected(
    tmp_path: Path,
) -> None:
    executor = _BoundaryExecutor()
    scenario = CapabilityScenario(
        "fixture-boundary",
        "read/write",
        "establish fixture then mutate",
        initial_files={"initial.txt": "initial\n"},
        expectation=ScenarioExpectation(allowed_changed_files=("agent.txt",)),
    )

    report = CapabilityEvaluator(executor).evaluate(scenario, tmp_path / "workspace")

    assert executor.prepared == [scenario.objective]
    assert report.passed is True
    assert report.changed_files == ("agent.txt",)
    assert (tmp_path / "workspace" / "fixture.txt").read_bytes().startswith(b"fixture")


def test_real_newline_drift_after_before_remains_a_changed_file(tmp_path: Path) -> None:
    class NewlineExecutor:
        def prepare_workspace(self, objective: str, workspace: Path) -> None:
            del objective
            # Make the measured baseline LF even though initial_files is
            # written through the platform text API.
            (workspace / "newline.txt").write_bytes(b"line\n")

        def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
            del objective
            (workspace / "newline.txt").write_bytes(b"line\r\n")
            return ExecutionObservation(success=True)

    scenario = CapabilityScenario(
        "newline-drift",
        "write",
        "change newline bytes",
        initial_files={"newline.txt": "line\n"},
        expectation=ScenarioExpectation(allowed_changed_files=("newline.txt",)),
    )

    report = CapabilityEvaluator(NewlineExecutor()).evaluate(scenario, tmp_path / "workspace")

    assert report.passed is True
    assert report.changed_files == ("newline.txt",)
    assert (tmp_path / "workspace" / "newline.txt").read_bytes() == b"line\r\n"


def _write_initial_files(scenario: CapabilityScenario, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    for relative, content in scenario.initial_files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


def test_pv105_stale_memory_setup_does_not_modify_measured_workspace(tmp_path: Path) -> None:
    scenario = next(item for item in PRACTICAL_V1 if item.scenario_id == "PV1-05")
    workspace = tmp_path / "pv105"
    _write_initial_files(scenario, workspace)
    before = (workspace / "feature.py").read_bytes()
    paths = AppPaths.discover(tmp_path / "home", env={})

    prepare_practical_workspace(scenario, workspace)
    prepare_practical_application(scenario, workspace, paths)

    assert before == (workspace / "feature.py").read_bytes()
    assert before == b'MODE = "new"\n'


def test_pv107_dirty_notes_are_baseline_and_remain_byte_identical_after_execution(
    tmp_path: Path,
) -> None:
    scenario = next(item for item in PRACTICAL_V1 if item.scenario_id == "PV1-07")

    class DirtyRepositoryExecutor:
        baseline_notes: bytes = b""

        def prepare_workspace(self, objective: str, workspace: Path) -> None:
            del objective
            prepare_practical_workspace(scenario, workspace)
            self.baseline_notes = (workspace / "notes.txt").read_bytes()

        def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
            del objective
            (workspace / "app.py").write_bytes(b"agent change\n")
            return ExecutionObservation(success=True)

    executor = DirtyRepositoryExecutor()
    report = CapabilityEvaluator(executor).evaluate(
        scenario,
        tmp_path / "measured",
    )
    measured = tmp_path / "measured"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=measured,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert report.changed_files == ("app.py",)
    assert "notes.txt" not in report.changed_files
    assert "notes.txt" in status
    assert executor.baseline_notes
    assert (measured / "notes.txt").read_bytes() == executor.baseline_notes


def test_pv107_application_setup_does_not_rebuild_or_mutate_dirty_workspace(
    tmp_path: Path,
) -> None:
    scenario = next(item for item in PRACTICAL_V1 if item.scenario_id == "PV1-07")
    workspace = tmp_path / "pv107"
    _write_initial_files(scenario, workspace)
    prepare_practical_workspace(scenario, workspace)
    before = {
        path.name: path.read_bytes()
        for path in (workspace / "app.py", workspace / "notes.txt")
    }

    prepare_practical_application(scenario, workspace, AppPaths.discover(tmp_path / "home", env={}))

    after = {
        path.name: path.read_bytes()
        for path in (workspace / "app.py", workspace / "notes.txt")
    }
    assert after == before


def test_canonical_projection_keeps_code_outcome_shallow_and_secret_safe() -> None:
    state = AgentState()
    state.objective = "keep the observed implementation"
    state.plan = Plan()
    state.tool_history = [
        {
            "tool": "code_task",
            "args": {"action": "modify", "targets": ["feature.py"]},
            "result": {
                "status": "succeeded",
                "ok": True,
                "executed": True,
                "data": {
                    "metadata": {
                        "proposal_reason_code": "NONE",
                        "code_outcome": {
                            "kind": "NO_CHANGE",
                            "verification": "SUPPORTED",
                            "cited_evidence_ids": ["file-feature"],
                            "nested": {"depth": {"secret": "api_key=DO_NOT_PROJECT"}},
                        },
                    },
                    "artifacts": [
                        {
                            "metadata": {
                                "mutation_attempted": True,
                                "mutation_occurred": False,
                                "persisted_mutation": False,
                                "surviving_mutation": False,
                                "affected_files": ["feature.py"],
                            }
                        }
                    ],
                },
            },
        }
    ]
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=state,
            _run_correlation=RunCorrelation.fresh(),
            _task_failed=False,
            _cancelled=False,
            _last_failure_code=None,
            _last_failure_layer=None,
        ),
        "succeeded",
        metrics=project_run_metrics([]),
    )

    projection = snapshot_evaluation_projection(snapshot)
    code = projection["code_outcome"]

    assert code["kind"] == "NO_CHANGE"
    assert code["verification"] == "SUPPORTED"
    assert code["reason_code"] == "NONE"
    assert code["mutation_occurred"] is False
    assert code["affected_files"] == ["feature.py"]
    assert "DO_NOT_PROJECT" not in repr(projection)
    assert "depth" not in repr(code)
    assert "event-data-depth-truncated" in repr(projection["history"])


def test_canonical_projection_reads_needs_input_metadata_after_empty_records() -> None:
    state = AgentState()
    state.objective = "request the missing preference"
    state.tool_history = [
        {
            "tool": "code_task",
            "args": {"action": "modify", "targets": ["config.py"]},
            "result": {
                "status": "blocked",
                "ok": False,
                "executed": True,
                "data": {
                    "status": "blocked",
                    "metadata": {
                        "proposal_kind": "NEEDS_INPUT",
                        "proposal_reason_code": "USER_CHOICE_REQUIRED",
                        "code_verification": {"verdict": "SUPPORTED"},
                    },
                    "failure_code": "CODE_INPUT_REQUIRED",
                },
            },
        }
    ]
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=state,
            _run_correlation=RunCorrelation.fresh(),
            _task_failed=False,
            _cancelled=False,
            _last_failure_code=None,
            _last_failure_layer=None,
        ),
        "blocked",
        metrics=project_run_metrics([]),
    )

    assert snapshot_evaluation_projection(snapshot)["code_outcome"] == {
        "kind": "NEEDS_INPUT",
        "reason_code": "USER_CHOICE_REQUIRED",
        "verification": "SUPPORTED",
        "failure_code": "CODE_INPUT_REQUIRED",
        "evidence_ids": [],
        "mutation_attempted": False,
        "mutation_occurred": False,
        "persisted_mutation": False,
        "surviving_mutation": False,
        "affected_files": [],
    }


def test_canonical_validation_detail_is_explicit_bounded_and_deterministic() -> None:
    state = AgentState()
    state.objective = "change math"
    state.tool_history = [
        {
            "tool": "code_task",
            "args": {"action": "modify", "targets": ["src/math_ops.py"]},
            "result": {
                "status": "succeeded",
                "ok": True,
                "executed": True,
                "data": {
                    "metadata": {
                        "validation": {
                            "execution_status": "passed",
                            "effective_status": "passed",
                            "tests_requested": True,
                            "test_coverage": "targeted",
                            "plan_fingerprint": "plan-123",
                            "selections": [
                                {
                                    "command_kind": "pytest",
                                    "scope": "targeted_tests",
                                    "targets": ["tests/test_math_ops.py"],
                                    "status": "passed",
                                    "extra": {"secret": "token=DO_NOT_PROJECT"},
                                }
                            ],
                        }
                    }
                },
            },
        }
    ]
    owner = SimpleNamespace(
        agent_state=state,
        _run_correlation=RunCorrelation.fresh(),
        _task_failed=False,
        _cancelled=False,
        _last_failure_code=None,
        _last_failure_layer=None,
    )
    snapshot = build_canonical_run_snapshot(
        owner,
        "succeeded",
        metrics=project_run_metrics([]),
    )

    first = snapshot_evaluation_projection(snapshot)
    second = snapshot_evaluation_projection(snapshot)
    validation = first["validation_detail"]

    assert validation["tests_requested"] is True
    assert validation["test_coverage"] == "targeted"
    assert validation["selections"] == [
        {
            "command_kind": "pytest",
            "scope": "targeted_tests",
            "targets": ["tests/test_math_ops.py"],
            "status": "passed",
        }
    ]
    assert "DO_NOT_PROJECT" not in repr(first)
    assert first == second
    json.dumps(first, ensure_ascii=False, sort_keys=True)
    first["validation_detail"]["selections"].clear()
    assert snapshot_evaluation_projection(snapshot)["validation_detail"]["selections"]


def test_production_verifier_requires_literal_and_complete_file_for_needs_input() -> None:
    file_record = CodeEvidenceRecord(
        "file-config",
        "FILE_CONTENT",
        path="config.py",
        sha256="a" * 64,
        content="MODE = 'new'\n",
    )
    literal_record = CodeEvidenceRecord(
        "user-choice",
        "USER_LITERAL",
        content="choose the current mode",
        sha256="b" * 64,
    )
    incomplete_file = CodeEvidenceRecord(
        "file-incomplete",
        "FILE_CONTENT",
        path="config.py",
        sha256="c" * 64,
        content="MODE = 'new'\n",
        complete=False,
        truncated=True,
        text_lossless=False,
    )
    manifest = CodeEvidenceManifest(
        "manifest",
        (file_record, literal_record),
        total_text_chars=len(file_record.content or "") + len(literal_record.content or ""),
    )
    incomplete_manifest = CodeEvidenceManifest(
        "incomplete-manifest",
        (incomplete_file, literal_record),
        total_text_chars=len(incomplete_file.content or "") + len(literal_record.content or ""),
    )

    literal_only = CodeOutcomeVerifier._project_verdict(
        {
            "verdict": "SUPPORTED",
            "reason": "literal only",
            "evidence_ids": ["user-choice"],
        },
        manifest,
        CodeProposalKind.NEEDS_INPUT,
        target_paths=("config.py",),
        reason_code=CodeProposalReasonCode.USER_CHOICE_REQUIRED,
        additional_evidence_ids=(),
    )
    sufficient = CodeOutcomeVerifier._project_verdict(
        {
            "verdict": "SUPPORTED",
            "reason": "literal and current file",
            "evidence_ids": ["user-choice", "file-config"],
        },
        manifest,
        CodeProposalKind.NEEDS_INPUT,
        target_paths=("config.py",),
        reason_code=CodeProposalReasonCode.USER_CHOICE_REQUIRED,
        additional_evidence_ids=(),
    )
    incomplete = CodeOutcomeVerifier._project_verdict(
        {
            "verdict": "SUPPORTED",
            "reason": "literal and incomplete file",
            "evidence_ids": ["user-choice", "file-incomplete"],
        },
        incomplete_manifest,
        CodeProposalKind.NEEDS_INPUT,
        target_paths=("config.py",),
        reason_code=CodeProposalReasonCode.USER_CHOICE_REQUIRED,
        additional_evidence_ids=(),
    )

    assert literal_only.verdict is CodeOutcomeVerdict.INSUFFICIENT
    assert sufficient.verdict is CodeOutcomeVerdict.SUPPORTED
    assert incomplete.verdict is CodeOutcomeVerdict.INSUFFICIENT


def test_scripted_verifier_selects_existing_complete_file_and_literal_records() -> None:
    file_record = CodeEvidenceRecord(
        "arbitrary-file-id",
        "FILE_CONTENT",
        path="any/target.py",
        sha256="d" * 64,
        content="value = 1\n",
    )
    literal_record = CodeEvidenceRecord(
        "arbitrary-literal-id",
        "USER_LITERAL",
        content="choose the value",
        sha256="e" * 64,
    )
    incomplete_record = CodeEvidenceRecord(
        "incomplete-file-id",
        "FILE_CONTENT",
        path="any/target.py",
        sha256="f" * 64,
        content="value = 1\n",
        complete=False,
        truncated=True,
        text_lossless=False,
    )
    envelope = render_untrusted_context_envelope(
        (
            file_record.to_context_record(),
            literal_record.to_context_record(),
            incomplete_record.to_context_record(),
        )
    )
    request = SimpleNamespace(
        messages=(
            SimpleNamespace(content=envelope),
            SimpleNamespace(content="Decisão a verificar: NEEDS_INPUT"),
        )
    )

    selected = _select_verifier_evidence(request, "NEEDS_INPUT")
    response = json.loads(ScriptedEvaluationGateway._verifier_response(request))

    assert selected == ("arbitrary-file-id", "arbitrary-literal-id")
    assert response["verdict"] == "SUPPORTED"
    assert response["evidence_ids"] == [
        "arbitrary-file-id",
        "arbitrary-literal-id",
    ]
    assert "incomplete-file-id" not in response["evidence_ids"]


def test_scripted_verifier_no_change_uses_file_record_without_inventing_ids() -> None:
    record = CodeEvidenceRecord(
        "file-from-fixture",
        "FILE_CONTENT",
        path="fixture.py",
        sha256="1" * 64,
        content="ok\n",
    )
    envelope = render_untrusted_context_envelope((record.to_context_record(),))
    request = SimpleNamespace(
        messages=(
            SimpleNamespace(content=envelope),
            SimpleNamespace(content="Decisão a verificar: NO_CHANGE"),
        )
    )

    response = json.loads(ScriptedEvaluationGateway._verifier_response(request))

    assert response["evidence_ids"] == ["file-from-fixture"]
    assert all(item in {"file-from-fixture"} for item in response["evidence_ids"])


def _scripted_verifier_request(
    records: list[CodeEvidenceRecord],
    decision: str = "NEEDS_INPUT",
) -> SimpleNamespace:
    envelope = render_untrusted_context_envelope(
        tuple(record.to_context_record() for record in records)
    )
    return SimpleNamespace(
        messages=(
            SimpleNamespace(content=envelope),
            SimpleNamespace(content=f"Decisão a verificar: {decision}"),
        )
    )


def _complete_file_records(count: int) -> list[CodeEvidenceRecord]:
    return [
        CodeEvidenceRecord(
            f"file-{index}",
            "FILE_CONTENT",
            path=f"src/module_{index}.py",
            sha256=f"{index:064x}",
            content=f"value = {index}\n",
        )
        for index in range(count)
    ]


def _literal_record() -> CodeEvidenceRecord:
    return CodeEvidenceRecord(
        "user-literal",
        "USER_LITERAL",
        content="choose the current value",
        sha256="a" * 64,
    )


def test_c5_1_case_a_selects_literal_and_complete_file() -> None:
    records = [_complete_file_records(1)[0], _literal_record()]

    selected = _select_verifier_evidence(
        _scripted_verifier_request(records),
        "NEEDS_INPUT",
    )

    assert selected == ("file-0", "user-literal")


def test_c5_1_case_b_reserves_literal_at_evidence_limit() -> None:
    files = _complete_file_records(MAX_VERIFIER_EVIDENCE_IDS)
    records = files + [_literal_record()]

    selected = _select_verifier_evidence(
        _scripted_verifier_request(records),
        "NEEDS_INPUT",
    )

    assert len(selected) == MAX_VERIFIER_EVIDENCE_IDS
    assert selected == tuple(file.evidence_id for file in files[:-1]) + ("user-literal",)
    assert "user-literal" in selected
    assert set(selected).issubset({record.evidence_id for record in records})


def test_c5_1_case_c_reserves_literal_with_more_than_limit_files() -> None:
    files = _complete_file_records(MAX_VERIFIER_EVIDENCE_IDS + 4)
    records = files + [_literal_record()]

    first = _select_verifier_evidence(
        _scripted_verifier_request(records),
        "NEEDS_INPUT",
    )
    second = _select_verifier_evidence(
        _scripted_verifier_request(records),
        "NEEDS_INPUT",
    )

    assert len(first) == MAX_VERIFIER_EVIDENCE_IDS
    assert first == tuple(file.evidence_id for file in files[: MAX_VERIFIER_EVIDENCE_IDS - 1]) + (
        "user-literal",
    )
    assert first == second
    assert "user-literal" in first
    assert set(first).issubset({record.evidence_id for record in records})


def test_c5_1_case_d_file_content_only_does_not_invent_literal() -> None:
    files = _complete_file_records(MAX_VERIFIER_EVIDENCE_IDS)

    selected = _select_verifier_evidence(
        _scripted_verifier_request(files),
        "NEEDS_INPUT",
    )

    assert selected == tuple(file.evidence_id for file in files)
    assert "user-literal" not in selected
    assert set(selected).issubset({record.evidence_id for record in files})


def test_c5_1_case_e_incomplete_file_is_not_promoted() -> None:
    incomplete = CodeEvidenceRecord(
        "incomplete-file",
        "FILE_CONTENT",
        path="src/module.py",
        sha256="b" * 64,
        content="value =",
        complete=False,
        truncated=True,
        text_lossless=False,
    )
    literal = _literal_record()

    selected = _select_verifier_evidence(
        _scripted_verifier_request([literal, incomplete]),
        "NEEDS_INPUT",
    )

    assert selected == ("user-literal",)
    assert "incomplete-file" not in selected


def test_c5_1_case_f_repeated_input_is_structurally_identical() -> None:
    records = _complete_file_records(MAX_VERIFIER_EVIDENCE_IDS + 4) + [_literal_record()]
    request = _scripted_verifier_request(records)

    first = _select_verifier_evidence(request, "NEEDS_INPUT")
    second = _select_verifier_evidence(request, "NEEDS_INPUT")

    assert first == second
    assert first == tuple(file.evidence_id for file in records[: MAX_VERIFIER_EVIDENCE_IDS - 1]) + (
        "user-literal",
    )


def _catalog_prompt(names: list[str]) -> str:
    catalog = json.dumps([{"name": name} for name in names], ensure_ascii=False)
    return f"TOOL DISCOVERY\n<untrusted_tool_catalog>{catalog}</untrusted_tool_catalog>"


def test_scripted_tool_selection_prioritizes_plan_tools_without_leaving_catalog() -> None:
    names = [f"optional_{index}" for index in range(10)] + ["repository_state", "code_task"]
    response = json.loads(
        selection_response(
            _catalog_prompt(names),
            required_tools=("repository_state", "code_task", "not-present", "repository_state"),
        )
    )

    assert response["tools"][:2] == ["repository_state", "code_task"]
    assert len(response["tools"]) == 8
    assert set(response["tools"]).issubset(set(names))
    assert len(response["tools"]) == len(set(response["tools"]))
    assert "not-present" not in response["tools"]
    assert set(response) == {"tools"}


def test_scripted_tool_selection_reads_required_names_from_fixture_plan() -> None:
    required = scripted_required_tools("PV1-07: preserve the repository state")

    assert required[:2] == ("repository_state", "code_task")

    gateway = SimpleNamespace(
        dispatch_objective="PV1-07: preserve the repository state",
        objective="preserve the repository state",
    )
    response = json.loads(
        scripted_response(
            gateway,
            "",
            _catalog_prompt(
                [
                    "file_reader",
                    "grep",
                    "code_task",
                    "shell_process",
                    "summarize",
                    "repository_state",
                    "memory_search",
                    "extension_catalog",
                    "extra",
                ]
            ),
        )
    )

    assert response["tools"][:2] == ["repository_state", "code_task"]
    assert len(response["tools"]) == 8
    assert set(response["tools"]).issubset(
        {
            "file_reader",
            "grep",
            "code_task",
            "shell_process",
            "summarize",
            "repository_state",
            "memory_search",
            "extension_catalog",
            "extra",
        }
    )
