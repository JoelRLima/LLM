from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.execution_state import StepStatus
from agent.memory.memory import AgentMemory
from agent.memory.prompt_context import build_memory_prompt_context
from agent.planning.capability_manifest import render_validation_repair_manual
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from agent.planning.plan_executor import PlanExecutor
from agent.planning.plan_model import Plan
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_prompts import (
    PLANNING_GUIDANCE,
    build_reasoning_boundary_prompt,
)
from agent.planning.plan_validator import BlockedStep
from agent.planning.provenance_validation import grounded_user_literal_narrowing
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
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


@pytest.mark.parametrize(
    "child_target",
    [
        "project/build/generated.py",
        "project/.hidden/hidden.py",
        "project/broken.py",
        "project/missing.py",
    ],
)
def test_directory_analysis_never_dominates_runtime_specific_child(
    child_target: str,
) -> None:
    directory = _analyzer("project", mode="directory", compact=True)
    child = _analyzer(child_target, mode="file", compact=True)

    report = PlanOptimizer().optimize([directory, child])

    assert report.optimized_steps == [directory, child]
    assert report.removed_duplicates == 0


def test_directory_child_is_not_removed_across_mutation() -> None:
    directory = _analyzer("project", mode="directory", compact=True)
    mutation = {
        "tool": "file_writer",
        "args": {"action": "write", "file_path": "project/config.py"},
    }
    child = _analyzer("project/config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([directory, mutation, child])

    assert report.optimized_steps == [directory, mutation, child]
    assert report.removed_duplicates == 0


def test_equal_analyzer_after_mutation_is_not_deduplicated() -> None:
    first = _analyzer("project/config.py", mode="file", compact=True)
    mutation = {
        "tool": "file_writer",
        "args": {"action": "write", "file_path": "project/config.py"},
    }
    second = _analyzer("project/config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([first, mutation, second])

    assert report.optimized_steps == [first, mutation, second]
    assert report.removed_duplicates == 0


def test_equal_analyzer_separated_by_read_only_step_can_be_deduplicated() -> None:
    first = _analyzer("project/config.py", mode="file", compact=True)
    unrelated = {"tool": "file_reader", "args": {"file_path": "project/README.md"}}
    second = _analyzer("project/config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([first, unrelated, second])

    assert report.optimized_steps == [first, unrelated]
    assert report.removed_duplicates == 1


def test_code_analyzer_runtime_directory_map_omits_noncanonical_children(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "visible.py").write_text("def visible():\n    return True\n", encoding="utf-8")
    (project / "build").mkdir()
    (project / "build" / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
    (project / ".hidden").mkdir()
    (project / ".hidden" / "hidden.py").write_text("def hidden():\n    return True\n", encoding="utf-8")
    (project / "broken.py").write_text("def broken(:\n    return False\n", encoding="utf-8")

    skill = CodeAnalyzerSkill(str(workspace))
    directory = skill.execute({"target": "project", "mode": "directory", "compact": True})
    generated = skill.execute({"target": "project/build/generated.py", "mode": "file", "compact": True})
    hidden = skill.execute({"target": "project/.hidden/hidden.py", "mode": "file", "compact": True})
    broken = skill.execute({"target": "project/broken.py", "mode": "file", "compact": True})
    missing = skill.execute({"target": "project/missing.py", "mode": "file", "compact": True})

    assert {item.replace("\\", "/") for item in directory["data"]["files"]} == {
        "project/visible.py"
    }
    assert generated["ok"] is True
    assert hidden["ok"] is True
    assert broken["ok"] is False
    assert missing["ok"] is False


def test_referenced_child_producer_remains_present() -> None:
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


def test_child_with_own_binding_remains_present() -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)
    child = _analyzer(
        "project/atlas/config.py",
        mode="file",
        compact=True,
    )
    child["bindings"] = {"target": {"from_step": 1, "path": []}}

    report = PlanOptimizer().optimize([directory, child])

    assert report.optimized_steps == [directory, child]


def test_optimizer_keeps_order_without_directory_child_elimination() -> None:
    directory = _analyzer("project/atlas", mode="directory", compact=True)
    unrelated = {"tool": "file_reader", "args": {"file_path": "project/main.py"}}
    child = _analyzer("project/atlas/config.py", mode="file", compact=True)

    report = PlanOptimizer().optimize([directory, unrelated, child])

    assert [step["tool"] for step in report.optimized_steps] == [
        "code_analyzer",
        "file_reader",
        "code_analyzer",
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
    ("objective", "rejected", "expected"),
    [
        ("Onde a função format_name é usada?", "def format_name", "format_name"),
        ("Onde a função format_name é usada?", r"format_name\(", "format_name"),
        ('Procure "alpha beta" neste projeto.', "^alpha beta$", "alpha beta"),
        ("Procure `foo.*bar`.", "^foo.*bar$", "foo.*bar"),
        ("Procure função_média no projeto.", "^função_média$", "função_média"),
        ('Procure "alpha beta gamma".', "^alpha beta gamma$", "alpha beta gamma"),
    ],
)
def test_grounded_grep_narrowing_prefers_complete_explicit_or_unicode_literal(
    objective: str, rejected: str, expected: str
) -> None:
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


def test_grounded_grep_narrowing_rejects_unrelated_pattern() -> None:
    assert grounded_user_literal_narrowing(
        rejected_value="password|secret",
        objective="Onde format_name é usada?",
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


def test_validation_repair_manual_does_not_require_unavailable_objective_context() -> None:
    manual = render_validation_repair_manual(
        SimpleNamespace(operational_mode_label="FULL"),
        tool="grep",
        frozen_args={"path": "."},
        repairable_fields={"pattern"},
    )

    assert "objective" not in manual.lower()
    assert "never invent regex" in manual
    assert "array result" in manual


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
    plan = Plan.from_raw([
        {
            "tool": "grep",
            "args": {"path": ".", "pattern": "def format_name"},
        }
    ])
    budget = {"remaining": 0}

    accepted = ExecutionGateway(orchestrator)._replace_blocked_step(
        plan,
        objective,
        BlockedStep(0, "Argumento 'pattern' requer proveniencia fundamentada", frozenset({"pattern"})),
        repair_budget=budget,
    )

    assert accepted is True
    assert orchestrator.agent_state.plan[0]["args"]["pattern"] == "format_name"
    assert budget == {"remaining": 0}
    assert model_calls == []


@pytest.mark.parametrize(
    ("objective", "rejected"),
    [
        ("Find class Foo in project.", "^class Foo$"),
        ("Procure alpha beta neste projeto.", "^alpha beta$"),
    ],
)
def test_grounded_grep_narrowing_fails_closed_for_ambiguous_bare_literals(
    objective: str, rejected: str
) -> None:
    assert grounded_user_literal_narrowing(
        rejected_value=rejected,
        objective=objective,
    ) is None


@pytest.mark.parametrize(
    ("objective", "rejected", "expected"),
    [
        ("Procure format_name neste projeto.", "^format_name$", "format_name"),
        ("Procure `alpha beta`.", "^alpha beta$", "alpha beta"),
    ],
)
def test_grounded_grep_narrowing_keeps_single_bare_and_delimited_literals(
    objective: str, rejected: str, expected: str
) -> None:
    assert grounded_user_literal_narrowing(
        rejected_value=rejected,
        objective=objective,
    ) == expected


def test_bound_analyzer_does_not_seed_semantic_deduplication() -> None:
    producer = {
        "tool": "echo",
        "args": {"text": True},
        "_step_id": "producer",
    }
    bound = _analyzer("x.py")
    bound["_step_id"] = "bound"
    bound["bindings"] = {
        "include_code": {"from_step": "producer", "path": []},
    }
    unbound = _analyzer("x.py", include_code=False)
    unbound["_step_id"] = "unbound"

    report = PlanOptimizer().optimize([producer, bound, unbound])

    assert report.optimized_steps == [producer, bound, unbound]
    assert report.removed_duplicates == 0


class _ExecutionSkill:
    def get_schema(self):
        return {}


class _ExecutionWorkspace:
    def create_restore_point(self, _plan):
        return None

    def show_diff(self, _file_path, _content):
        return None

    def lint_check(self, _file_path):
        return None


class _ExecutionContext:
    def __init__(self, state: AgentState):
        self.agent_state = state
        self.skills = {
            name: _ExecutionSkill()
            for name in ("code_analyzer", "file_reader", "file_writer")
        }
        self.active_skills = list(self.skills)
        self.verbose = False
        self.workspace = _ExecutionWorkspace()
        self.context_manager = SimpleNamespace(maybe_compress_context=lambda: None)
        self.cancellation_token = CancellationToken()
        self.session = SimpleNamespace(
            config={
                "max_task_steps": 100,
                "max_task_tokens": 100_000,
                "max_task_tool_calls": 100,
                "max_task_wall_seconds": 3600,
                "max_repeated_no_progress": 10,
                "max_consecutive_same_error": 10,
                "max_reasoning_turns": 1,
            }
        )
        self._task_start_time = None
        self.plan_builder = SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                kind=PlanningDecisionKind.COMPLETE
            )
        )
        self.calls = []
        self.events = []
        self.failed = False

    def _emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))

    def _run_tool(self, tool_name, args):
        self.calls.append((tool_name, dict(args)))
        result = {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": {"tool": tool_name, "target": args.get("target") or args.get("file_path")},
        }
        if tool_name == "file_reader":
            result["data"] = "fresh source"
            result["total_lines"] = 1
        self.agent_state.record_tool_result(tool_name, args, result)
        return result

    def _handle_step_failure(self, *_args, **_kwargs):
        return "continue"

    def _purge_stale_context(self):
        return None

    def _maybe_summarize_and_store(self, _tool, _args, _result):
        return None

    def fail_task(self):
        self.failed = True


