from __future__ import annotations

import inspect
from typing import Any, Optional, Sequence

from agent.code.changes import ChangeSet, ChangeSetTransaction
from agent.code.outcome_verifier import (
    CodeOutcomeVerifier,
    prepared_change_evidence,
)
from agent.code.policy import ChangeApprover
from agent.code.workflow_application_flow_support import (
    run_apply_changes,
)
from agent.runtime.context import TaskResult


def apply_changes(
    service: Any,
    change_set: ChangeSet,
    *,
    include_tests: bool = False,
    requested_targets: Sequence[str] = (),
    approver: Optional[ChangeApprover] = None,
    evidence_manifest: Any = None,
    repair_attempt: bool = False,
) -> TaskResult:
    return run_apply_changes(
        service,
        change_set,
        include_tests=include_tests,
        requested_targets=requested_targets,
        approver=approver,
        evidence_manifest=evidence_manifest,
        repair_attempt=repair_attempt,
        transaction_factory=ChangeSetTransaction,
        outcome_verifier_factory=CodeOutcomeVerifier,
        prepared_change_evidence_factory=prepared_change_evidence,
        validate_model_code_task=_validate_model_code_task,
        requires_selective_verification=_requires_selective_verification,
    )


def _validate_model_code_task(
    validator: Any,
    project: Any,
    changed_files: Sequence[str],
    *,
    include_tests: bool,
) -> Any:
    """Call the canonical validator with the model-task boundary when known."""

    kwargs: dict[str, object] = {"include_tests": include_tests}
    try:
        parameters = inspect.signature(validator.validate).parameters.values()
        accepts_boundary = any(
            parameter.name == "model_actionable"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_boundary = False
    if accepts_boundary:
        kwargs["model_actionable"] = True
    return validator.validate(project, changed_files, **kwargs)


def _requires_selective_verification(
    change_set: ChangeSet,
    preview: Any,
    requested_targets: Sequence[str],
    assessment: Any,
    repair_attempt: bool,
) -> bool:
    kinds = {change.kind.value for change in change_set.changes}
    if kinds & {"delete", "move"}:
        return True
    if len(tuple(getattr(preview, "affected_files", ()) or ())) > 2:
        return True
    if bool(getattr(assessment, "requires_confirmation", False)) or repair_attempt:
        return True
    targets = tuple(str(item).replace("\\", "/").strip("/") for item in requested_targets)
    if not targets:
        return True
    return any(
        not any(
            affected == target or affected.startswith(target + "/")
            for target in targets
        )
        for affected in getattr(preview, "affected_files", ())
    )
