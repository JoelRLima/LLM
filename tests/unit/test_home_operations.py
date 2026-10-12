from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest

from llm_agent.application import home_operations
from llm_agent.application.context import AppPaths


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.discover(app_home=tmp_path / "home")


@pytest.mark.parametrize("operation, startup", [(home_operations.transient_home_operation, False), (home_operations.startup_home_operation, True)])
def test_home_operation_orders_and_yields_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: object, startup: bool) -> None:
    events: list[str] = []

    class Lease:
        def activate(self) -> None:
            events.append("activate")

        def close(self) -> None:
            events.append("close")

    class LeaseFactory:
        @staticmethod
        def begin_transient(home: Path) -> Lease:
            events.append("acquire transient")
            return Lease()

        @staticmethod
        def begin_startup(home: Path) -> Lease:
            events.append("acquire startup")
            return Lease()

    class Bootstrap:
        def prepare(self, paths: AppPaths) -> object:
            events.append("prepare")
            return object()

    monkeypatch.setattr(home_operations._home_lifecycle, "HomeLifecycleLease", LeaseFactory)
    monkeypatch.setattr(home_operations._storage_bootstrap, "StorageBootstrap", Bootstrap)
    with operation(_paths(tmp_path)) as result:  # type: ignore[operator]
        assert result is None
        events.append("yield")
    assert events == (["acquire startup", "prepare", "activate", "yield", "close"] if startup else ["acquire transient", "prepare", "yield", "close"])


@pytest.mark.parametrize("failure", ["acquire", "prepare", "activate", "operation"])
@pytest.mark.parametrize("startup", [False, True])
def test_home_operation_cleanup_and_exception_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str, startup: bool) -> None:
    if failure == "activate" and not startup:
        pytest.skip("transient leases are not activated")
    error = RuntimeError(failure)
    events: list[str] = []

    class Lease:
        def activate(self) -> None:
            events.append("activate")
            if failure == "activate":
                raise error

        def close(self) -> None:
            events.append("close")

    class LeaseFactory:
        @staticmethod
        def begin_transient(home: Path) -> Lease:
            events.append("acquire")
            if failure == "acquire":
                raise error
            return Lease()

        begin_startup = begin_transient

    class Bootstrap:
        def prepare(self, paths: AppPaths) -> None:
            events.append("prepare")
            if failure == "prepare":
                raise error

    monkeypatch.setattr(home_operations._home_lifecycle, "HomeLifecycleLease", LeaseFactory)
    monkeypatch.setattr(home_operations._storage_bootstrap, "StorageBootstrap", Bootstrap)
    operation = home_operations.startup_home_operation if startup else home_operations.transient_home_operation
    with pytest.raises(RuntimeError) as caught:
        with operation(_paths(tmp_path)):
            if failure == "operation":
                raise error
    assert caught.value is error
    assert ("close" in events) == (failure != "acquire")


def test_public_annotations_and_exports_hide_canonical_resources() -> None:
    public = set(home_operations.__all__)
    assert public == {"startup_home_operation", "transient_home_operation"}
    annotations = " ".join(str(get_type_hints(getattr(home_operations, name))) for name in public)
    assert "HomeLifecycleLease" not in annotations
    assert "StorageBootstrap" not in annotations
