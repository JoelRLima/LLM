from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _files(relative: str) -> list[Path]:
    root = ROOT / relative
    assert root.is_dir()
    files = sorted(root.rglob("*.py"))
    assert files
    return files


def _containing(files: list[Path], token: str) -> list[Path]:
    return [path for path in files if token in path.read_text(encoding="utf-8")]


def test_decision_validity_projection_cannot_use_action_key_presence() -> None:
    source = (ROOT / "agent/llm/decision_compat.py").read_text(encoding="utf-8")

    assert "is_model_decision_contract_valid(" in source
    assert "request_contract=exact_contract" in source
    assert "decision is not None" not in source
    assert '"action" in decision' not in source


def test_decision_contract_production_has_no_regression_fixture_vocabulary() -> None:
    modules = [
        ROOT / "agent/llm/decision_contract.py",
        ROOT / "agent/llm/structured_output.py",
    ]
    assert all(path.is_file() for path in modules)

    sources = [path.read_text(encoding="utf-8") for path in modules]
    for fixture_only_field in ("expected_tools", "expected_result"):
        assert all(fixture_only_field not in source for source in sources)


def test_final_request_measurement_precedes_provider_dispatch() -> None:
    source = (ROOT / "agent/runtime/model_call.py").read_text(encoding="utf-8")
    record_source = (ROOT / "agent/runtime/model_call_record.py").read_text(
        encoding="utf-8"
    )

    measurement = source.index("measure_request_input_tokens(")
    dispatch = source.index("_complete_provider(")
    assert measurement < dispatch
    assert "request_estimation_source=measurement.source" in record_source


def test_eval_code_does_not_own_task_budget_call_accounting() -> None:
    files = _files("agent/evaluation")

    assert not _containing(files, "reserve_model_call(")
    assert not _containing(files, "finalize_model_call(")
    assert not _containing(files, "TaskBudgetLedger(")


def test_readiness_set_reuses_existing_scenarios_contract_and_has_no_runner() -> None:
    source = (ROOT / "agent/evaluation/real_model_readiness.py").read_text(encoding="utf-8")

    assert "from agent.evaluation.block7 import H_SERIES" in source
    assert "CapabilityScenario" in source
    assert "REAL_MODEL_READINESS_VERSION" in source
    assert "class ReadinessRunner" not in source
    assert "class ReadinessEvaluator" not in source


def test_eval_readiness_does_not_add_production_grammar_phrases() -> None:
    grammar_files = [
        path for path in _files("agent/llm")
        if "grammar" in path.name or "structured_output" in path.name
    ]
    assert grammar_files

    assert not _containing(grammar_files, "M3B-RMR-V1")
    assert not _containing(grammar_files, "real_model_readiness")
