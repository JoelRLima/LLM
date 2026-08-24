from types import SimpleNamespace

import pytest

from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_prompts import (
    PLANNING_GUIDANCE,
    build_reasoning_boundary_prompt,
)
from agent.planning.plan_validator import BlockedStep
from agent.planning.provenance_validation import grounded_user_literal_narrowing
from agent.skills import load_skill_registry
from agent.skills.code_analyzer import CodeAnalyzerSkill
from agent.skills.grep import GrepSkill
from agent.state import AgentState
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.tool_registry import ToolRegistry


def _analyzer(target: str, **args: object) -> dict[str, object]:
    return {
        "tool": "code_analyzer",
        "args": {"target": target, **args},
    }


def test_code_analyzer_default_is_semantic_for_deduplication() -> None:
    report = PlanOptimizer().optimize(
        [
            _analyzer("project", mode="directory", compact=True),
            _analyzer(
                "project",
                mode="directory",
                compact=True,
                include_code=False,
            ),
        ]
    )

    assert len(report.optimized_steps) == 1
    assert report.removed_duplicates == 1
    assert "duplicata semântica" in report.transformations[0]


def test_directory_analysis_dominates_compatible_python_child() -> None:
    plan = [
        _analyzer("project/atlas", mode="directory", compact=True),
        _analyzer("project/atlas/config.py", mode="file", compact=True),
    ]

    report = PlanOptimizer().optimize(plan)

    assert report.optimized_steps == plan[:1]
    assert report.removed_duplicates == 1
    assert "coberta" in report.transformations[0]


@pytest.mark.parametrize(
    "child",
    [
        _analyzer("project/atlas/config.py", mode="file", compact=False),
        _analyzer(
            "project/atlas/config.py",
            mode="file",
            compact=True,
            include_code=True,
        ),
        _analyzer("project/main.py", mode="file", compact=True),
        _analyzer("project/atlas/config.txt", mode="file", compact=True),
        _analyzer("project/atlas/config.py", mode="security", compact=True),
        _analyzer("project/atlas/../config.py", mode="file", compact=True),
    ],
)
def test_directory_dominance_is_conservative(child: dict[str, object]) -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)

    report = PlanOptimizer().optimize([directory, child])

    assert report.optimized_steps == [directory, child]
    assert report.removed_duplicates == 0


def test_directory_dominance_handles_windows_separators_without_filesystem_access() -> None:
    directory = _analyzer(r"project\atlas", mode="directory", compact=True)
    child = _analyzer(r"project\atlas\config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([directory, child])

    assert report.optimized_steps == [directory]


def test_directory_dominance_preserves_referenced_child_producer() -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)
    child = _analyzer(
        "project/atlas/config.py",
        mode="file",
        compact=True,
    )
    child["_step_id"] = "child-analysis"
    consumer = {
        "tool": "grep",
        "args": {"path": "."},
        "bindings": {"pattern": {"from_step": "child-analysis", "path": []}},
    }

    report = PlanOptimizer().optimize([directory, child, consumer])

    assert report.optimized_steps == [directory, child, consumer]
    assert report.removed_duplicates == 0


def test_directory_dominance_preserves_child_with_own_binding() -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)
    child = _analyzer(
        "project/atlas/config.py",
        mode="file",
        compact=True,
    )
    child["bindings"] = {"target": {"from_step": 1, "path": []}}

    report = PlanOptimizer().optimize([directory, child])

    assert report.optimized_steps == [directory, child]


