"""Public facade for the evaluation campaign modules."""

from agent.evaluation.adversarial_audit import ADVERSARIAL_AUDIT_QUESTIONS, adversarial_audit
from agent.evaluation.analysis import (
    analyze_campaign,
    build_corrective_readiness,
    prior_epoch_disposition,
    secret_safe_report,
    validate_campaign_report,
)
from agent.evaluation.artifact_paths import EvaluationArtifactPaths, canonical_artifact_paths
from agent.evaluation.campaign import run_real_model_campaign, run_scripted_campaign
from agent.evaluation.evaluation_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    DEFAULT_DRY_RUN_EPOCH,
    DEFAULT_PROFILE,
    DEFAULT_REAL_MODEL_EPOCH,
    campaign_config,
    candidate_identity,
    candidate_identity_string,
    documentation_fingerprint,
    fake_model_identity,
    fixture_identity,
    model_config_identity,
    normalize_endpoint_identity,
    planned_model_profile,
    resume_compatible,
    semantic_candidate_fingerprint,
    semantic_candidate_manifest,
    semantic_manifest_hash,
    source_fingerprint,
)
from agent.evaluation.execution import CampaignRun
from agent.evaluation.real_model_preflight import (
    REAL_MODEL_PREFLIGHT_SCHEMA_VERSION,
    build_real_model_preflight,
    validate_real_model_preflight,
)
from agent.evaluation.scenario_contracts import H_SERIES_VERSION
from agent.evaluation.scripted_gateway import ScriptedEvaluationGateway
from agent.llm.identity import normalize_external_identity

__all__ = [
    "ADVERSARIAL_AUDIT_QUESTIONS",
    "EvaluationArtifactPaths",
    "REAL_MODEL_PREFLIGHT_SCHEMA_VERSION",
    "analyze_campaign",
    "CAMPAIGN_SCHEMA_VERSION",
    "CampaignRun",
    "DEFAULT_PROFILE",
    "DEFAULT_DRY_RUN_EPOCH",
    "DEFAULT_REAL_MODEL_EPOCH",
    "H_SERIES_VERSION",
    "ScriptedEvaluationGateway",
    "campaign_config",
    "canonical_artifact_paths",
    "candidate_identity",
    "candidate_identity_string",
    "documentation_fingerprint",
    "fake_model_identity",
    "fixture_identity",
    "adversarial_audit",
    "planned_model_profile",
    "model_config_identity",
    "normalize_endpoint_identity",
    "normalize_external_identity",
    "prior_epoch_disposition",
    "resume_compatible",
    "run_real_model_campaign",
    "run_scripted_campaign",
    "source_fingerprint",
    "semantic_candidate_fingerprint",
    "semantic_candidate_manifest",
    "semantic_manifest_hash",
    "build_corrective_readiness",
    "build_real_model_preflight",
    "secret_safe_report",
    "validate_real_model_preflight",
    "validate_campaign_report",
]
