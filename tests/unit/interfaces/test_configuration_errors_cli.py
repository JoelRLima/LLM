from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.runtime.config_errors import ConfigError, ConfigNotFound, ConfigVersionError
from llm_agent.application.configuration_errors import ConfigurationError, ConfigurationNotFound
from llm_agent.application.first_run_configuration import FirstRunConfigurationView, FirstRunProfileView
from llm_agent.interfaces.cli import app as cli
from llm_agent.interfaces.cli import bootstrap, first_run, interactive_resources, interactive_session
from llm_agent.workspace.paths import AppPaths


@pytest.mark.parametrize(
    ("source_type", "target_type"),
    [
        (ConfigNotFound, ConfigurationNotFound),
        (ConfigError, ConfigurationError),
        (ConfigVersionError, ConfigurationError),
    ],
)
def test_bootstrap_translates_agent_configuration_error(
    monkeypatch: pytest.MonkeyPatch, source_type: type[Exception], target_type: type[Exception]
) -> None:
    original = source_type("startup sentinel")

    def fail(**_kwargs: object) -> object:
        raise original

    monkeypatch.setattr(AgentApplication, "create", fail)
    args = cli.build_parser().parse_args(["run", "--workspace", str(Path.cwd()), "objective"])
    with pytest.raises(target_type) as caught:
        bootstrap.create_application(args, configure_logging=False)
    assert type(caught.value) is target_type
    assert caught.value.__cause__ is original


@pytest.mark.parametrize(
    ("error", "exit_code", "hint"),
    [
        (ConfigNotFound("Configuração não encontrada: X"), 2, True),
        (ConfigError("profile inválido"), 2, False),
        (ConfigVersionError("schema futura"), 2, False),
        (FileNotFoundError("other file"), 2, False),
        (RuntimeError("other runtime"), 1, False),
    ],
)
@pytest.mark.parametrize("json_output", [False, True])
def test_app_error_mapping_preserves_exit_and_rendering(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    hint: bool,
    json_output: bool,
) -> None:
    def fail(**_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(AgentApplication, "create", fail)
    argv = ["run", "--workspace", str(Path.cwd()), "--home", "test-home", "objective"]
    if json_output:
        argv.append("--json")
    assert cli.main(argv) == exit_code
    captured = capsys.readouterr()
    if json_output:
        assert captured.err == ""
        document = json.loads(captured.out)
        assert set(document) == {"error", "status", "success"}
        assert document["status"] == "failed"
        assert document["success"] is False
        message = document["error"]
    else:
        assert captured.out == ""
        assert captured.err.startswith("Erro: ")
        message = captured.err
    assert ("llm-agent config init --home test-home" in message) is hint
    if isinstance(error, RuntimeError) and not isinstance(error, ConfigError):
        assert "RuntimeError: other runtime" in message
    else:
        assert str(error) in message


def test_explicit_missing_config_hint_preserves_both_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(**_kwargs: object) -> object:
        raise ConfigNotFound("Configuração não encontrada: explicit")

    monkeypatch.setattr(AgentApplication, "create", fail)
    assert cli.main([
        "run", "--workspace", str(Path.cwd()), "--config", "explicit.json", "--home", "test-home", "objective", "--json"
    ]) == 2
    document = json.loads(capsys.readouterr().out)
    assert "llm-agent config init --config explicit.json --home test-home" in document["error"]
    assert "reason_code" not in document


@pytest.mark.parametrize("explicit,interactive", [(True, True), (False, False)])
def test_missing_config_recovery_rethrows_outside_default_interactive(
    explicit: bool, interactive: bool
) -> None:
    error = ConfigurationNotFound("missing")
    args = Namespace(config="explicit.json" if explicit else None)
    with pytest.raises(ConfigurationNotFound) as caught:
        interactive_resources.recover_missing_config(
            error, args, value=getattr, interactive=interactive, shell_enabled=False,
            shell_holder={"shell": None}, console_prompt=None, app_paths=lambda _args: None,
        )
    assert caught.value is error


def test_guided_profile_selection_uses_application_error() -> None:
    view = FirstRunConfigurationView(
        Path("config.json"), "known", (FirstRunProfileView("known", "model", "endpoint"),)
    )
    with pytest.raises(ConfigurationError, match="Profile desconhecido: other"):
        first_run._complete_guided_setup(
            Namespace(), view, AppPaths.discover(), SimpleNamespace(print=lambda *_args: None),
            lambda *_args, **_kwargs: "other",
        )


@pytest.mark.parametrize("error", [ConfigurationError("generic"), ConfigurationNotFound("missing"), OSError("io"), ValueError("bad")])
def test_guided_setup_renders_existing_error_categories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    lines: list[str] = []
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    monkeypatch.setattr(first_run, "configuration_path", lambda *_args: paths.config_file)
    monkeypatch.setattr(first_run, "initialize_configuration", lambda *_args: paths.config_file)
    monkeypatch.setattr(first_run, "read_first_run_configuration", lambda *_args: (_ for _ in ()).throw(error))
    monkeypatch.setattr(first_run.HomeLifecycleLease, "begin_transient", lambda *_args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(first_run.StorageBootstrap, "prepare", lambda *_args: None)
    result = first_run.recover_first_run_config(
        Namespace(config=None, home=None), console=SimpleNamespace(print=lines.append),
        app_paths=paths, prompt=lambda *_args: "y",
    )
    assert result == 0
    assert any("Configuração guiada não concluída:" in line and str(error) in line for line in lines)


@pytest.mark.parametrize("guided", [False, True])
def test_interactive_recovery_restarts_only_after_guided_completion(
    monkeypatch: pytest.MonkeyPatch, guided: bool
) -> None:
    monkeypatch.setattr(first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(first_run, "prepare_chat_workspace", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(interactive_resources, "recover_missing_config", lambda *_args, **_kwargs: (guided, 0))
    monkeypatch.setattr(
        interactive_session, "_run_application_session",
        lambda *_args, **_kwargs: interactive_session.InteractiveSessionResult(
            None, None, SimpleNamespace(settled=True, exit_code=0)
        ),
    )
    calls: list[int] = []

    def create(*_args: object, **_kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise ConfigurationNotFound("missing")
        return object()

    assert interactive_session.run_chat(
        Namespace(workspace=str(Path.cwd()), config=None),
        value=getattr, app_paths=lambda _args: None, create_application=create,
        context_from_application=lambda *_args, **_kwargs: None,
    ) == 0
    assert len(calls) == (2 if guided else 1)
