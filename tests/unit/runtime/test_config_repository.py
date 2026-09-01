import json
from pathlib import Path

import pytest

from agent.runtime.config_repository import (
    ConfigError,
    ConfigNotFound,
    ConfigRepository,
    ConfigVersionError,
)
from agent.runtime.paths import AppPaths


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.discover(app_home=tmp_path / "app-home", env={})


@pytest.fixture
def repository(app_paths: AppPaths) -> ConfigRepository:
    return ConfigRepository(app_paths)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_missing_configuration_is_explicit_or_can_resolve_defaults(
    repository: ConfigRepository,
) -> None:
    with pytest.raises(ConfigNotFound):
        repository.load(environment={})

    resolved = repository.load(environment={}, allow_missing=True)

    assert resolved.schema_version == 1
    values = resolved.to_dict()
    assert values["hardware_profile"] == "low_vram_8gb"
    assert values["semantic_memory_enabled"] is False
    assert values["semantic_memory_model"] == "all-MiniLM-L6-v2"
    assert values["max_reasoning_turns"] == 3


def test_initialize_uses_packaged_resource_outside_checkout_and_is_idempotent(
    repository: ConfigRepository,
    app_paths: AppPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elsewhere = tmp_path / "unrelated-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    created = repository.initialize()
    document = json.loads(created.read_text(encoding="utf-8"))
    document["model"] = "preserve-me"
    _write(created, document)

    assert repository.initialize() == app_paths.config_file
    assert repository.load(environment={}).to_dict()["model"] == "preserve-me"
    assert document["schema_version"] == 1
    assert "checkpoint_file" not in document
    assert "output_dir" not in document["task_report"]


def test_explicit_config_path_overrides_only_the_repository_file(
    app_paths: AppPaths,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "profile" / "custom.json"
    repository = ConfigRepository(app_paths, config_path=explicit)

    assert repository.initialize() == explicit.resolve()
    assert not app_paths.config_file.exists()
    resolved = repository.load(environment={}).to_dict()
    assert "checkpoint_file" not in resolved


def test_precedence_is_cli_then_allowlisted_environment_file_and_defaults(
    repository: ConfigRepository,
) -> None:
    repository.initialize()
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    document["model"] = "from-file"
    document["max_tokens"] = 1024
    document["validation"]["pytest"] = False
    _write(repository.path, document)

    resolved = repository.load(
        environment={
            "LLM_AGENT_MODEL": "from-env",
            "LLM_AGENT_MAX_TOKENS": "2048",
            "UNRELATED_MODEL": "must-be-ignored",
        },
        overrides={
            "model": "from-cli",
            "max_tokens": 4096,
            "validation": {"pytest": True},
        },
    ).to_dict()

    assert resolved["model"] == "from-cli"
    assert resolved["max_tokens"] == 4096
    assert resolved["validation"]["pytest"] is True
    assert resolved["validation"]["enabled"] is True
    assert "UNRELATED_MODEL" not in resolved


def test_explicit_model_overrides_reach_the_selected_profile(
    repository: ConfigRepository,
) -> None:
    repository.initialize()

    resolved = repository.load(
        environment={
            "LLM_AGENT_API_URL": "http://env.example/v1/chat/completions",
            "LLM_AGENT_MODEL": "from-env",
            "LLM_AGENT_TEMPERATURE": "0.4",
            "LLM_AGENT_MAX_TOKENS": "1024",
            "LLM_AGENT_TIMEOUT": "45",
            "LLM_AGENT_ENABLE_GBNF": "false",
        },
        overrides={
            "model": "from-cli",
            "temperature": 0.1,
        },
    ).to_dict()
    profile = resolved["model_profiles"][resolved["default_model_profile"]]

    assert profile["api_url"] == "http://env.example/v1/chat/completions"
    assert profile["model"] == "from-cli"
    assert profile["temperature"] == 0.1
    assert profile["max_tokens"] == 1024
    assert profile["timeout"] == 45.0
    assert profile["capabilities"]["structured_output"] == "json_prompt"


def test_invalid_values_fail_strictly_before_legacy_adaptation(
    repository: ConfigRepository,
) -> None:
    repository.initialize()
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    document["temperature"] = "quente"
    _write(repository.path, document)

    with pytest.raises(ConfigError, match="temperature"):
        repository.load(environment={})


def test_future_or_missing_schema_version_is_rejected(
    repository: ConfigRepository,
) -> None:
    repository.initialize()
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    document["schema_version"] = 2
    _write(repository.path, document)
    with pytest.raises(ConfigVersionError, match="futura"):
        repository.load(environment={})

    document.pop("schema_version")
    _write(repository.path, document)
    with pytest.raises(ConfigVersionError, match="obrigatório"):
        repository.load(environment={})


def test_environment_parsing_is_allowlisted_and_strict(
    repository: ConfigRepository,
) -> None:
    repository.initialize()

    resolved = repository.load(
        environment={
            "LLM_AGENT_AUTO_CONFIRM": "true",
            "LLM_AGENT_MAX_MODEL_CALLS": "7",
            "LLM_AGENT_SEMANTIC_MEMORY_ENABLED": "true",
            "LLM_AGENT_SEMANTIC_MEMORY_MODEL": "custom-model",
        }
    ).to_dict()

    assert resolved["auto_confirm"] is True
    assert resolved["max_model_calls"] == 7
    assert resolved["semantic_memory_enabled"] is True
    assert resolved["semantic_memory_model"] == "custom-model"
    with pytest.raises(ConfigError, match="MAX_MODEL_CALLS"):
        repository.load(
            environment={"LLM_AGENT_MAX_MODEL_CALLS": "muitos"}
        )


def test_agent_max_tokens_accepts_none_or_positive_integer_only(
    repository: ConfigRepository,
) -> None:
    repository.initialize()

    assert repository.load(environment={}).to_dict()["agent_max_tokens"] is None
    assert repository.load(
        environment={},
        overrides={"agent_max_tokens": 2048},
    ).to_dict()["agent_max_tokens"] == 2048
    with pytest.raises(ConfigError, match="agent_max_tokens"):
        repository.load(
            environment={},
            overrides={"agent_max_tokens": 0},
        )


def test_resolved_config_to_dict_returns_an_independent_snapshot(
    repository: ConfigRepository,
) -> None:
    repository.initialize()
    resolved = repository.load(environment={})

    values = resolved.to_dict()
    values["validation"]["enabled"] = False

    assert values["schema_version"] == 1
    assert resolved.to_dict()["validation"]["enabled"] is True


def test_migrate_copies_source_atomically_and_is_idempotent(
    repository: ConfigRepository,
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy-config.json"
    _write(
        source,
        {
            "model": "legacy-model",
            "max_tokens": 1536,
            "checkpoint_file": "runtime/agent_checkpoint.json",
            "task_report": {
                "enabled": True,
                "format": "json",
                "output_dir": "runtime/reports",
            },
        },
    )
    original_source = source.read_bytes()

    destination = repository.migrate(source)
    first_bytes = destination.read_bytes()
    first_mtime = destination.stat().st_mtime_ns

    assert repository.migrate(source) == destination
    assert destination.read_bytes() == first_bytes
    assert destination.stat().st_mtime_ns == first_mtime
    assert source.read_bytes() == original_source
    migrated = json.loads(first_bytes)
    assert migrated["schema_version"] == 1
    assert "checkpoint_file" not in migrated
    assert "output_dir" not in migrated["task_report"]
    assert not list(destination.parent.glob("*.tmp"))


def test_migrate_fails_closed_on_conflict_or_future_source(
    repository: ConfigRepository,
    tmp_path: Path,
) -> None:
    repository.initialize()
    conflicting = tmp_path / "conflicting.json"
    _write(conflicting, {"model": "different"})

    with pytest.raises(ConfigError, match="diferente"):
        repository.migrate(conflicting)

    future_paths = AppPaths.discover(app_home=tmp_path / "future-home", env={})
    future_repository = ConfigRepository(future_paths)
    future = tmp_path / "future.json"
    _write(future, {"schema_version": 99, "model": "future"})

    with pytest.raises(ConfigVersionError, match="futura"):
        future_repository.migrate(future)
    assert future.exists()
