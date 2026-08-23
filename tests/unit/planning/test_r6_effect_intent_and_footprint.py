import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove
from agent.code.application import CodingApplicationService
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.memory.memory import MemoryDatabaseError
from agent.planning.effect_intent import effect_intent_error
from agent.planning.plan_validator import PlanValidator
from agent.planning.task_completion import (
    allow_linear_completion,
    initialize_task_progression,
    refresh_executed_effects,
)
from agent.planning.task_graph import NodeState
from agent.planning.task_scheduler import GraphExecutionResult
from agent.planning.task_semantics_inference import infer_effect_semantics
from agent.reporting.observation_evidence import project_artifact_evidence
from agent.runtime.context import Artifact, TaskResult, TaskStatus
from agent.skills import load_skill_registry
from agent.skills.code_task import CodeTaskSkill
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


class _ModelGateway:
    provider_name = "r6-fake"
    capabilities = ProviderCapabilities()

    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, _request):
        return ModelResponse(content=self.content)

    def stream(self, _request):
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def _write_artifact(
    *paths: str,
    mutation: bool = True,
    persisted: bool = True,
    rollback: bool = False,
    status: str = "applied",
    invocation_id: str | None = None,
) -> Artifact:
    metadata = {
        "affected_files": paths,
        "mutation_occurred": mutation,
        "persisted_mutation": persisted,
        "applied": mutation,
        "rollback_occurred": rollback,
        "final_state": status,
    }
    if invocation_id is not None:
        metadata["invocation_id"] = invocation_id
    return Artifact("changeset", metadata=metadata)


@pytest.mark.parametrize(
    ("objective", "requested", "prohibited"),
    [
        ("Lembre na memória que a cor é azul.", ("memory_write",), ()),
        ("Não salve na memória.", (), ("memory_write",)),
        ("Remova o arquivo notes.txt.", ("write",), ()),
        ("Remova da memória a preferência antiga.", ("memory_write",), ()),
    ],
)
def test_memory_intent_is_bounded_and_file_removal_stays_write(
    objective: str,
    requested: tuple[str, ...],
    prohibited: tuple[str, ...],
) -> None:
    semantics = infer_effect_semantics(objective)

    assert semantics.requested == requested
    assert semantics.prohibited == prohibited


def _plan_validator(tmp_path: Path, objective: str) -> PlanValidator:
    skills = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    return PlanValidator(
        {name: skills.skill(name) for name in skills.names()},
        list(skills.names()),
        frozenset({"read", "write", "validate", "analyze", "memory"}),
        registry,
        objective=objective,
    )


def test_model_plan_cannot_turn_write_capability_into_intent(tmp_path: Path) -> None:
    plan = [{
        "tool": "code_task",
        "args": {"action": "modify", "objective": "ajuste", "targets": ["a.py"]},
    }]

    read_only = _plan_validator(tmp_path, "Analise o projeto.").validate(plan)
    explicit_write = _plan_validator(tmp_path, "Altere a.py.").validate(plan)
    prohibited = _plan_validator(tmp_path, "Nao altere nenhum arquivo.").validate(plan)

    assert not read_only.is_valid
    assert "UNREQUESTED_EFFECT" in read_only.blocked_steps[0].reason
    assert explicit_write.is_valid
    assert not prohibited.is_valid
    assert "PROHIBITED_EFFECT" in prohibited.blocked_steps[0].reason


def test_conditional_write_and_read_only_shell_are_not_false_prohibitions() -> None:
    conditional = (
        "Se controle.txt for original, altere para modificado; "
        "caso contrario nao altere."
    )
    assert effect_intent_error(
        conditional,
        "code_task",
        {"action": "modify", "targets": ["controle.txt"]},
        SimpleNamespace(capabilities=frozenset({"write"})),
    ) is None
    assert effect_intent_error(
        "Inspecione o historico local.",
        "shell",
        {"command": "git log -1"},
        SimpleNamespace(capabilities=frozenset({"write"})),
    ) is None