def _execute_optimized_plan(
    plan: list[dict[str, object]], memory: AgentMemory | None = None
):
    report = PlanOptimizer().optimize(plan)
    state = AgentState(memory=memory)
    state.set_plan(report.optimized_steps)
    context = _ExecutionContext(state)
    return report, state, context


def test_mutation_invalidates_execution_repetition_and_cache_state(tmp_path) -> None:
    plan = [
        _analyzer("x.py"),
        {
            "tool": "file_writer",
            "args": {"file_path": "x.txt", "action": "write", "content": "changed"},
        },
        _analyzer("x.py"),
    ]
    memory = AgentMemory(
        db_path=tmp_path / "memory.db",
        default_file=tmp_path / "memory.json",
        backup_dir=tmp_path / "backups",
    )
    report, state, context = _execute_optimized_plan(plan, memory)
    state.memory.state["file_hashes"] = {"x.py": "stale"}
    state.memory.state["file_cache_entries"] = {"x.py": {"data": "stale"}}
    usage: dict[str, int] = {}

    assert len(report.optimized_steps) == 3
    assert PlanExecutor(context).execute("reanalisar depois da escrita", usage) is None
    assert [tool for tool, _args in context.calls] == [
        "code_analyzer",
        "file_writer",
        "code_analyzer",
    ]
    assert [state.get_step_status(index) for index in range(3)] == [
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
    ]
    assert state.memory.state["file_hashes"] == {}
    assert state.memory.state["file_cache_entries"] == {}


