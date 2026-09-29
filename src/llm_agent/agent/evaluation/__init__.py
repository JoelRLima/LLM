"""Avaliação determinística das capacidades do agente."""

from llm_agent.agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
from llm_agent.agent.evaluation.comparison import (
    EVALUATION_COMPARISON_INCOMPATIBLE,
    EvaluationAggregate,
    EvaluationComparison,
    EvaluationComparisonError,
    aggregate_receipts,
    compare_receipt_groups,
)
from llm_agent.agent.evaluation.contracts import (
    CapabilityScenario,
    EvaluationFailure,
    ExecutionObservation,
    FileExpectation,
    ScenarioExpectation,
    ScenarioReport,
)
from llm_agent.agent.evaluation.curated import CURATED_CAPABILITY_SET
from llm_agent.agent.evaluation.experiment import (
    EVALUATION_EXPERIMENT_CONTRACT_VERSION,
    EvaluationExperimentContext,
    EvaluationExperimentError,
    EvaluationVariantProfile,
    built_in_evaluation_profile,
    evaluation_context,
)
from llm_agent.agent.evaluation.feedback import (
    FEEDBACK_SCHEMA_VERSION,
    FeedbackError,
    FeedbackRecord,
    FeedbackService,
    FeedbackTarget,
    FeedbackVerdict,
)
from llm_agent.agent.evaluation.feedback_store import FeedbackStore, FeedbackStoreError
from llm_agent.agent.evaluation.loader import load_scenario, load_scenarios
from llm_agent.agent.evaluation.long_horizon import (
    LONG_HORIZON_V1,
    run_long_horizon_scripted,
)
from llm_agent.agent.evaluation.practical import run_practical_scripted
from llm_agent.agent.evaluation.practical_scenarios import (
    PRACTICAL_SET_VERSION,
    PRACTICAL_V1,
    practical_fixture_identity,
)
from llm_agent.agent.evaluation.real_model_readiness import (
    REAL_MODEL_READINESS_VERSION,
    readiness_campaign_policy,
    real_model_readiness_scenarios,
)
from llm_agent.agent.evaluation.receipt import (
    EVALUATION_RECEIPT_SCHEMA_VERSION,
    EvaluationPrimitiveMeasurements,
    EvaluationReceiptError,
    EvaluationReceiptV1,
    EvaluationRunIdentity,
    EvaluationTechnicalOutcome,
    EvaluationVariantIdentity,
    build_evaluation_receipt,
    receipt_id_for_payload,
    validate_evaluation_receipt,
)
from llm_agent.agent.evaluation.regressions import CURATED_REGRESSION_SET, RegressionCase
from llm_agent.agent.evaluation.runner import CapabilityEvaluator, EvaluationSetReport, ScenarioExecutor
from llm_agent.agent.evaluation.scenario_contracts import (
    H_SERIES,
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    HRunEvidence,
    HSeriesArm,
    HSeriesScenario,
    RepetitionPolicy,
    digest_fixture,
    sanitize_evidence,
    validate_h_series,
)
from llm_agent.agent.evaluation.trace import RecordingGateway

__all__ = [
    "CapabilityEvaluator",
    "AgentApplicationScenarioExecutor",
    "CausalFailureClass",
    "CURATED_CAPABILITY_SET",
    "CURATED_REGRESSION_SET",
    "EvidenceLevel",
    "CapabilityScenario",
    "EvaluationFailure",
    "ExecutionObservation",
    "EvaluationSetReport",
    "FileExpectation",
    "HRunEvidence",
    "HSeriesArm",
    "HSeriesScenario",
    "H_SERIES",
    "H_SERIES_VERSION",
    "RepetitionPolicy",
    "RecordingGateway",
    "REAL_MODEL_READINESS_VERSION",
    "ScenarioExecutor",
    "ScenarioExpectation",
    "ScenarioReport",
    "digest_fixture",
    "RegressionCase",
    "load_scenario",
    "load_scenarios",
    "readiness_campaign_policy",
    "real_model_readiness_scenarios",
    "sanitize_evidence",
    "validate_h_series",
    "PRACTICAL_SET_VERSION",
    "PRACTICAL_V1",
    "practical_fixture_identity",
    "run_practical_scripted",
    "LONG_HORIZON_V1",
    "run_long_horizon_scripted",
    "EVALUATION_COMPARISON_INCOMPATIBLE",
    "EVALUATION_EXPERIMENT_CONTRACT_VERSION",
    "EVALUATION_RECEIPT_SCHEMA_VERSION",
    "FEEDBACK_SCHEMA_VERSION",
    "EvaluationAggregate",
    "EvaluationComparison",
    "EvaluationComparisonError",
    "EvaluationExperimentContext",
    "EvaluationExperimentError",
    "EvaluationPrimitiveMeasurements",
    "EvaluationReceiptError",
    "EvaluationReceiptV1",
    "EvaluationRunIdentity",
    "EvaluationTechnicalOutcome",
    "EvaluationVariantIdentity",
    "EvaluationVariantProfile",
    "FeedbackError",
    "FeedbackRecord",
    "FeedbackService",
    "FeedbackStore",
    "FeedbackStoreError",
    "FeedbackTarget",
    "FeedbackVerdict",
    "aggregate_receipts",
    "built_in_evaluation_profile",
    "build_evaluation_receipt",
    "compare_receipt_groups",
    "evaluation_context",
    "receipt_id_for_payload",
    "validate_evaluation_receipt",
]
