from __future__ import annotations

import copy
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove
from agent.cancellation import CancellationToken
from agent.evaluation.analysis_metrics import metric_summary
from agent.evaluation.analysis_verdict import verdict
from agent.evaluation.execution_attribution import classify_failure
from agent.evaluation.oracle import deterministic_oracle_evidence
from agent.evaluation.scenario_contracts import H_SERIES, CausalFailureClass, EvidenceLevel
from agent.planning.task_graph import ResourceMode, TaskNode, TaskResource
from agent.planning.task_semantics import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
    TaskSemanticsError,
)
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolDescriptor,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def _lossy_read(value: str) -> dict[str, object]:
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "data": value,
        "complete": False,
        "truncated": False,
        "evidence_provenance": "DERIVED_LOSSY",
        "source_identity": "source.txt",
        "source_hash": hashlib.sha256(value.encode()).hexdigest(),
        "source_extent": {"kind": "derived_summary"},
    }


def _registry(adapter: object) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    return registry


def test_r1_r7_lossy_evidence_cannot_admit_previous_read_obligation() -> None:
    semantics = TaskSemantics.empty(
        "Leia source.txt e procure nos outros arquivos pela palavra que ele contem."
    )
    semantics.observe_tool(
        "file_reader",
        _lossy_read("summary only"),
        evidence_ref=1,
        args={"file_path": "source.txt"},
    )

    review = semantics.review_obligations(
        [
            {
                "id": "search:previous",
                "kind": "search",
                "query_source": "previous_read",
                "description": "Procure o valor da leitura anterior.",
            }
        ],
        source="canonical_review",
    )

    assert review.rejected[0].code == "MISSING_CAUSAL_EVIDENCE"
    assert semantics.obligations == ()


def test_r2_r3_nested_context_cancel_cannot_outlive_parent() -> None:
    parent = TaskExecutionContext(
        model_gateway=SimpleNamespace(),
        cancellation=CancellationToken(),
        permissions=frozenset({"read"}),
    )
    child = parent.child("code_task", permissions=frozenset({"read"}))

    parent.cancellation.cancel()

    assert child.cancellation.cancelled is True
    assert child.parent_task_id == parent.task_id


def test_r2_r8_nested_contexts_debit_one_canonical_tool_metric() -> None:
    parent = TaskExecutionContext(
        model_gateway=SimpleNamespace(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_task_tool_calls=5),
        permissions=frozenset({"read"}),
    )
    child = parent.child("code_task", permissions=frozenset({"read"}))
    parent.reserve_tool_call()
    child.reserve_tool_call()

    snapshot = parent.budget_ledger.snapshot().to_dict()
    metrics = metric_summary([
        {"evidence": {"measurement": {"tool_history_count": 99, "budget_snapshot": snapshot}}}
    ])

    assert metrics["tool_calls"] == 2