def test_reader_after_mutation_is_not_blocked_by_fully_read_marker(tmp_path) -> None:
    plan = [
        {"tool": "file_reader", "args": {"file_path": "x.py"}},
        {
            "tool": "file_writer",
            "args": {"file_path": "x.txt", "action": "write", "content": "changed"},
        },
        {"tool": "file_reader", "args": {"file_path": "x.py"}},
    ]
    memory = AgentMemory(
        db_path=tmp_path / "memory.db",
        default_file=tmp_path / "memory.json",
        backup_dir=tmp_path / "backups",
    )
    report, state, context = _execute_optimized_plan(plan, memory)
    usage: dict[str, int] = {}

    assert len(report.optimized_steps) == 3
    assert PlanExecutor(context).execute("reler depois da escrita", usage) is None
    assert [tool for tool, _args in context.calls] == [
        "file_reader",
        "file_writer",
        "file_reader",
    ]
    assert [state.get_step_status(index) for index in range(3)] == [
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
    ]


def test_without_mutation_optimizer_and_policy_still_block_repetition() -> None:
    duplicate_plan = [_analyzer("x.py"), _analyzer("x.py")]
    report, _state, context = _execute_optimized_plan(duplicate_plan)

    assert len(report.optimized_steps) == 1
    assert report.removed_duplicates == 1
    assert PlanExecutor(context).execute("analisar", {}) is None
    assert [tool for tool, _args in context.calls] == ["code_analyzer"]

    state = AgentState()
    state.set_plan(duplicate_plan)
    direct_context = _ExecutionContext(state)
    assert PlanExecutor(direct_context).execute("analisar", {}) is None
    assert [tool for tool, _args in direct_context.calls] == ["code_analyzer"]
    assert state.get_step_status(1) is StepStatus.SKIPPED


