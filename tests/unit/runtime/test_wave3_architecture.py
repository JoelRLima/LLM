import pytest

from agent.planning.plan_model import (
    Plan,
    PlanStepReference,
    resolve_previous_step_reference,
)
from scripts.check_wave3_architecture import check_source, run_checks


def test_current_wave3_decision_identity_gates_are_clean() -> None:
    assert run_checks() == []


def test_w3s1_rejects_direct_typed_decision_mapping_access() -> None:
    findings = check_source(
        """
from agent.llm.admitted_decisions import ReactiveToolDecision

def consume(decision: ReactiveToolDecision):
    return decision.get("action")
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


def test_w3s1_follows_one_and_two_decision_aliases() -> None:
    findings = check_source(
        """
def consume():
    decision = ask_typed_model_decision()
    first = decision
    second = first
    return second["action"]
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


def test_w3s1_allows_typed_union_dispatch_and_typed_attributes() -> None:
    assert check_source(
        """
from agent.llm.admitted_decisions import ReactiveFinalDecision, ReactiveToolDecision

def consume(decision: ReactiveToolDecision | ReactiveFinalDecision):
    if isinstance(decision, ReactiveFinalDecision):
        return decision.answer
    return decision.tool
""",
        "agent/planning/adversarial.py",
    ) == []


def test_w3s1_allows_unrelated_dicts_and_named_compatibility_edges() -> None:
    assert check_source(
        """
def unrelated(value: dict):
    return value.get("action")

def compatibility(value: dict):
    legacy = legacy_model_decision_compatibility(value, step_type="final")
    return legacy.get("answer") if legacy else None
""",
        "agent/planning/adversarial.py",
    ) == []


def test_w3s1_follows_module_qualified_typed_producer() -> None:
    findings = check_source(
        """
import agent.llm.admitted_decisions as decisions

def consume():
    value = decisions.admit_typed_model_decision({})
    alias = value
    return alias["tool"]
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


@pytest.mark.parametrize(
    "producer_setup",
    [
        "from agent.llm.admitted_decisions import ask_typed_model_decision as ask",
        "from agent.llm.admitted_decisions import ask_typed_model_decision\nask = ask_typed_model_decision",
        "from agent.llm.admitted_decisions import admit_typed_model_decision\nfirst = admit_typed_model_decision\nask = first",
    ],
)
def test_w3s1_follows_import_and_assignment_aliases_of_typed_producer(
    producer_setup: str,
) -> None:
    findings = check_source(
        f"""
{producer_setup}

def consume(ctx):
    decision = ask(ctx, "x")
    return decision.get("action")
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


@pytest.mark.parametrize(
    "projector_setup, projector_name",
    [
        (
            "from agent.llm.admitted_decisions import _project_exactly_admitted_model_decision as project",
            "project",
        ),
        (
            "from agent.llm.admitted_decisions import _project_exactly_admitted_model_decision\nproject = _project_exactly_admitted_model_decision",
            "project",
        ),
        (
            "from agent.llm.admitted_decisions import _project_exactly_admitted_model_decision\nfirst = _project_exactly_admitted_model_decision\nproject = first",
            "project",
        ),
    ],
)
def test_w3s1_confines_import_and_assignment_aliases_of_trusted_projector(
    projector_setup: str, projector_name: str
) -> None:
    findings = check_source(
        f"""
{projector_setup}

def bypass(raw, contract):
    return {projector_name}(raw, contract)
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


def test_w3s1_keeps_unrelated_aliased_callable_clean() -> None:
    assert check_source(
        """
from somewhere import transform as ask
first = ask
second = first

def consume(value):
    return second(value).get("action")
""",
        "agent/planning/adversarial.py",
    ) == []


def test_w3s1_keeps_trusted_exact_admission_boundary_clean() -> None:
    assert check_source(
        """
from agent.llm.admitted_decisions import _project_exactly_admitted_model_decision as project
alias = project

def admit(raw, contract):
    return alias(raw, contract)
""",
        "agent/llm/admitted_decisions.py",
    ) == []


@pytest.mark.parametrize(
    "helpers",
    [
        'def helper(x):\n    return x.get("action")',
        'def leaf(x):\n    return x.get("action")\n\ndef helper(x):\n    return leaf(x)',
    ],
)
def test_w3s1_follows_typed_decision_through_local_helper_hops(helpers: str) -> None:
    findings = check_source(
        f"""
from agent.llm.admitted_decisions import ReactiveToolDecision

{helpers}

def consume(decision: ReactiveToolDecision):
    return helper(decision)
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


def test_w3s1_helper_analysis_keeps_unrelated_controls_clean() -> None:
    assert check_source(
        """
def mapping_helper(value):
    return value.get("action")

def string_helper(value):
    return value.upper()

def consume(data: dict, text: str):
    return mapping_helper(data), string_helper(text)
""",
        "agent/planning/adversarial.py",
    ) == []


def test_w3s1_confines_trusted_projection_to_exact_admission_boundary() -> None:
    findings = check_source(
        """
def bypass(raw, contract):
    return _project_exactly_admitted_model_decision(raw, contract)
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S1:") for item in findings)


def test_w3s2_rejects_step_type_as_unbounded_model_policy() -> None:
    findings = check_source(
        """
def choose(step_type):
    grammars = {"plan": "plan", "final": "final"}
    return grammars.get(step_type)
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S2:") for item in findings)


def test_w3s2_rejects_model_call_without_explicit_contract() -> None:
    findings = check_source(
        """
def choose(context, prompt):
    return context.ask_model(prompt, step_type="plan")
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("W3-S2:") for item in findings)


def test_w3s2_allows_explicit_contract_and_non_model_kind() -> None:
    assert check_source(
        """
def choose(context, prompt, contract):
    response = context.ask_model(
        prompt, step_type="plan", request_contract=contract
    )
    kind = "tool"
    return kind
""",
        "agent/planning/adversarial.py",
    ) == []


@pytest.mark.parametrize(
    "expression",
    [
        'return step.get("tool")',
        'alias = step\n    return alias.get("tool")',
        'return step["tool"]',
        "return dict(step)",
        "return step.copy()",
    ],
)
def test_w3s3_rejects_raw_typed_plan_step_access(expression: str) -> None:
    findings = check_source(
        f"""
from agent.planning.plan_model import ToolPlanStep

def consume(step: ToolPlanStep):
    {expression}
""",
        "agent/planning/adversarial.py",
    )
    assert any(item.startswith("W3-S3:") for item in findings)


def test_w3s3_allows_serializer_and_unrelated_dict_controls() -> None:
    serializer = check_source(
        """
from agent.planning.plan_model import ToolPlanStep

def serialize(step: ToolPlanStep):
    return step.get("tool")
""",
        "agent/planning/plan_serializer.py",
    )
    unrelated = check_source(
        """
def consume(value: dict):
    return value.get("tool")
""",
        "agent/planning/adversarial.py",
    )
    assert serializer == []
    assert unrelated == []


def test_w3s4_allows_the_single_typed_ordinal_resolver() -> None:
    plan = Plan.from_raw(
        [
            {"tool": "source", "args": {}},
            {"tool": "sink", "args": {}},
        ]
    )
    assert resolve_previous_step_reference(
        PlanStepReference.from_ordinal(1), 1, plan
    ) == 0
    assert check_source(
        """
from agent.planning.plan_model import resolve_previous_step_reference

def consume(reference, index, plan):
    return resolve_previous_step_reference(reference, index, plan)
""",
        "agent/planning/adversarial.py",
    ) == []


def test_w3s4_rejects_duplicate_ordinal_and_json_path_resolution() -> None:
    findings = check_source(
        """
def resolve_reference(reference, index, plan):
    candidate = reference - 1
    return plan[candidate]

def resolve_path(data, path):
    for segment in path:
        data = data[segment]
    return data
""",
        "agent/planning/adversarial.py",
    )
    assert sum(item.startswith("W3-S4:") for item in findings) >= 2


@pytest.mark.parametrize(
    "source",
    [
        """
from agent.planning.plan_validator import PlanValidator

def build(value):
    return PlanValidator(value)
""",
        """
from agent.planning.plan_validator import PlanValidator as PV

def build(value):
    alias = PV
    return alias(value)
""",
        """
import agent.planning.plan_validator as validator_module

def build(value):
    return validator_module.PlanValidator(value)
""",
    ],
)
def test_w3s5_rejects_validator_composition_outside_owner(source: str) -> None:
    findings = check_source(source, "agent/planning/adversarial.py")
    assert any(item.startswith("W3-S5:") for item in findings)


def test_w3s5_allows_the_single_validator_composition_owner() -> None:
    assert check_source(
        """
from agent.planning.plan_validator import PlanValidator

def build(value):
    return PlanValidator(value)
""",
        "agent/planning/plan_admission.py",
    ) == []


def test_w3s6_requires_an_explicit_admission_mode() -> None:
    findings = check_source(
        """
from agent.planning.plan_admission import PlanAdmissionService

def build(orchestrator, plan, objective):
    admission = PlanAdmissionService(orchestrator)
    return admission.admit(plan, objective)
""",
        "agent/planning/adversarial.py",
    )
    assert any(item.startswith("W3-S6:") for item in findings)


def test_w3s6_allows_explicit_admission_mode() -> None:
    assert check_source(
        """
from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService

def build(orchestrator, plan, objective):
    admission = PlanAdmissionService(orchestrator)
    return admission.admit(plan, objective, mode=PlanAdmissionMode.INITIAL)
""",
        "agent/planning/adversarial.py",
    ) == []