def test_r3_r5_cancelled_mutation_commits_only_one_terminal_observation(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    target = tmp_path / "cancelled.txt"
    committed: list[ToolResult] = []

    class Writer:
        def descriptors(self):
            return (
                ToolDescriptor(
                    "writer",
                    "writer",
                    capabilities=frozenset({"write"}),
                    cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            started.set()
            release.wait(timeout=3)
            target.write_text("mutated", encoding="utf-8")
            return ToolResult(invocation.invocation_id, ToolStatus.CANCELLED, executed=True)

    token = CancellationToken()
    gateway = ToolInvocationGateway(
        _registry(Writer()),
        approval_port=AutoApprove(),
        state_recorder=lambda _name, _args, result: committed.append(result),
    )
    results: list[ToolResult] = []
    worker = threading.Thread(
        target=lambda: results.append(gateway.run("writer", {}, cancellation_token=token))
    )
    worker.start()
    assert started.wait(timeout=2)
    token.cancel()
    release.set()
    worker.join(timeout=3)

    assert results[0].status is ToolStatus.CANCELLED
    assert len(committed) == 1
    assert committed[0].status is ToolStatus.CANCELLED
    assert target.read_text(encoding="utf-8") == "mutated"
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_r4_r6_actual_mutating_target_overrides_false_disjoint_claim() -> None:
    left = TaskNode(
        "left",
        "left",
        resources=(TaskResource("claimed-a.py", ResourceMode.WRITE),),
        capabilities=frozenset({"read", "write", "validate"}),
        metadata={"action": "modify", "targets": ["src/shared.py"]},
    )
    right = TaskNode(
        "right",
        "right",
        resources=(TaskResource("claimed-b.py", ResourceMode.WRITE),),
        capabilities=frozenset({"read", "write", "validate"}),
        metadata={"action": "modify", "targets": ["src/shared.py"]},
    )

    from agent.planning.task_scheduler import TaskGraphScheduler

    selected = TaskGraphScheduler(SimpleNamespace(), max_workers=2)._select_batch([left, right])
    assert len(selected) == 1


def test_r5_r6_physical_effect_with_commit_failure_is_unverified(tmp_path: Path) -> None:
    target = tmp_path / "written.txt"

    class Writer:
        def descriptors(self):
            return (ToolDescriptor("writer", "writer", capabilities=frozenset({"write"})),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            Path(str(invocation.args["path"])).write_text("persisted", encoding="utf-8")
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)

    gateway = ToolInvocationGateway(
        _registry(Writer()),
        approval_port=AutoApprove(),
        state_recorder=lambda *_args: (_ for _ in ()).throw(RuntimeError("commit")),
    )
    result = gateway.run("writer", {"path": str(target)})

    assert target.read_text(encoding="utf-8") == "persisted"
    assert result.status is ToolStatus.UNVERIFIED
    assert result.error is not None
    assert result.error.code == "CANONICAL_COMMIT_FAILED"
    assert result.error.detail["physical_effect_unknown"] is True


def test_r6_r8_h12_collateral_footprint_is_not_accepted() -> None:
    arm = next(item for item in H_SERIES if item.h_id == "H12").arms[0]
    report = SimpleNamespace(
        scenario_id="h12-modify-validate",
        changed_files=["h12_module.py", "collateral.txt"],
        passed=True,
        observation=SimpleNamespace(
            success=True,
            answer="valid",
            evidence={
                "terminal_status": "succeeded",
                "invocation_evidence": [{"tool": "code_task", "result": {"status": "succeeded"}}],
                "receipt": {"validation": {"outcome": "passed"}, "rollback": {"occurred": False}},
            },
        ),
    )

    assert "h12_collateral_mutation" in deterministic_oracle_evidence(report, arm)["failures"]


def test_r8_identity_drift_cannot_be_model_capability_or_release_ready() -> None:
    report = SimpleNamespace(
        passed=False,
        observation=SimpleNamespace(
            measurement={"status": "failed"},
            evidence={"terminal_status": "failed"},
        ),
    )
    attribution = {
        "runtime_defect": {
            "proven": True,
            "reason_codes": ["observed_model_identity_drift"],
            "evidence_refs": ["model_call_identities", "observed_model_ids"],
        }
    }
    classification = classify_failure(
        report,
        ("identity:observed_model_drift",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence=attribution,
    )
    assert classification.classification is not CausalFailureClass.MODEL_CAPABILITY
    release_verdict, _ = verdict(
        evidence_level="real_model",
        identity_consistent=False,
        complete=True,
        unknown_failures=0,
        scenario_summary={},
        aggregate_rate=1.0,
        incidents={},
        classifications={},
        installed_acceptance={"acceptance": True},
        observed_identity_available=False,
    )
    assert release_verdict == "INCONCLUSIVE"


def test_r7_checkpoint_cannot_upgrade_forged_admission_source() -> None:
    semantics = TaskSemantics.empty("Leia a.txt.")
    semantics.review_obligations(
        [{"id": "read-a", "kind": "read", "target": "a.txt", "description": "Ler a.txt."}],
        source="initial_plan",
    )
    checkpoint = copy.deepcopy(semantics.to_checkpoint_dict())
    checkpoint["obligations"][0]["admission_source"] = "EXTERNALLY_AUTHORIZED"

    with pytest.raises(TaskSemanticsError):
        TaskSemantics.from_checkpoint_dict(checkpoint)


def test_r1_checkpoint_cannot_upgrade_lossy_source_evidence() -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia source.txt."),
        [TaskObligation("read:source", "read", "Ler source.txt.", target="source.txt")],
        _strict_evidence=True,
    )
    semantics.observe_tool(
        "file_reader",
        _lossy_read("summary"),
        evidence_ref=1,
        args={"file_path": "source.txt"},
    )

    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())

    assert restored.obligation_status("read:source") is ObligationStatus.PENDING
    assert restored.obligation_evidence("read:source") == ()