def test_optimizer_keeps_order_when_directory_dominance_removes_child() -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)
    unrelated = {"tool": "file_reader", "args": {"file_path": "project/main.py"}}
    child = _analyzer("project/atlas/config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([directory, unrelated, child])

    assert [step["tool"] for step in report.optimized_steps] == [
        "code_analyzer",
        "file_reader",
    ]
    assert report.optimized_steps[1] == unrelated


@pytest.mark.parametrize(
    ("rejected", "expected"),
    [
        ("format_name", "format_name"),
        ("def format_name", "format_name"),
        (r"format_name\(", "format_name"),
        (r"^format_name$", "format_name"),
        ("format_name|password", "format_name"),
        ('"format_name"', "format_name"),
        ("`format_name`", "format_name"),
    ],
)
def test_grounded_grep_narrowing_returns_only_user_literal(
    rejected: str, expected: str
) -> None:
    objective = "Onde a função format_name é usada neste projeto?"

    assert grounded_user_literal_narrowing(
        rejected_value=rejected,
        objective=objective,
    ) == expected


@pytest.mark.parametrize(
    "rejected",
    [
        r"^\s*\S+\s*$",
        "password|secret",
        "",
        None,
        ["format_name"],
    ],
)
def test_grounded_grep_narrowing_rejects_ungrounded_values(rejected: object) -> None:
    assert grounded_user_literal_narrowing(
        rejected_value=rejected,
        objective="Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
    ) is None


def test_grounded_grep_narrowing_does_not_use_generic_sentence_word() -> None:
    assert grounded_user_literal_narrowing(
        rejected_value="def palavra",
        objective="Procure a palavra observada nos arquivos.",
    ) is None


def test_planning_guidance_teaches_smallest_sufficient_evidence() -> None:
    assert "smallest sufficient evidence" in PLANNING_GUIDANCE
    assert "discovery antes de fan-out" in PLANNING_GUIDANCE
    assert "DERIVED_LOSSY" in PLANNING_GUIDANCE
    assert "valores/conteúdo exatos" in PLANNING_GUIDANCE
    assert "literal exato" in PLANNING_GUIDANCE

    boundary = build_reasoning_boundary_prompt("objective", "observation", "progress", "tools")
    assert "não repita discovery" in boundary
    assert "complete" in boundary
    assert "próxima evidência relevante" in boundary


def test_tool_descriptions_expose_evidence_boundaries() -> None:
    assert "DERIVED_LOSSY" in CodeAnalyzerSkill.description
    assert "valores literais" in CodeAnalyzerSkill.description
    assert "file_reader" in CodeAnalyzerSkill.description
    assert "literal" in GrepSkill.description
    assert "provenance" in GrepSkill.description


def _repair_orchestrator(tmp_path: object) -> tuple[SimpleNamespace, list[str]]:
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    state = AgentState()
    model_calls: list[str] = []

    class _Context:
        def ask_model(self, prompt: str, *_args: object, **_kwargs: object) -> object:
            model_calls.append(prompt)
            raise AssertionError("LLM repair não deveria ser chamado")

    orchestrator = SimpleNamespace(
        skills={},
        active_skills=list(skills.names()),
        allowed_capabilities=frozenset({"read"}),
        tool_registry=registry,
        agent_state=state,
        context_manager=_Context(),
        events=[],
        failed=False,
        verbose=False,
    )
    orchestrator._emit = lambda event, data=None: orchestrator.events.append((event, data or {}))
    orchestrator.fail_task = lambda: setattr(orchestrator, "failed", True)
    return orchestrator, model_calls


def test_two_grounded_grep_repairs_happen_before_llm_budget_and_then_dedup(tmp_path) -> None:
    orchestrator, model_calls = _repair_orchestrator(tmp_path)
    objective = "Onde a função format_name é usada neste projeto? Quero saber onde ela é definida e onde é chamada."
    plan = [
        {
            "tool": "grep",
            "args": {
                "path": ".",
                "pattern": "def format_name",
                "recursive": True,
                "max_results": 20,
            },
        },
        {
            "tool": "grep",
            "args": {
                "path": ".",
                "pattern": r"format_name\(",
                "recursive": True,
                "max_results": 20,
            },
        },
    ]

    validated = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        plan,
        objective,
    )

    assert validated is not None
    assert len(validated) == 1
    assert validated[0]["tool"] == "grep"
    assert validated[0]["args"]["pattern"] == "format_name"
    assert model_calls == []
    repairs = [event for event in orchestrator.events if event[0] == "validation_repair"]
    assert len(repairs) == 2
    assert all(item[1]["strategy"] == "deterministic_grounded_literal" for item in repairs)


def test_deterministic_grounded_repair_does_not_consume_zero_llm_budget(tmp_path) -> None:
    orchestrator, model_calls = _repair_orchestrator(tmp_path)
    objective = "Onde a função format_name é usada neste projeto?"
    plan = [
        {
            "tool": "grep",
            "args": {"path": ".", "pattern": "def format_name"},
        }
    ]
    budget = {"remaining": 0}

    accepted = ExecutionGateway(orchestrator)._replace_blocked_step(
        plan,
        objective,
        BlockedStep(0, "Argumento 'pattern' requer proveniencia fundamentada", frozenset({"pattern"})),
        repair_budget=budget,
    )

    assert accepted is True
    assert plan[0]["args"]["pattern"] == "format_name"
    assert budget == {"remaining": 0}
    assert model_calls == []
