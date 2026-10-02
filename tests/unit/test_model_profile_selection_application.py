from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llm_agent.application import model_profile_selection as selection


def test_selection_validates_effective_snapshot_then_persists_only_default(monkeypatch: pytest.MonkeyPatch) -> None:
    effective = {"default_model_profile": "from-env", "model": "effective-model"}
    calls: list[tuple[Any, ...]] = []

    def resolve(config: Any, *, profile_name: str) -> None:
        calls.append(("resolve", config, profile_name))

    class Repository:
        def __init__(self, app_paths: object, *, config_path: str | Path | None) -> None:
            calls.append(("repository", app_paths, config_path))

        def update(self, payload: dict[str, str]) -> None:
            calls.append(("update", payload))

    monkeypatch.setattr(selection, "_resolve_model_profile", resolve)
    monkeypatch.setattr(selection, "_ConfigRepository", Repository)
    app_paths = object()

    result = selection.select_default_model_profile(effective, "chosen", app_paths, "custom.json")

    assert result is None
    assert calls == [
        ("resolve", effective, "chosen"),
        ("repository", app_paths, "custom.json"),
        ("update", {"default_model_profile": "chosen"}),
    ]


def test_selection_does_not_persist_when_canonical_resolution_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[object] = []

    def resolve(*_args: object, **_kwargs: object) -> None:
        raise ValueError("invalid selected profile")

    class Repository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def update(self, payload: object) -> None:
            updates.append(payload)

    monkeypatch.setattr(selection, "_resolve_model_profile", resolve)
    monkeypatch.setattr(selection, "_ConfigRepository", Repository)

    with pytest.raises(ValueError, match="invalid selected profile"):
        selection.select_default_model_profile({}, "missing", object())
    assert updates == []


def test_selection_propagates_repository_failure_without_reloading(monkeypatch: pytest.MonkeyPatch) -> None:
    effective = {"effective": "invocation-snapshot"}
    received: list[object] = []

    def resolve(config: object, *, profile_name: str) -> None:
        received.append((config, profile_name))

    class Repository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load(self, **_kwargs: object) -> None:
            raise AssertionError("selection must not reload configuration")

        def update(self, _payload: object) -> None:
            raise OSError("write failed")

    monkeypatch.setattr(selection, "_resolve_model_profile", resolve)
    monkeypatch.setattr(selection, "_ConfigRepository", Repository)

    with pytest.raises(OSError, match="write failed"):
        selection.select_default_model_profile(effective, "chosen", object())
    assert received == [(effective, "chosen")]
