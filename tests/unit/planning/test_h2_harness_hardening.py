import json
from types import SimpleNamespace

from agent.orchestration.operations import OrchestratorOperations
from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder
from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import (
    PlanningContextSnapshot,
    PlanningTool,
    build_planning_context,
)
from agent.planning.replan import ReplanContext, ask_llm_for_alternative, replan
from agent.planning.result_bindings import (
    bind_result_references,
    resolve_bound_args,
    validate_result_bindings,
)
from agent.skills import load_skill_registry
from agent.state import AgentState
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolResult, ToolStatus
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
from agent.tools.tool_registry import ToolRegistry


def _grep_validator(tmp_path, objective=""):
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    return PlanValidator(
        {},
        list(skills.names()),
        frozenset({"read"}),
        registry,
        objective=objective,
    )


def _successful_observation(data, *, complete=True):
    artifacts = (
        [{"kind": "text_observation", "metadata": {"complete": complete}}]
        if complete is not None
        else []
    )
    return {
        "step_id": "reader",
        "result": {
            "ok": True,
            "executed": True,
            "status": "succeeded",
            "data": data,
            "artifacts": artifacts,
        },
    }


def test_h2_invented_pattern_is_blocked_before_execution(tmp_path):
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    state = AgentState()

    class _Executor:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1

    orchestrator = SimpleNamespace(
        skills={},
        active_skills=list(skills.names()),
        allowed_capabilities=frozenset({"read"}),
        tool_registry=registry,
        agent_state=state,
        plan_executor=_Executor(),
        events=[],
        failed=False,
    )
    orchestrator._emit = lambda event, data=None: orchestrator.events.append((event, data or {}))
    orchestrator.fail_task = lambda: setattr(orchestrator, "failed", True)

    bad = [
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        {
            "tool": "grep",
            "args": {"path": ".", "pattern": r"^\s*\S+\s*$"},
        },
    ]
    result = ExecutionGateway(orchestrator).execute_validated_plan(
        bad,
        "Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
        {},
    )

    assert result.aborted is True
    assert orchestrator.plan_executor.calls == 0
    assert any(event == "hard_block" for event, _data in orchestrator.events)


def test_result_binding_is_accepted_and_resolves_after_observation(tmp_path):
    validator = _grep_validator(tmp_path, "Leia fonte_h2.txt e procure pela palavra")
    plan = [
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        {
            "tool": "grep",
            "args": {"path": "."},
            "bindings": {"pattern": {"from_step": 1, "path": []}},
        },
    ]
    assert validator.validate(plan).is_valid
    ids = iter(("reader", "grep"))
    normalized = bind_result_references(plan, lambda: next(ids))
    canonical = _grep_validator(tmp_path, "Leia fonte_h2.txt e procure pela palavra")
    canonical.canonical_deferred_references = True
    assert canonical.validate(normalized).is_valid
    resolved = resolve_bound_args(
        normalized[1],
        1,
        normalized,
        [_successful_observation("orion_584271")],
    )
    assert resolved["pattern"] == "orion_584271"


def test_duplicate_binding_target_remains_invalid(tmp_path):
    validator = _grep_validator(tmp_path, "Leia fonte_h2.txt e procure pela palavra")
    duplicate = [
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        {
            "tool": "grep",
            "args": {
                "path": ".",
                "pattern": "fonte_h2.txt",
                "recursive": True,
                "max_results": 20,
            },
            "bindings": {"pattern": {"from_step": 1, "path": []}},
        },
    ]

    report = validator.validate(duplicate)

    assert not report.is_valid
    assert any("pattern" in error and "colide" in error for error in report.errors)


def test_user_and_observation_literals_are_allowed_but_incomplete_is_not(tmp_path):
    user = _grep_validator(tmp_path, "Procure por banana nos arquivos do workspace")
    literal = [{"tool": "grep", "args": {"path": ".", "pattern": "banana"}}]
    assert user.validate(literal).is_valid

    observation = _grep_validator(tmp_path, "Procure a palavra observada")
    observation.available_observations = (_successful_observation("orion_584271"),)
    assert observation.validate(
        [{"tool": "grep", "args": {"path": ".", "pattern": "orion_584271"}}]
    ).is_valid

    incomplete = _grep_validator(tmp_path, "Procure a palavra observada")
    incomplete.available_observations = (_successful_observation("orion_584271", complete=False),)
    report = incomplete.validate(
        [{"tool": "grep", "args": {"path": ".", "pattern": "orion_584271"}}]
    )
    assert not report.is_valid
    assert report.blocked_steps
    missing = _grep_validator(tmp_path, "Procure nos arquivos")
    missing_report = missing.validate([{"tool": "grep", "args": {"path": "."}}])
    assert not missing_report.is_valid