def test_auto_approval_does_not_expand_task_intent(tmp_path: Path) -> None:
    skill = CodeTaskSkill(base_dir=str(tmp_path), approval_policy=AutoApprove())
    validator = PlanValidator(
        {"code_task": skill},
        ["code_task"],
        frozenset({"read", "write", "validate", "analyze"}),
        objective="Analise o projeto.",
    )

    report = validator.validate([
        {
            "tool": "code_task",
            "args": {"action": "modify", "objective": "ajuste", "targets": ["a.py"]},
        }
    ])

    assert not report.is_valid
    assert "UNREQUESTED_EFFECT" in report.blocked_steps[0].reason
    assert effect_intent_error(
        "Analise o projeto.",
        "code_task",
        {"action": "modify"},
        SimpleNamespace(capabilities=frozenset({"write"})),
    ).startswith("UNREQUESTED_EFFECT")


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "affected_files": ("src\\module.py",),
                "mutation_occurred": True,
                "persisted_mutation": True,
                "applied": True,
                "final_state": "applied",
            },
            {"affected_files": ("src/module.py",), "persisted_mutation": True},
        ),
        (
            {
                "affected_files": ("same.txt",),
                "mutation_occurred": False,
                "persisted_mutation": False,
                "applied": True,
                "final_state": "applied",
            },
            {"affected_files": (), "persisted_mutation": False},
        ),
        (
            {
                "affected_files": ("rolled.txt",),
                "mutation_occurred": True,
                "persisted_mutation": True,
                "applied": True,
                "rollback_occurred": True,
                "final_state": "restored",
            },
            {"affected_files": (), "persisted_mutation": False},
        ),
    ],
)
def test_code_task_projection_reports_surviving_footprint_only(metadata, expected) -> None:
    result = TaskResult(TaskStatus.FAILED, artifacts=(Artifact("changeset", metadata=metadata),))

    projection = CodeTaskSkill._effect_projection(result)

    assert projection["attempted_files"] == tuple(
        sorted(project_artifact_evidence({"data": {"artifacts": [{"metadata": metadata}]}}).affected_files)
    )
    assert projection["affected_files"] == expected["affected_files"]
    assert projection["persisted_mutation"] is expected["persisted_mutation"]


def test_code_task_result_preserves_actual_mutation_footprint(tmp_path: Path) -> None:
    gateway = _ModelGateway(json.dumps({
        "changes": [{
            "path": "nested/out.py",
            "kind": "create",
            "content": "value = 1\n",
        }]
    }))
    skill = CodeTaskSkill(
        base_dir=str(tmp_path),
        model_gateway=gateway,
        approval_policy=AutoApprove(),
    )

    result = skill.execute({"action": "generate", "objective": "Crie out.py"})

    assert result["status"] == "succeeded"
    assert result["affected_files"] == ("nested/out.py",)
    assert result["attempted_files"] == ("nested/out.py",)
    assert result["mutation_occurred"] is True
    assert result["persisted_mutation"] is True
    assert (tmp_path / "nested" / "out.py").is_file()


def test_collateral_mutation_is_visible_in_canonical_footprint() -> None:
    result = {"data": {"artifacts": [{
        "metadata": {
            "affected_files": ["target.py", "../collateral.txt"],
            "applied": True,
            "mutation_occurred": True,
            "persisted_mutation": True,
            "final_state": "applied",
        }
    }]}}

    evidence = project_artifact_evidence(result)

    assert evidence.surviving_files == ("target.py", "../collateral.txt")
    assert evidence.persisted_mutation is True


def test_multitask_projection_preserves_partial_effects_and_all_nodes() -> None:
    graph_result = GraphExecutionResult(
        states={
            "rolled": NodeState.FAILED,
            "survivor": NodeState.SUCCEEDED,
            "blocked": NodeState.BLOCKED,
        },
        results={
            "rolled": TaskResult(
                TaskStatus.FAILED,
                error="validation:failed",
                artifacts=(_write_artifact("old\\rolled.py", rollback=True, persisted=False, status="restored"),),
            ),
            "survivor": TaskResult(
                TaskStatus.SUCCEEDED,
                artifacts=(_write_artifact("new/survivor.py", invocation_id="validation-1"),),
            ),
        },
        execution_order=("rolled", "survivor"),
        errors={"rolled": "validation:failed", "blocked": "Dependencia falhou"},
    )

    result = CodingApplicationService._graph_result(graph_result)

    assert result.status is TaskStatus.FAILED
    assert result.metadata["affected_files"] == ("new/survivor.py",)
    assert result.metadata["attempted_files"] == ("new/survivor.py", "old/rolled.py")
    assert result.metadata["mutation_occurred"] is True
    assert result.metadata["persisted_mutation"] is True
    assert result.metadata["rollback_occurred"] is True
    assert result.metadata["nodes"]["blocked"]["status"] == "blocked"
    assert result.metadata["nodes"]["survivor"]["invocation_ids"] == ["validation-1"]


class _MemoryOwner:
    def __init__(self, state, *, fail: bool = False) -> None:
        self.agent_state = state
        self.fail = fail

    def remember(self, key: str, value: str, *, section: str) -> None:
        if self.fail:
            raise MemoryDatabaseError("database unavailable")
        self.agent_state.memory.state.setdefault(section, {})[key] = value

    def forget(self, key: str) -> None:
        if self.fail:
            raise MemoryDatabaseError("database unavailable")
        self.agent_state.memory.state.setdefault("key_findings", {}).pop(key, None)


