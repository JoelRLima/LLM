"""Pure regressions for post-parent POSIX cleanup classification."""
from types import SimpleNamespace

import pytest

from llm_agent.execution import cleanup


@pytest.mark.parametrize("state", ["live", "absent", "unavailable"])
def test_exited_parent_group_is_not_silently_successful(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    queries = []
    def killpg(group: int, signal: int) -> None:
        queries.append((group, signal))
        if state == "absent":
            raise ProcessLookupError()
        if state == "unavailable":
            raise PermissionError("cannot query")
    monkeypatch.setattr(cleanup, "os", SimpleNamespace(name="posix", killpg=killpg))
    context = SimpleNamespace(process=SimpleNamespace(poll=lambda: 0), process_group=43210)
    errors = []
    cleanup._record_orphaned_group(context, errors)
    assert queries == [(43210, 0)]
    assert bool(errors) is (state != "absent")


def test_query_failure_does_not_skip_any_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object) -> None:
        raise PermissionError("cannot query")
    monkeypatch.setattr(cleanup, "os", SimpleNamespace(name="posix", killpg=denied))
    events = []
    for name in ["_terminate_tree", "_settle_readers", "_close_process_pipes"]:
        monkeypatch.setattr(cleanup, name, lambda *args, name=name: events.append(name))
    context = SimpleNamespace(process=SimpleNamespace(poll=lambda: 0), process_group=43210, windows_job=None, fail_on_orphaned_group=True)
    error = cleanup.cleanup_command(context, terminate_tree=True)
    assert error is not None and "could not confirm" in error
    assert events == ["_terminate_tree", "_settle_readers", "_close_process_pipes"]


def test_native_windows_branch_does_not_query_posix_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup, "os", SimpleNamespace(name="nt"))
    errors = []
    cleanup._record_orphaned_group(SimpleNamespace(), errors)
    assert errors == []