def test_observation_literal_accepts_canonical_tool_result_object(tmp_path):
    validator = _grep_validator(tmp_path, "Procure a palavra observada")
    validator.available_observations = (
        {
            "result": ToolResult(
                invocation_id="reader-1",
                status=ToolStatus.SUCCEEDED,
                data="orion_584271",
                artifacts=(
                    {"kind": "text_observation", "metadata": {"complete": True}},
                ),
                executed=True,
            )
        },
    )
    report = validator.validate(
        [{"tool": "grep", "args": {"path": ".", "pattern": "orion_584271"}}]
    )
    assert report.is_valid


def test_non_protected_arguments_and_static_reads_remain_valid(tmp_path):
    validator = _grep_validator(tmp_path, "Leia a.txt e b.txt e compare os conteudos")
    assert validator.validate(
        [{"tool": "file_reader", "args": {"file_path": "a.txt"}}]
    ).is_valid
    skills = load_skill_registry(base_dir=tmp_path)
    non_protected = PlanValidator(
        {"echo": skills.skill("echo")}, ["echo"], frozenset(), None
    )
    assert non_protected.validate(
        [{"tool": "echo", "args": {"message": "banana"}}]
    ).is_valid


def test_capability_manifest_is_derived_from_presented_tools_and_authority():
    identity = RuntimeSnapshotIdentity("registry", "workspace")
    context = PlanningContextSnapshot(
        snapshot_id="context",
        registry_identity="registry",
        authority_identity="authority",
        runtime_identity=identity,
        tools=(PlanningTool(name="grep", description="search", required_capabilities=frozenset({"read"})),),
        eligible_names=frozenset({"grep"}),
        allowed_capabilities=frozenset({"read"}),
    )
    orchestrator = SimpleNamespace(
        planning_context=context,
        get_planning_view=lambda kind: context.present(kind),
        operational_mode_label="READ ONLY",
        allowed_capabilities=frozenset({"read"}),
        execution_gateway=SimpleNamespace(_bind_deferred_references=lambda plan: plan, _recover=lambda *args: None),
        plan_builder=SimpleNamespace(continue_after_observation=lambda *args: None, continue_after_reasoning_boundary=lambda *args: None),
        agent_state=SimpleNamespace(continuation_attempts=0),
        session=SimpleNamespace(config={"max_reasoning_turns": 3}),
        tool_invocation_gateway=object(),
    )
    manifest = render_active_harness_capabilities(orchestrator)
    assert "ACTIVE HARNESS CAPABILITIES" in manifest
    assert "READ ONLY" in manifest
    assert "prior-result binding" in manifest
    assert "semantic continuation" in manifest
    assert "mutation-capable tools are unavailable" in manifest
    assert "grep" in manifest
    assert "file_writer" not in manifest

    orchestrator.session = SimpleNamespace(
        config={"max_reasoning_turns": 3},
        hardware_profile=SimpleNamespace(context_limit=8192),
    )
    description = OrchestratorOperations._build_tools_description(
        orchestrator, compact=True, planner_kind="linear"
    )
    assert "ACTIVE HARNESS CAPABILITIES" in description
    assert "prior-result binding" in description

    editor_context = PlanningContextSnapshot(
        snapshot_id="editor-context",
        registry_identity="registry",
        authority_identity="authority",
        runtime_identity=identity,
        tools=(
            PlanningTool(
                name="code_task",
                description="edit",
                required_capabilities=frozenset({"read", "write", "validate"}),
            ),
        ),
        eligible_names=frozenset({"code_task"}),
        allowed_capabilities=frozenset({"read", "write", "validate"}),
    )
    editor = SimpleNamespace(
        planning_context=editor_context,
        get_planning_view=lambda kind: editor_context.present(kind),
        operational_mode_label="EDITOR",
        allowed_capabilities=frozenset({"read", "write", "validate"}),
        execution_gateway=orchestrator.execution_gateway,
        plan_builder=orchestrator.plan_builder,
        agent_state=orchestrator.agent_state,
        session=orchestrator.session,
        tool_invocation_gateway=object(),
    )
    editor_manifest = render_active_harness_capabilities(editor)
    assert "EDITOR" in editor_manifest
    assert "mutation-capable tools are active" in editor_manifest
    assert "code_task" in editor_manifest


