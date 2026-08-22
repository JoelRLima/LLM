"""Avaliação determinística das capacidades do agente."""

from agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
from agent.evaluation.block7 import (
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
from agent.evaluation.contracts import (
    CapabilityScenario,
    EvaluationFailure,
    ExecutionObservation,
    FileExpectation,
    ScenarioExpectation,
    ScenarioReport,
)
from agent.evaluation.curated import CURATED_CAPABILITY_SET
from agent.evaluation.loader import load_scenario, load_scenarios
from agent.evaluation.regressions import CURATED_REGRESSION_SET, RegressionCase
from agent.evaluation.runner import CapabilityEvaluator, EvaluationSetReport, ScenarioExecutor
from agent.evaluation.trace import RecordingGateway

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
    "ScenarioExecutor",
    "ScenarioExpectation",
    "ScenarioReport",
    "digest_fixture",
    "RegressionCase",
    "load_scenario",
    "load_scenarios",
    "sanitize_evidence",
    "validate_h_series",
]
