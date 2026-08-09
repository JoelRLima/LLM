"""Avaliação determinística das capacidades do agente."""

from agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
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

__all__ = [
    "CapabilityEvaluator",
    "AgentApplicationScenarioExecutor",
    "CURATED_CAPABILITY_SET",
    "CURATED_REGRESSION_SET",
    "CapabilityScenario",
    "EvaluationFailure",
    "ExecutionObservation",
    "EvaluationSetReport",
    "FileExpectation",
    "ScenarioExecutor",
    "ScenarioExpectation",
    "ScenarioReport",
    "RegressionCase",
    "load_scenario",
    "load_scenarios",
]
