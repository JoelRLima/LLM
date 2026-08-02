from types import SimpleNamespace

import pytest

from agent.memory.json_persistence import AtomicJsonWriteError
from agent.orchestration.task_runner import TaskRunner


class _CancellationToken:
    def reset(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class _FailingPersistenceOrchestrator:
    def __init__(self, memory_path) -> None:
        self.session = SimpleNamespace(messages=[], config={})
        self.agent_state = SimpleNamespace(
            max_history_turns=5,
            conversation_history=[],
        )
        self.cancellation_token = _CancellationToken()
        self.workspace = SimpleNamespace(rollback=lambda: None)
        self.context_manager = SimpleNamespace(maybe_compress_context=lambda: None)
        self._task_failed = False
        self._cancelled = False
        self.persistence_calls = 0
        self.checkpoint_deleted = False
        self.memory_path = memory_path

    def _reset_task_state(self, objective: str) -> None:
        self.objective = objective
        self._task_failed = False

    def _count_metrics_lines(self) -> int:
        return 0

    def _answer_trivial(self, objective: str) -> str:
        return f"resposta para {objective}"

    def _persist_memory_to_file(self) -> None:
        self.persistence_calls += 1
        raise AtomicJsonWriteError(
            self.memory_path,
            OSError("disco indisponível"),
        )

    def _delete_checkpoint(self) -> None:
        self.checkpoint_deleted = True


def test_task_success_is_not_returned_when_automatic_memory_save_fails(
    tmp_path,
) -> None:
    orchestrator = _FailingPersistenceOrchestrator(
        tmp_path / "agent_memory.json"
    )

    with pytest.raises(AtomicJsonWriteError, match="disco indisponível"):
        TaskRunner(orchestrator).run("oi", None)

    assert orchestrator.persistence_calls == 1
    assert orchestrator._task_failed is True
    assert orchestrator.checkpoint_deleted is False