class _ObservationMemory:
    def __init__(self) -> None:
        self.state = {
            "file_summaries": {
                "x.py": "old summary x",
                "y.py": "old summary y",
            },
            "analyzed_files": {
                "x.py": "old index x",
                "y.py": "old index y",
            },
            "file_hashes": {"x.py": "hash-x", "y.py": "hash-y"},
            "file_cache_entries": {
                "x.py": {"data": "cache-x"},
                "y.py": {"data": "cache-y"},
            },
        }
        self.forget_calls: list[tuple[str, str]] = []

    def forget(self, key: str, section: str = "key_findings") -> None:
        self.forget_calls.append((key, section))
        self.state.setdefault(section, {}).pop(key, None)

    def invalidate_file_observation(self, key: str) -> None:
        self.forget(key, section="file_summaries")
        for section in ("analyzed_files", "file_hashes", "file_cache_entries"):
            self.state.setdefault(section, {}).pop(key, None)


def _observation_context(
    memory: object, tool: str, args: dict[str, object]
) -> tuple[AgentState, _ExecutionContext]:
    state = AgentState(memory=memory)  # type: ignore[arg-type]
    state.set_plan([{"tool": tool, "args": args}])
    state.mark_step_running(0)
    return state, _ExecutionContext(state)


def _observation_usage() -> dict[str, int]:
    return {
        "code_analyzer_x.py": 1,
        "file_reader_x.py_1_2": 1,
        "fully_read_x.py": 1,
        "fully_analyzed_x.py": 1,
        "code_analyzer_y.py": 1,
        "file_reader_y.py_1_2": 1,
        "fully_read_y.py": 1,
        "fully_analyzed_y.py": 1,
    }


def test_successful_persisted_mutation_invalidates_derived_memory_and_prompt(
    tmp_path,
) -> None:
    memory = AgentMemory(
        db_path=tmp_path / "memory.db",
        default_file=tmp_path / "memory.json",
        backup_dir=tmp_path / "backups",
    )
    memory.remember("x.py", "old summary x", section="file_summaries")
    memory.remember("y.py", "old summary y", section="file_summaries")
    memory.state["analyzed_files"] = {"x.py": "old index x", "y.py": "old index y"}
    memory.state["file_hashes"] = {"x.py": "hash-x", "y.py": "hash-y"}
    memory.state["file_cache_entries"] = {
        "x.py": {"data": "cache-x"},
        "y.py": {"data": "cache-y"},
    }
    state, context = _observation_context(
        memory,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
    )
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "mutation_occurred": True,
            "persisted_mutation": True,
            "surviving_mutation": True,
            "affected_files": ["x.py"],
        },
        "",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.COMPLETED
    assert "x.py" not in state.memory.state["file_summaries"]
    assert "x.py" not in state.memory.state["analyzed_files"]
    assert "x.py" not in state.memory.state["file_hashes"]
    assert "x.py" not in state.memory.state["file_cache_entries"]
    assert "y.py" in state.memory.state["file_summaries"]
    assert "y.py" in state.memory.state["analyzed_files"]
    assert "y.py" in state.memory.state["file_hashes"]
    assert "y.py" in state.memory.state["file_cache_entries"]
    assert not any("x.py" in key for key in usage)
    assert any("y.py" in key for key in usage)

    prompt = build_memory_prompt_context(state.memory.state, "x.py", 800)
    assert "old summary x" not in prompt
    assert "old index x" not in prompt

    reloaded = AgentMemory(
        db_path=tmp_path / "memory.db",
        default_file=tmp_path / "memory.json",
        backup_dir=tmp_path / "backups",
    )
    reloaded.initialize()
    assert "x.py" not in reloaded.state["file_summaries"]
    assert reloaded.state["file_summaries"]["y.py"] == "old summary y"