def test_initial_h2_prompt_teaches_binding_as_a_model_facing_api():
    identity = RuntimeSnapshotIdentity("registry", "workspace")
    context = PlanningContextSnapshot(
        snapshot_id="h2-prompt",
        registry_identity="registry",
        authority_identity="authority",
        runtime_identity=identity,
        tools=(
            PlanningTool(name="file_reader", description="read", required_capabilities=frozenset({"read"})),
            PlanningTool(name="grep", description="search", required_capabilities=frozenset({"read"})),
        ),
        eligible_names=frozenset({"file_reader", "grep"}),
        allowed_capabilities=frozenset({"read"}),
    )

    class _Context:
        @staticmethod
        def get_file_hints(_objective: str) -> str:
            return ""

    orchestrator = SimpleNamespace(
        planning_context=context,
        get_planning_view=lambda kind: context.present(kind),
        operational_mode_label="READ ONLY",
        allowed_capabilities=frozenset({"read"}),
        execution_gateway=SimpleNamespace(
            _bind_deferred_references=lambda plan: plan,
            _recover=lambda *args: None,
        ),
        plan_builder=SimpleNamespace(
            continue_after_observation=lambda *args: None,
            continue_after_reasoning_boundary=lambda *args: None,
        ),
        agent_state=SimpleNamespace(continuation_attempts=0),
        session=SimpleNamespace(
            config={"max_reasoning_turns": 0},
            hardware_profile=SimpleNamespace(context_limit=8192),
        ),
        tool_invocation_gateway=object(),
        context_manager=_Context(),
    )
    orchestrator._build_tools_description = lambda compact=False, planner_kind=None: OrchestratorOperations._build_tools_description(
        orchestrator, compact, planner_kind=planner_kind
    )

    prompt = PlanBuilder(orchestrator)._build_prompt(
        "Leia fonte_h2.txt e depois procure nos outros arquivos do workspace pela palavra que ele contem."
    )

    assert "prior-result binding" in prompt
    assert "EXISTS:" in prompt and "WHEN:" in prompt and "HOW:" in prompt
    assert '"bindings":{"pattern":{"from_step":1,"path":[]}}' in prompt
    assert "known-now values go in args" in prompt
    assert "from_step: 1" in prompt and "public numbering is 1-based" in prompt
    assert "path: [] means the complete canonical ToolResult.data value" in prompt
    assert "${1.text}" in prompt and "Do not put a bound field in args" in prompt
    assert 'path":["text"]' not in prompt
    assert "file_reader" in prompt and "grep" in prompt


def test_binding_manual_is_not_advertised_when_gateway_lacks_binding_support():
    manifest = render_active_harness_capabilities(
        SimpleNamespace(
            planning_context=None,
            execution_gateway=SimpleNamespace(_recover=lambda *args: None),
            plan_builder=SimpleNamespace(),
            agent_state=SimpleNamespace(continuation_attempts=0),
            session=SimpleNamespace(config={}),
        )
    )
    assert "prior-result binding" not in manifest


