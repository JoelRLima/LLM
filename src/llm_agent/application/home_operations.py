"""Finite lifecycle operations for Interface home preparation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from llm_agent.agent.runtime import home_lifecycle as _home_lifecycle
from llm_agent.agent.runtime import storage_bootstrap as _storage_bootstrap
from llm_agent.workspace.paths import AppPaths


@contextmanager
def transient_home_operation(paths: AppPaths) -> Iterator[None]:
    """Prepare storage under a transient lease and release it on every exit."""
    lease = _home_lifecycle.HomeLifecycleLease.begin_transient(paths.home_dir)
    try:
        _storage_bootstrap.StorageBootstrap().prepare(paths)
        yield None
    finally:
        lease.close()


@contextmanager
def startup_home_operation(paths: AppPaths) -> Iterator[None]:
    """Prepare storage, activate a startup lease, then release it on exit."""
    lease = _home_lifecycle.HomeLifecycleLease.begin_startup(paths.home_dir)
    try:
        _storage_bootstrap.StorageBootstrap().prepare(paths)
        lease.activate()
        yield None
    finally:
        lease.close()


__all__ = ["startup_home_operation", "transient_home_operation"]
