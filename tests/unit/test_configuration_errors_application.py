from __future__ import annotations

from pathlib import Path

import pytest

from llm_agent.agent.runtime.config_errors import ConfigError, ConfigNotFound, ConfigVersionError
from llm_agent.application import configuration_admin, first_run_configuration
from llm_agent.application.configuration_errors import (
    ConfigurationError,
    ConfigurationNotFound,
    translate_configuration_errors,
)
from llm_agent.workspace.paths import AppPaths


def test_application_errors_are_owned_types() -> None:
    assert ConfigurationError is not ConfigError
    assert ConfigurationNotFound is not ConfigNotFound
    assert ConfigurationError.__module__ == "llm_agent.application.configuration_errors"
    assert ConfigurationNotFound.__module__ == "llm_agent.application.configuration_errors"
    assert issubclass(ConfigurationNotFound, ConfigurationError)
    assert issubclass(ConfigurationNotFound, FileNotFoundError)
    assert not issubclass(ConfigurationError, ConfigError)
    assert not issubclass(ConfigurationNotFound, ConfigNotFound)


@pytest.mark.parametrize(
    ("source_type", "target_type", "message"),
    [
        (ConfigNotFound, ConfigurationNotFound, "Configuração não encontrada: X"),
        (ConfigError, ConfigurationError, "Profile desconhecido: foo"),
        (ConfigVersionError, ConfigurationError, "schema futura"),
    ],
)
def test_translation_preserves_args_message_and_cause(
    source_type: type[Exception], target_type: type[Exception], message: str
) -> None:
    original = source_type(message)
    with pytest.raises(target_type) as caught:
        with translate_configuration_errors():
            raise original
    assert type(caught.value) is target_type
    assert caught.value.args == original.args
    assert str(caught.value) == str(original)
    assert caught.value.__cause__ is original


@pytest.mark.parametrize("source_type", [FileNotFoundError, RuntimeError])
def test_translation_leaves_unrelated_exception_untouched(source_type: type[Exception]) -> None:
    original = source_type("unrelated")
    with pytest.raises(source_type) as caught:
        with translate_configuration_errors():
            raise original
    assert caught.value is original


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("configuration_path", ()),
        ("validate_configuration", ()),
        ("initialize_configuration", ()),
        ("migrate_configuration", ("legacy.json",)),
    ],
)
def test_configuration_admin_translates_repository_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str, args: tuple[str, ...]
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ConfigNotFound("missing repository")

    monkeypatch.setattr(configuration_admin, "ConfigRepository", fail)
    with pytest.raises(ConfigurationNotFound, match="missing repository"):
        getattr(configuration_admin, operation)(paths, *args)


@pytest.mark.parametrize("operation", ["read_first_run_configuration", "update_first_run_configuration"])
def test_first_run_operations_translate_repository_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ConfigVersionError("schema futura")

    monkeypatch.setattr(first_run_configuration, "ConfigRepository", fail)
    args: tuple[object, ...] = () if operation.startswith("read") else ("selected", "model", "endpoint")
    with pytest.raises(ConfigurationError, match="schema futura") as caught:
        getattr(first_run_configuration, operation)(paths, *args)
    assert type(caught.value) is ConfigurationError