def _memory_runtime(tmp_path: Path, objective: str, *, fail: bool = False):
    from agent.state import AgentState

    state = AgentState()
    owner = _MemoryOwner(state, fail=fail)
    skills = load_skill_registry(base_dir=tmp_path)
    skills.skill("session_memory").orchestrator = owner
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    registry.freeze()
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=registry,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )
    initialize_task_progression(orchestrator, objective)
    gateway = ToolInvocationGateway(
        registry,
        approval_port=AutoApprove(),
        state_recorder=lambda name, args, result: state.record_tool_result(
            name,
            args,
            result.to_legacy_dict(include_details=True),
        ),
    )
    orchestrator.tool_invocation_gateway = gateway
    return orchestrator, gateway


def test_memory_set_is_a_canonical_requested_effect(tmp_path: Path) -> None:
    orchestrator, gateway = _memory_runtime(tmp_path, "Lembre na memoria que alpha vale beta.")

    result = gateway.run(
        "session_memory",
        {"action": "set", "key": "alpha", "value": "beta"},
        active_skills=["session_memory"],
        allowed_capabilities=frozenset({"memory"}),
    )
    refresh_executed_effects(orchestrator)

    assert result.status.value == "succeeded"
    assert result.executed is True
    assert result.to_legacy_dict(include_details=True)["artifacts"][0]["metadata"]["effect"] == "memory_write"
    assert orchestrator.agent_state.executed_effects == ["memory_write"]
    assert orchestrator.agent_state.pending_effects() == ()
    assert allow_linear_completion(orchestrator, "Lembre na memoria que alpha vale beta.") is None


def test_memory_get_and_failed_set_do_not_prove_persistence(tmp_path: Path) -> None:
    read_orchestrator, read_gateway = _memory_runtime(tmp_path, "Consulte a memoria.")
    read_result = read_gateway.run(
        "session_memory",
        {"action": "keys"},
        active_skills=["session_memory"],
        allowed_capabilities=frozenset({"memory"}),
    )
    refresh_executed_effects(read_orchestrator)

    failed_orchestrator, failed_gateway = _memory_runtime(
        tmp_path, "Lembre na memoria que a escrita deve falhar.", fail=True
    )
    failed_result = failed_gateway.run(
        "session_memory",
        {"action": "set", "key": "alpha", "value": "beta"},
        active_skills=["session_memory"],
        allowed_capabilities=frozenset({"memory"}),
    )
    refresh_executed_effects(failed_orchestrator)

    assert read_result.to_legacy_dict(include_details=True)["artifacts"] == []
    assert read_orchestrator.agent_state.executed_effects == []
    failed_artifact = failed_result.to_legacy_dict(include_details=True)["artifacts"][0]["metadata"]
    assert failed_artifact["persisted_mutation"] is False
    assert failed_orchestrator.agent_state.executed_effects == []


def test_ordinary_task_cannot_model_plan_memory_set(tmp_path: Path) -> None:
    report = _plan_validator(tmp_path, "Analise o projeto.").validate([
        {
            "tool": "session_memory",
            "args": {"action": "set", "key": "alpha", "value": "beta"},
        }
    ])

    assert not report.is_valid
    assert "UNREQUESTED_EFFECT" in report.blocked_steps[0].reason


def test_unrequested_durable_write_blocks_completion_without_rollback() -> None:
    from agent.state import AgentState

    class WriteRegistry:
        @staticmethod
        def descriptor(_name: str):
            return SimpleNamespace(capabilities=frozenset({"write"}))

    state = AgentState()
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=WriteRegistry(),
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )
    initialize_task_progression(orchestrator, "Analise o projeto.")
    state.tool_history = [{
        "tool": "code_task",
        "args": {"action": "modify"},
        "result": {
            "ok": True,
            "status": "succeeded",
            "executed": True,
            "data": {"artifacts": [{
                "metadata": {
                    "affected_files": ["module.py"],
                    "applied": True,
                    "mutation_occurred": True,
                    "persisted_mutation": True,
                    "final_state": "applied",
                }
            }]},
        },
    }]

    answer = allow_linear_completion(orchestrator, "Analise o projeto.")

    assert "efeito nao solicitado" in answer
    assert state.terminal_disposition == "block"
    assert state.executed_effects == ["write"]
    assert state.unrequested_effects() == ("write",)


def test_memory_effect_survives_checkpoint_reentry(tmp_path: Path) -> None:
    orchestrator, gateway = _memory_runtime(tmp_path, "Lembre na memoria que alpha vale beta.")
    gateway.run(
        "session_memory",
        {"action": "set", "key": "alpha", "value": "beta"},
        active_skills=["session_memory"],
        allowed_capabilities=frozenset({"memory"}),
    )
    refresh_executed_effects(orchestrator)
    checkpoint = orchestrator.agent_state.to_checkpoint_dict()

    from agent.state import AgentState

    restored = AgentState()
    restored_orchestrator = SimpleNamespace(
        agent_state=restored,
        tool_registry=orchestrator.tool_registry,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )
    restored.from_checkpoint_dict(checkpoint, effect_authority=restored_orchestrator)

    assert restored.requested_effects == ["memory_write"]
    assert restored.executed_effects == ["memory_write"]
    assert restored.pending_effects() == ()
