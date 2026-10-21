from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.application import configuration_admin, discovery_configuration, first_run_configuration
from llm_agent.workspace.paths import AppPaths


def test_configuration_path_and_validate_are_repository_projections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    explicit = tmp_path / "custom.json"
    observed: list[tuple[str, object]] = []

    class Repository:
        def __init__(self, _paths: AppPaths, *, config_path: str | Path | None = None) -> None:
            observed.append(("construct", config_path))
            self.path = explicit

        def load(self, *, overrides: object = None) -> object:
            observed.append(("load", overrides))
            return object()

    monkeypatch.setattr(configuration_admin, "ConfigRepository", Repository)
    assert configuration_admin.configuration_path(paths, explicit) == explicit
    assert configuration_admin.validate_configuration(paths, explicit, "selected") == explicit
    assert observed == [
        ("construct", explicit),
        ("construct", explicit),
        ("load", {"default_model_profile": "selected"}),
    ]


@pytest.mark.parametrize("operation_name,method", [("initialize_configuration", "initialize"), ("migrate_configuration", "migrate")])
def test_configuration_write_canonical_path_uses_lifecycle_and_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation_name: str,
    method: str,
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    observed: list[str] = []

    class Repository:
        path = paths.config_file

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def initialize(self) -> Path:
            observed.append("initialize")
            return self.path

        def migrate(self, _source: str | Path) -> Path:
            observed.append("migrate")
            return self.path

    class Lease:
        @staticmethod
        def begin_transient(home: Path) -> "Lease":
            observed.append("lease")
            assert home == paths.home_dir.resolve()
            return Lease()

        def close(self) -> None:
            observed.append("close")

    class Bootstrap:
        def prepare(self, app_paths: AppPaths) -> None:
            observed.append("bootstrap")
            assert app_paths is paths

    monkeypatch.setattr(configuration_admin, "ConfigRepository", Repository)
    monkeypatch.setattr(configuration_admin, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(configuration_admin, "StorageBootstrap", Bootstrap)
    operation = getattr(configuration_admin, operation_name)
    result = (
        operation(paths, None)
        if method == "initialize"
        else operation(paths, source=tmp_path / "legacy.json", config_path=None)
    )
    assert result == paths.config_file
    assert observed == ["lease", "bootstrap", method, "close"]


def test_configuration_write_external_path_has_no_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    external = tmp_path / "outside.json"
    observed: list[str] = []

    class Repository:
        path = external

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def initialize(self) -> Path:
            observed.append("initialize")
            return external

        def migrate(self, _source: str | Path) -> Path:
            observed.append("migrate")
            return external

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external target must not acquire canonical lifecycle")

    monkeypatch.setattr(configuration_admin, "ConfigRepository", Repository)
    monkeypatch.setattr(configuration_admin.HomeLifecycleLease, "begin_transient", forbidden)
    monkeypatch.setattr(configuration_admin.StorageBootstrap, "prepare", forbidden)
    assert configuration_admin.initialize_configuration(paths, external) == external
    assert configuration_admin.migrate_configuration(paths, tmp_path / "legacy.json", config_path=external) == external
    assert observed == ["initialize", "migrate"]


@pytest.mark.parametrize(
    "method,fail_at",
    [("initialize", "bootstrap"), ("initialize", "owner"), ("migrate", "owner")],
)
def test_configuration_write_closes_lease_after_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
    fail_at: str,
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    observed: list[str] = []

    class Repository:
        path = paths.config_file

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def initialize(self) -> Path:
            observed.append("owner")
            raise RuntimeError("initialize failed")

        def migrate(self, _source: str | Path) -> Path:
            observed.append("owner")
            raise RuntimeError("migrate failed")

    class Lease:
        @staticmethod
        def begin_transient(_home: Path) -> "Lease":
            observed.append("lease")
            return Lease()

        def close(self) -> None:
            observed.append("close")

    class Bootstrap:
        def prepare(self, _app_paths: AppPaths) -> None:
            observed.append("bootstrap")
            if fail_at == "bootstrap":
                raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(configuration_admin, "ConfigRepository", Repository)
    monkeypatch.setattr(configuration_admin, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(configuration_admin, "StorageBootstrap", Bootstrap)
    with pytest.raises(RuntimeError):
        if method == "initialize":
            configuration_admin.initialize_configuration(paths)
        else:
            configuration_admin.migrate_configuration(paths, tmp_path / "legacy.json")
    assert observed[-1] == "close"


def test_first_run_read_projects_order_defaults_and_endpoint_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    document = {
        "model": "root-model",
        "api_url": "root-endpoint",
        "default_model_profile": "second",
        "model_profiles": {
            "second": {"model": "selected-model", "api_url": "profile-endpoint"},
            "first": {"base_url": "base-endpoint"},
        },
    }

    class Repository:
        path = paths.config_file

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load(self, *, environment: object = None) -> object:
            assert environment == {}
            return SimpleNamespace(to_dict=lambda: document)

    monkeypatch.setattr(first_run_configuration, "ConfigRepository", Repository)
    view = first_run_configuration.read_first_run_configuration(paths)
    assert view.default_profile == "second"
    assert view.profiles == (
        first_run_configuration.FirstRunProfileView("second", "selected-model", "profile-endpoint"),
        first_run_configuration.FirstRunProfileView("first", "root-model", "base-endpoint"),
    )


def test_first_run_update_preserves_payload_then_empty_environment_reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    observed: list[object] = []

    class Repository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def update(self, changes: object) -> None:
            observed.append(changes)

        def load(self, *, environment: object = None) -> None:
            observed.append(environment)

    monkeypatch.setattr(first_run_configuration, "ConfigRepository", Repository)
    first_run_configuration.update_first_run_configuration(paths, "p", "m", "e", config_path=None)
    assert observed == [
        {"default_model_profile": "p", "model_profiles": {"p": {"model": "m", "base_url": "e"}}},
        {},
    ]


@pytest.mark.parametrize("load_error,expected", [(None, True), (ValueError("invalid"), False)])
def test_chat_entry_readiness_validates_without_lifecycle_or_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    load_error: Exception | None,
    expected: bool,
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("{}", encoding="utf-8")

    class Repository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load(self) -> None:
            if load_error is not None:
                raise load_error

    monkeypatch.setattr(first_run_configuration, "ConfigRepository", Repository)
    assert first_run_configuration.configuration_ready_for_chat_entry(paths) is expected


def test_chat_entry_readiness_missing_file_returns_false_before_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})

    class UnexpectedRepository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing config must be rejected before repository load")

    monkeypatch.setattr(first_run_configuration, "ConfigRepository", UnexpectedRepository)
    assert first_run_configuration.configuration_ready_for_chat_entry(paths) is False


def test_discovery_profile_is_returned_intact_and_failures_become_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    profile = object()
    observed: dict[str, object] = {}

    class Repository:
        def __init__(self, _paths: AppPaths, *, config_path: str | Path | None = None) -> None:
            observed["paths"] = _paths
            observed["config_path"] = config_path

        def load(self, *, overrides: object = None) -> object:
            observed["overrides"] = overrides
            return SimpleNamespace(model_profile=profile)

    monkeypatch.setattr(discovery_configuration, "ConfigRepository", Repository)
    result = discovery_configuration.resolve_semantic_discovery_profile(paths, "config.json", "chosen")
    assert result is profile
    assert observed == {"paths": paths, "config_path": "config.json", "overrides": {"default_model_profile": "chosen"}}

    class BrokenRepository(Repository):
        def load(self, **_kwargs: object) -> object:
            raise RuntimeError("optional Discovery prerequisite unavailable")

    monkeypatch.setattr(discovery_configuration, "ConfigRepository", BrokenRepository)
    assert discovery_configuration.resolve_semantic_discovery_profile(paths) is None