def test_rendered_repair_right_example_matches_binding_grammar():
    from agent.llm.grammars import get_grammar
    from agent.llm.model_client import ModelClient
    from agent.parsers import validate_decision
    from agent.planning.capability_manifest import render_validation_repair_manual

    manual = render_validation_repair_manual(
        SimpleNamespace(
            execution_gateway=SimpleNamespace(_bind_deferred_references=lambda plan: plan),
        ),
        tool="grep",
        frozen_args={"path": ".", "recursive": True, "max_results": 20},
        repairable_fields=("pattern",),
        prior_steps=((1, {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}}),),
    )
    right_line = next(line for line in manual.splitlines() if line.startswith("RIGHT: "))
    right = json.loads(right_line.removeprefix("RIGHT: "))
    assert right["action"] == "tool"
    assert ModelClient._extract_decision(right_line.removeprefix("RIGHT: ")) == right
    valid, error = validate_decision(right)
    assert valid, error
    replan_grammar = get_grammar("replan", {"ENABLE_GBNF": True}) or ""
    assert "root ::= tool-decision" in replan_grammar
    assert "root ::= tool-decision | final-decision" not in replan_grammar
    assert "final-decision" not in replan_grammar
    assert right["bindings"]["pattern"] == {"from_step": 1, "path": []}
    assert "pattern" not in right["args"]
    assert "A binding satisfies its target argument." in manual
    assert "If a field is in bindings, omit that field from args." in manual
    assert "NEVER put the same argument name in both args and bindings" in manual
    assert "Repair the representation/source of pattern only." in manual
    assert validate_result_bindings(
        [
            {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
            {key: value for key, value in right.items() if key != "action"},
        ]
    ) == []


def test_h2_repair_context_uses_real_builtin_catalog_and_separates_input_from_result(tmp_path):
    identity = RuntimeSnapshotIdentity("h2-catalog", "workspace")
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry(runtime_identity=identity)
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    planning_context = build_planning_context(
        registry,
        ApplicationAuthoritySnapshot(runtime_identity=identity),
        TaskAuthoritySnapshot(
            allowed_capabilities=frozenset({"read"}),
            runtime_identity=identity,
        ),
        frozenset({"read"}),
    )
    catalog = planning_context.present(
        "linear", {"file_reader", "grep"}
    ).render(compact=True)

    class _Context:
        def ask_model(self, prompt, *_args, **kwargs):
            self.prompt = prompt
            self.base_prompt = kwargs["base_prompt"]
            self.kwargs = kwargs
            return {
                "action": "tool",
                "tool": "grep",
                "args": {"path": ".", "recursive": True, "max_results": 20},
                "bindings": {"pattern": {"from_step": 1, "path": []}},
            }

    context = _Context()
    action = ask_llm_for_alternative(
        {
            "tool": "grep",
            "args": {"path": ".", "recursive": True, "max_results": 20},
        },
        "deterministic validation rejected argument field(s): pattern; validator detail: pattern lacks grounded provenance",
        SimpleNamespace(
            context_manager=context,
            execution_gateway=SimpleNamespace(_bind_deferred_references=lambda plan: plan),
            _cached_base_prompt=catalog,
        ),
        validation_repair=True,
        repairable_fields=("pattern",),
        prior_steps=((1, {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}}),),
    )

    complete_context = context.base_prompt + "\n" + context.prompt
    assert action is not None
    assert context.kwargs["step_type"] == "replan"
    assert '"name":"file_reader"' in complete_context
    assert '"name":"grep"' in complete_context
    assert '"pattern":{"description"' in complete_context
    assert '"argument_provenance"' in complete_context
    assert "Repair the representation/source of pattern only." in complete_context
    assert "A binding satisfies its target argument." in complete_context
    assert "If a field is in bindings, omit that field from args." in complete_context
    assert "NEVER put the same argument name in both args and bindings" in complete_context
    assert "WRONG (pattern in both args and bindings):" in complete_context
    assert 'known input: file_path="fonte_h2.txt"' in complete_context
    assert "Shown inputs are not future results." in complete_context
    assert "future result: unavailable before execution." in complete_context
    assert "do not auto-bind" in complete_context

    right_line = next(line for line in context.prompt.splitlines() if line.startswith("RIGHT: "))
    right = json.loads(right_line.removeprefix("RIGHT: "))
    assert right["action"] == "tool"
    assert "pattern" not in right["args"]
    assert right["bindings"]["pattern"] == {"from_step": 1, "path": []}

    wrong_line = next(
        line
        for line in context.prompt.splitlines()
        if line.startswith('WRONG (pattern in both args and bindings): {"action":"tool","tool":"grep"')
        and '"pattern":"fonte_h2.txt"' in line
    )
    wrong = json.loads(wrong_line.split(": ", 1)[1])
    assert "pattern" in wrong["args"]
    assert "pattern" in wrong["bindings"]


