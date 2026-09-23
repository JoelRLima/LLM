"""Canonical payload serialization for content-addressed evaluation receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.evaluation.receipt import EvaluationReceiptV1


def receipt_payload_without_id(receipt: "EvaluationReceiptV1") -> dict[str, object]:
    """Return the canonical receipt payload used to derive its identifier."""
    payload = {
        "schema_version": receipt.schema_version,
        "experiment_id": receipt.experiment_id,
        "trial_id": receipt.trial_id,
        "scenario_id": receipt.scenario_id,
        "scenario_arm_id": receipt.scenario_arm_id,
        "repetition": receipt.repetition,
        "attempt": receipt.attempt,
        "evidence_level": receipt.evidence_level,
        "run": {
            "run_id": receipt.run.run_id,
            "root_task_id": receipt.run.root_task_id,
            "task_id": receipt.run.task_id,
            "terminal_status": receipt.run.terminal_status,
        },
        "variant": {
            "profile_id": receipt.variant.profile_id,
            "fingerprint": receipt.variant.fingerprint,
            "composition": dict(receipt.variant.composition),
        },
        "measurements": {
            name: getattr(receipt.measurements, name)
            for name in (
                "duration_ms",
                "model_calls",
                "tool_calls",
                "tool_history_count",
                "accounted_tokens",
                "reported_input_tokens",
                "reported_output_tokens",
                "reported_total_tokens",
                "token_usage_complete",
                "output_chars",
                "output_truncated",
                "changed_files",
                "validation_status",
                "rollback_occurred",
                "replan_count",
            )
        },
        "technical": {
            "runtime_success": receipt.technical.runtime_success,
            "evaluator_passed": receipt.technical.evaluator_passed,
            "evaluator_failure_codes": receipt.technical.evaluator_failure_codes,
        },
    }
    if receipt.practical is not None:
        payload["practical"] = receipt.practical.to_dict()
    return payload


__all__ = ["receipt_payload_without_id"]
