from copy import deepcopy
from types import SimpleNamespace

import pytest

from llm_agent.agent.planning.plan_model import Plan, ToolPlanStep
from llm_agent.agent.planning.replan_models import ReplanAction
from llm_agent.agent.planning.validation_repair_contracts import accepts_constrained_repair
from llm_agent.agent.planning.validation_repair_plan import replan_blocked_steps
from llm_agent.agent.runtime.recovery import RecoveryBudgetState, RecoveryScope


def _original():
    return {
        "tool": "file_reader", "args": {"file_path": 123, "start_line": 1}, "_step_id": "s1",
        "requirement_ids": ["req-1"], "obligation_ids": ["obl-1"],
    }


def test_field_repair_accepts_unchanged_canonical_associations():
    original = _original()
    candidate = {**original, "args": {"file_path": "a.txt", "start_line": 1}}
    assert accepts_constrained_repair(original, candidate, frozenset({"file_path"}))


@pytest.mark.parametrize("field", ["requirement_ids", "obligation_ids"])
@pytest.mark.parametrize("change", ["remove", "replace", "add", "malformed"])
def test_field_repair_cannot_rewrite_or_expand_associations(field, change):
    original = _original()
    candidate = deepcopy(original)
    candidate["args"]["file_path"] = "a.txt"
    if change == "remove":
        candidate.pop(field)
    elif change == "replace":
        candidate[field] = ["other"]
    elif change == "add":
        original.pop(field)
    else:
        candidate[field] = original[field] = ["duplicate", "duplicate"]
    assert not accepts_constrained_repair(original, candidate, frozenset({"file_path"}))


@pytest.mark.parametrize("mutation", ["tool", "frozen_arg", "step_id", "extra_field"])
def test_association_support_does_not_weaken_other_repair_boundaries(mutation):
    original = _original()
    candidate = deepcopy(original)
    candidate["args"]["file_path"] = "a.txt"
    if mutation == "tool":
        candidate["tool"] = "writer"
    elif mutation == "frozen_arg":
        candidate["args"]["start_line"] = 2
    elif mutation == "step_id":
        candidate["_step_id"] = "other"
    else:
        candidate["permission"] = "write"
    assert not accepts_constrained_repair(original, candidate, frozenset({"file_path"}))


def test_typed_repair_preserves_runtime_associations_and_consumes_only_one_budget(monkeypatch):
    import llm_agent.agent.planning.replan as replan_module
    import llm_agent.agent.planning.validation_repair_support as support

    original = _original()
    plan = Plan.from_raw([original])
    replacement = ToolPlanStep("model-id", "file_reader", {"file_path": "a.txt", "start_line": 1})
    asked = []
    validated = []

    def replan(*args, **kwargs):
        asked.append(kwargs)
        return ReplanAction(Plan((replacement,)), source="fixture")

    def validate(gateway, candidate, *args):
        validated.append(candidate)
        return candidate

    monkeypatch.setattr(replan_module, "replan", replan)
    monkeypatch.setattr(support, "_validate_typed_candidate", validate)
    budget = RecoveryBudgetState()
    orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(recovery_budget=budget, tool_history=[]), _emit=lambda *args: None,
    )
    gateway = SimpleNamespace(orchestrator=orchestrator)
    blocked = SimpleNamespace(index=0, is_validation_repair=True, repairable_fields=frozenset({"file_path"}), reason="invalid file_path")
    result = replan_blocked_steps(gateway, plan, "Read a.txt", [blocked])
    assert result is not None
    assert result[0].step_id == "s1"
    assert result[0].requirement_ids == ("req-1",)
    assert result[0].obligation_ids == ("obl-1",)
    assert result[0].args == {"file_path": "a.txt", "start_line": 1}
    assert len(asked) == len(validated) == 1
    assert budget.used(RecoveryScope.VALIDATION_REPAIRS) == 1
    assert budget.used(RecoveryScope.LLM_REPLANS) == 0
    assert replan_blocked_steps(gateway, plan, "Read a.txt", [blocked]) is None
    assert len(asked) == 1
