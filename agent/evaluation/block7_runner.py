"""Public compatibility facade for the Block 7 campaign modules."""

from agent.evaluation.block7 import H_SERIES_VERSION
from agent.evaluation.block7_analysis import (
    analyze_campaign,
    build_corrective_readiness,
    prior_epoch_disposition,
    secret_safe_report,
    validate_campaign_report,
)
from agent.evaluation.block7_audit import ADVERSARIAL_AUDIT_QUESTIONS, phase4_audit
from agent.evaluation.block7_campaign import run_real_model_campaign, run_scripted_campaign
from agent.evaluation.block7_execution import CampaignRun
from agent.evaluation.block7_gateway import ScriptedBlock7Gateway
from agent.evaluation.block7_identity import (
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
from agent.evaluation.block7_model_identity import normalize_external_identity

__all__ = [
    "ADVERSARIAL_AUDIT_QUESTIONS",
    "analyze_campaign",
    "CAMPAIGN_SCHEMA_VERSION",
    "CampaignRun",
    "DEFAULT_PROFILE",
    "DEFAULT_DRY_RUN_EPOCH",
    "DEFAULT_REAL_MODEL_EPOCH",
    "H_SERIES_VERSION",
    "ScriptedBlock7Gateway",
    "campaign_config",
    "candidate_identity",
    "candidate_identity_string",
    "documentation_fingerprint",
    "fake_model_identity",
    "fixture_identity",
    "phase4_audit",
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
    "secret_safe_report",
    "validate_campaign_report",
]