def test_planning_catalog_exposes_descriptor_provenance_policy():
    identity = RuntimeSnapshotIdentity("registry", "workspace")
    context = PlanningContextSnapshot(
        snapshot_id="context-provenance",
        registry_identity="registry",
        authority_identity="authority",
        runtime_identity=identity,
        tools=(
            PlanningTool(
                name="grep",
                description="search",
                required_capabilities=frozenset({"read"}),
                argument_provenance={
                    "pattern": frozenset(
                        {"user_literal", "observation_literal", "result_binding"}
                    )
                },
            ),
        ),
        eligible_names=frozenset({"grep"}),
        allowed_capabilities=frozenset({"read"}),
    )
    rendered = context.present("linear").render(compact=True)
    assert '"argument_provenance"' in rendered
    assert '"result_binding"' in rendered


def test_replan_preserves_optional_binding_for_grounded_correction():
    decision = {
        "action": "tool",
        "tool": "grep",
        "args": {"path": "."},
        "bindings": {"pattern": {"from_step": 1, "path": []}},
    }

    class _Context:
        def ask_model(self, *_args, **_kwargs):
            return decision

    action = ask_llm_for_alternative(
        {"tool": "grep", "args": {"path": "."}},
        "Argumento pattern requer proveniencia fundamentada",
        SimpleNamespace(context_manager=_Context()),
    )
    assert action is not None
    assert action.steps[0]["bindings"]["pattern"]["from_step"] == 1


def test_h2_provenance_block_reuses_one_bounded_binding_correction(tmp_path):
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    state = AgentState()
    calls = []

    class _Context:
        def ask_model(self, *_args, **_kwargs):
            calls.append(1)
            return {
                "action": "tool",
                "tool": "grep",
                "args": {"path": "."},
                "bindings": {"pattern": {"from_step": 1, "path": []}},
            }

    class _Executor:
        def execute(self, *_args, **_kwargs):
            return None

    orchestrator = SimpleNamespace(
        skills={},
        active_skills=list(skills.names()),
        allowed_capabilities=frozenset({"read"}),
        tool_registry=registry,
        agent_state=state,
        context_manager=_Context(),
        plan_executor=_Executor(),
        events=[],
        failed=False,
        verbose=False,
    )
    orchestrator._emit = lambda event, data=None: orchestrator.events.append((event, data or {}))
    orchestrator.fail_task = lambda: setattr(orchestrator, "failed", True)
    plan = [
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        {"tool": "grep", "args": {"path": ".", "pattern": r"^\s*\S+\s*$"}},
    ]
    validated = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        plan,
        "Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
    )
    assert len(calls) == 1
    assert validated is not None
    assert validated[1].get("bindings", {}).get("pattern", {}).get("from_step") == 1


def _repair_orchestrator(tmp_path, decision):
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    state = AgentState()
    calls = []

    class _Context:
        def ask_model(self, prompt, *_args, **_kwargs):
            calls.append(prompt)
            return decision

    class _Executor:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1

    executor = _Executor()
    orchestrator = SimpleNamespace(
        skills={},
        active_skills=list(skills.names()),
        allowed_capabilities=frozenset({"read"}),
        tool_registry=registry,
        agent_state=state,
        context_manager=_Context(),
        plan_executor=executor,
        events=[],
        failed=False,
        verbose=False,
    )
    orchestrator._emit = lambda event, data=None: orchestrator.events.append((event, data or {}))
    orchestrator.fail_task = lambda: setattr(orchestrator, "failed", True)
    return orchestrator, calls, executor


def _h2_invalid_plan():
    return [
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        {
            "tool": "grep",
            "args": {"path": ".", "pattern": "${1.text}", "recursive": True, "max_results": 20},
        },
    ]


def test_validation_repair_rejects_wrong_tool_and_cannot_complete(tmp_path):
    orchestrator, prompts, executor = _repair_orchestrator(
        tmp_path, {"tool": "directory_lister", "args": {"path": "."}}
    )
    result = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        _h2_invalid_plan(),
        "Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
    )

    assert result is None
    assert len(prompts) == 1
    assert executor.calls == 0
    assert orchestrator.failed is True
    assert orchestrator.agent_state.last_result["status"] == "blocked"
    assert "grep" in prompts[0]
    assert "directory_lister" not in prompts[0]