def test_unverified_surviving_mutation_invalidates_only_affected_observations() -> None:
    memory = _ObservationMemory()
    state, context = _observation_context(
        memory,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
    )
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
        {
            "ok": False,
            "done": True,
            "status": "unverified",
            "persisted_mutation": True,
            "surviving_mutation": True,
            "affected_files": ["x.py"],
            "error": "validation unavailable",
        },
        "",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.UNVERIFIED
    assert memory.forget_calls == [("x.py", "file_summaries")]
    assert "x.py" not in memory.state["file_summaries"]
    assert "x.py" not in memory.state["analyzed_files"]
    assert "x.py" not in memory.state["file_hashes"]
    assert "x.py" not in memory.state["file_cache_entries"]
    assert not any("x.py" in key for key in usage)
    assert any("y.py" in key for key in usage)
    assert "old summary x" not in build_memory_prompt_context(
        state.memory.state, "x.py", 800
    )


def test_unverified_gateway_artifact_projection_invalidates_affected_observations() -> None:
    memory = _ObservationMemory()
    _state, context = _observation_context(
        memory,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
    )
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
        {
            "ok": False,
            "done": True,
            "status": "unverified",
            "artifacts": [{
                "metadata": {
                    "persisted_mutation": True,
                    "surviving_mutation": True,
                    "affected_files": ["x.py"],
                }
            }],
        },
        "",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.UNVERIFIED
    assert "x.py" not in memory.state["file_summaries"]
    assert "y.py" in memory.state["file_summaries"]
    assert not any("x.py" in key for key in usage)
    assert any("y.py" in key for key in usage)


def test_unverified_rollback_does_not_invalidate_restored_observations() -> None:
    memory = _ObservationMemory()
    state, context = _observation_context(
        memory,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
    )
    before = {
        section: dict(values)
        for section, values in memory.state.items()
        if isinstance(values, dict)
    }
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "code_task",
        {"action": "modify", "targets": ["x.py"]},
        {
            "ok": False,
            "done": True,
            "status": "unverified",
            "mutation_occurred": True,
            "rollback_occurred": True,
            "persisted_mutation": False,
            "surviving_mutation": False,
            "affected_files": ["x.py"],
            "final_state": "restored",
        },
        "",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.UNVERIFIED
    assert memory.state == before
    assert memory.forget_calls == []
    assert usage == _observation_usage()


@pytest.mark.parametrize("action", ["analyze", "review"])
def test_read_only_code_task_effect_projection_does_not_invalidate(
    action: str,
) -> None:
    memory = _ObservationMemory()
    state, context = _observation_context(
        memory,
        "code_task",
        {"action": action, "targets": ["x.py"]},
    )
    before = {
        section: dict(values)
        for section, values in memory.state.items()
        if isinstance(values, dict)
    }
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "code_task",
        {"action": action, "targets": ["x.py"]},
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "mutation_occurred": False,
            "persisted_mutation": False,
            "surviving_mutation": False,
            "affected_files": [],
        },
        "",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.COMPLETED
    assert memory.state == before
    assert memory.forget_calls == []
    assert usage == _observation_usage()


def test_writer_success_keeps_conservative_invalidation_fallback() -> None:
    memory = _ObservationMemory()
    state, context = _observation_context(
        memory,
        "file_writer",
        {"action": "write", "file_path": "x.py", "content": "new"},
    )
    usage = _observation_usage()

    outcome = StepExecutor(context).finalize_result(
        0,
        "file_writer",
        {"action": "write", "file_path": "x.py", "content": "new"},
        {"ok": True, "done": True, "status": "succeeded"},
        "x.py",
        "x.py",
        usage,
    )

    assert outcome.kind is StepOutcomeKind.COMPLETED
    assert all(not memory.state.get(section) for section in memory.state)
    assert sorted(memory.forget_calls) == [
        ("x.py", "file_summaries"),
        ("y.py", "file_summaries"),
    ]
    assert usage == {}
