from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent.application as application_module
import agent.application_cleanup as cleanup_module
from agent.application import AgentApplication
from agent.application_cleanup import StartupCleanupError, abort_startup


class _Resource:
    def __init__(self, events: list[str], name: str, failure: BaseException | None = None) -> None:
        self.events = events
        self.name = name
        self.failure = failure

    def release(self) -> None:
        self.events.append(self.name)
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        self.events.append(self.name)
        if self.failure is not None:
            raise self.failure


def test_abort_startup_attempts_all_owners_and_surfaces_every_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lock = _Resource(events, "lock", RuntimeError("lock cleanup"))
    lease = _Resource(events, "lease", RuntimeError("lease cleanup"))

    def fail_logging() -> None:
        events.append("logging")
        raise RuntimeError("logging cleanup")

    monkeypatch.setattr(cleanup_module, "teardown_logger", fail_logging)
    failures = abort_startup(lock, True, lease)
    assert events == ["lock", "logging", "lease"]
    assert [str(error) for error in failures] == [
        "lock cleanup",
        "logging cleanup",
        "lease cleanup",
    ]


@pytest.mark.parametrize("failure", [RuntimeError("startup"), KeyboardInterrupt()])
def test_startup_failure_after_home_acquisition_closes_lease_and_preserves_original(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    events: list[str] = []
    lease = _Resource(events, "lease")
    monkeypatch.setattr(
        application_module.HomeLifecycleLease,
        "begin_startup",
        lambda _home: lease,
    )

    def fail_prepare(_self: object, _paths: object) -> None:
        raise failure

    monkeypatch.setattr(application_module.StorageBootstrap, "prepare", fail_prepare)
    paths = SimpleNamespace(home_dir="home")
    with pytest.raises(type(failure)) as raised:
        AgentApplication.create(paths=paths, workspace="workspace", configure_logging=False)
    assert raised.value is failure
    assert events == ["lease"]


def test_startup_and_home_cleanup_failures_are_both_observable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("startup")
    cleanup = RuntimeError("lease cleanup")
    lease = _Resource([], "lease", cleanup)
    monkeypatch.setattr(
        application_module.HomeLifecycleLease,
        "begin_startup",
        lambda _home: lease,
    )

    def fail_prepare(_self: object, _paths: object) -> None:
        raise original

    monkeypatch.setattr(application_module.StorageBootstrap, "prepare", fail_prepare)
    with pytest.raises(StartupCleanupError) as raised:
        AgentApplication.create(
            paths=SimpleNamespace(home_dir="home"),
            workspace="workspace",
            configure_logging=False,
        )
    assert raised.value.original is original
    assert raised.value.cleanup_failures == (cleanup,)
    assert raised.value.__cause__ is original