def test_validation_repair_preserves_tool_and_frozen_arguments(tmp_path):
    orchestrator, prompts, executor = _repair_orchestrator(
        tmp_path,
        {
            "action": "tool",
            "tool": "grep",
            "args": {"path": ".", "recursive": True, "max_results": 20},
            "bindings": {"pattern": {"from_step": 1, "path": []}},
        },
    )
    validated = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        _h2_invalid_plan(),
        "Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
    )

    assert len(prompts) == 1
    assert executor.calls == 0
    assert validated is not None
    assert validated[1]["tool"] == "grep"
    assert validated[1]["args"] == {"path": ".", "recursive": True, "max_results": 20}
    assert validated[1]["bindings"]["pattern"]["from_step"] == 1


def test_validation_repair_rejects_mutation_of_valid_argument(tmp_path):
    orchestrator, prompts, executor = _repair_orchestrator(
        tmp_path,
        {
            "tool": "grep",
            "args": {"path": "subdir", "recursive": True, "max_results": 20},
            "bindings": {"pattern": {"from_step": 1, "path": []}},
        },
    )
    result = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        _h2_invalid_plan(),
        "Leia fonte_h2.txt e procure nos outros arquivos pela palavra que ele contém.",
    )

    assert result is None
    assert len(prompts) == 1
    assert executor.calls == 0
    assert orchestrator.failed is True


def test_validation_repair_budget_exhaustion_does_not_request_third_plan(tmp_path):
    orchestrator, prompts, executor = _repair_orchestrator(
        tmp_path,
        {
            "tool": "grep",
            "args": {"path": ".", "recursive": True, "max_results": 20},
            "bindings": {"pattern": {"from_step": 99, "path": []}},
        },
    )
    result = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        _h2_invalid_plan(),
        "Leia fonte_h2.txt e procure nos outros arquivos do workspace pela palavra que ele contém.",
    )

    assert result is None
    assert len(prompts) == 1
    assert executor.calls == 0
    assert orchestrator.failed is True


def test_validation_repair_prompt_exposes_binding_and_rejects_placeholders():
    decision = {"action": "tool", "tool": "grep", "args": {"path": "."}}

    class _Context:
        def ask_model(self, prompt, *_args, **_kwargs):
            self.prompt = prompt
            return decision

    context = _Context()
    action = ask_llm_for_alternative(
        {
            "tool": "grep",
            "args": {
                "path": ".",
                "pattern": "${1.text}",
                "recursive": True,
                "max_results": 20,
            },
        },
        "deterministic validation rejected argument field(s): pattern",
        SimpleNamespace(
            context_manager=context,
            execution_gateway=SimpleNamespace(_bind_deferred_references=lambda plan: plan),
        ),
        validation_repair=True,
        repairable_fields=("pattern",),
        prior_steps=(
            {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}},
        ),
    )

    assert action is not None
    assert "CONSTRAINED VALIDATION REPAIR" in context.prompt
    assert "canonical bindings" in context.prompt
    assert "${...}" in context.prompt
    assert "$ref" in context.prompt
    assert "another tool" in context.prompt
    assert 'path: []' in context.prompt
    assert 'Keep unchanged:' in context.prompt
    assert 'path="."' in context.prompt
    assert 'max_results=20' in context.prompt
    assert 'recursive=true' in context.prompt
    assert "Available prior steps" in context.prompt
    assert "file_reader" in context.prompt


def test_executed_file_failure_keeps_semantic_replan_available(tmp_path):
    orchestrator, _prompts, _executor = _repair_orchestrator(tmp_path, {})
    action = replan(
        ReplanContext(
            task="localize missing file",
            current_step={"tool": "file_reader", "args": {"file_path": "missing.txt"}},
            tool_history=[],
        ),
        "FileNotFoundError: missing.txt",
        orchestrator,
    )

    assert action is not None
    assert [step["tool"] for step in action.steps] == ["directory_lister"]
