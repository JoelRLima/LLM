from __future__ import annotations

from types import SimpleNamespace

from agent.orchestration.task_runner import TaskInputs, TaskRunner
from agent.state import AgentState
from tests.support.task_definition import make_contract, make_spec


def test_runner_admits_and_resolves_authority_before_execution() -> None:
    contract = make_contract("runner-task", "runner objective")
    repository_ref = SimpleNamespace(
        task_id=contract.task_id,
        definition_state="complete",
        is_complete=True,
    )
    calls: list[str] = []

    class Compiler:
        last_ref = None

        def compile(self, task_id: str, objective: str):
            calls.append(f"compile:{task_id}:{objective}")
            self.last_ref = repository_ref
            return repository_ref

    class Resolver:
        def resolve(self, reference):
            calls.append(f"resolve:{reference.task_id}")
            return object()

    state = AgentState()
    state.root_task_id = contract.task_id
    state.objective = contract.objective
    orchestrator = SimpleNamespace(
        agent_state=state,
        task_definition_compiler=Compiler(),
        task_context_resolver=Resolver(),
        _save_checkpoint=lambda: True,
        _preserve_checkpoint=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = TaskRunner(orchestrator)._ensure_task_definition(
        TaskInputs(contract.objective, False, 0)
    )

    assert answer is None
    assert calls == [
        "compile:runner-task:runner objective",
        "resolve:runner-task",
    ]
    assert state.task_definition_ref is repository_ref


def test_runner_resume_without_binding_blocks_and_preserves_checkpoint() -> None:
    saves: list[bool] = []
    state = AgentState()
    state.root_task_id = "resume-task"
    state.objective = "resume objective"

    class Compiler:
        last_ref = None

        def compile(self, *_args):
            raise AssertionError("resume without binding must not compile fresh")

    orchestrator = SimpleNamespace(
        agent_state=state,
        task_definition_compiler=Compiler(),
        task_context_resolver=None,
        _save_checkpoint=lambda: saves.append(True) or True,
        _preserve_checkpoint=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = TaskRunner(orchestrator)._ensure_task_definition(
        TaskInputs(state.objective, True, 0)
    )

    assert answer is not None
    assert state.terminal_disposition == "block"
    assert state.last_result["error_code"] == "TASK_DEFINITION_BINDING_MISSING"
    assert orchestrator._preserve_checkpoint is True
    assert saves


def test_successful_task_cleanup_does_not_touch_durable_definition(repository, tmp_path) -> None:
    contract = make_contract("cleanup-task", "cleanup objective")
    repository.save_contract(contract)
    repository.save_spec(make_spec(contract))
    contract_bytes = repository.contract_path(contract.task_id).read_bytes()
    spec_bytes = repository.spec_path(contract.task_id).read_bytes()

    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("checkpoint", encoding="utf-8")
    checkpoint.unlink()

    assert repository.contract_path(contract.task_id).read_bytes() == contract_bytes
    assert repository.spec_path(contract.task_id).read_bytes() == spec_bytes
