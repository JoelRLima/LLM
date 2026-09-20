from __future__ import annotations

from pathlib import Path

from agent.application import AgentApplication
from agent.evaluation import (
    ExecutionObservation,
    ScenarioReport,
    aggregate_receipts,
    build_evaluation_receipt,
    evaluation_context,
    validate_evaluation_receipt,
)
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.variants.models import VariantLifecycle
from tests.support.offline_scenarios import OfflineChatGateway


def _initialized_paths(root: Path) -> AppPaths:
    paths = AppPaths.discover(root / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


def _completed_report(context) -> ScenarioReport:
    observation = ExecutionObservation(
        success=True,
        answer="deterministic completed scenario",
        steps=1,
        measurement={
            "variant_fingerprint": context.profile.composition.fingerprint,
            "variant_composition": context.profile.composition.normalized_dict(),
            "run_id": "run-evaluation-001",
            "root_task_id": "root-evaluation-001",
            "runtime_task_id": "task-evaluation-001",
            "terminal_outcome": "succeeded",
            "duration_ms": 1,
            "model_calls": 0,
            "tool_calls": 0,
            "tool_history_count": 0,
            "accounted_tokens": 0,
            "output_chars": 32,
            "token_usage_complete": True,
            "output_truncated": False,
            "rollback_occurred": False,
            "replan_count": 0,
        },
    )
    return ScenarioReport(
        scenario_id="w19-cross-domain-scenario",
        capability="w19/cross-domain",
        passed=True,
        observation=observation,
        failures=(),
        changed_files=(),
    )


def test_variant_composition_is_explicit_and_evaluation_receipt_identity_is_frozen(
    tmp_path: Path,
) -> None:
    current = evaluation_context(
        "current", experiment_id="w19-cross-domain", trial_id="trial-current"
    )
    reference = evaluation_context(
        "persona-reference-w18",
        experiment_id="w19-cross-domain",
        trial_id="trial-reference",
    )

    current_root = tmp_path / "current"
    current_workspace = current_root / "workspace"
    current_workspace.mkdir(parents=True)
    with AgentApplication.create(
        paths=_initialized_paths(current_root),
        workspace=current_workspace,
        gateway=OfflineChatGateway("unused"),
        configure_logging=False,
        variant_composition=current.profile.composition,
    ) as current_application:
        assert current_application.variant_composition == current.profile.composition
        assert current_application.variant_fingerprint == current.profile.composition.fingerprint
        assert current_application.orchestrator.persona_router.__class__.__module__.endswith("persona.current")
        current_tools = current_application.tool_registry.names()
        current_gateway_type = type(current_application.tool_invocation_gateway)

    reference_root = tmp_path / "reference"
    reference_workspace = reference_root / "workspace"
    reference_workspace.mkdir(parents=True)
    with AgentApplication.create(
        paths=_initialized_paths(reference_root),
        workspace=reference_workspace,
        gateway=OfflineChatGateway("unused"),
        configure_logging=False,
        variant_composition=reference.profile.composition,
    ) as reference_application:
        assert reference_application.variant_composition == reference.profile.composition
        assert reference_application.variant_fingerprint == reference.profile.composition.fingerprint
        assert reference.profile.composition.selection(
            reference.profile.composition.selections[0].seam
        ).lifecycle is VariantLifecycle.REFERENCE
        assert reference_application.orchestrator.persona_router.__class__.__module__.endswith(
            "reference_w18"
        )
        assert callable(reference_application.orchestrator.persona_router.route)
        assert reference_application.tool_registry.names() == current_tools
        assert type(reference_application.tool_invocation_gateway) is current_gateway_type

    receipt = build_evaluation_receipt(
        _completed_report(current),
        experiment=current,
        scenario_arm_id="arm-current",
        repetition=1,
        attempt=1,
        evidence_level="deterministic",
    )
    validated = validate_evaluation_receipt(receipt.to_dict())
    assert validated.experiment_id == current.experiment_id
    assert validated.trial_id == current.trial_id
    assert validated.variant.profile_id == current.profile.profile_id
    assert validated.variant.fingerprint == current.profile.composition.fingerprint
    assert validated.technical.runtime_success is True
    assert validated.technical.evaluator_passed is True
    assert "transcript" not in validated.to_dict()

    aggregate = aggregate_receipts([validated])
    assert aggregate.profile_id == current.profile.profile_id
    assert aggregate.variant_fingerprint == current.profile.composition.fingerprint
