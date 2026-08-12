from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.interfaces.cli import app as cli


@dataclass
class _Result:
    success: bool
    answer: str = ""
    error: str | None = None
    receipt: dict[str, Any] | None = None
    report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "succeeded" if self.success else "failed",
            "success": self.success,
            "answer": self.answer,
            "error": self.error,
            "receipt": self.receipt or {},
            "report_path": self.report_path,
        }


class _Application:
    def __init__(self, result: _Result | None = None) -> None:
        self.result = result or _Result(True, "resposta")
        self.closed = False
        self.session = SimpleNamespace(config={}, thinking_budget=0)
        self.orchestrator = SimpleNamespace()
        self.config: dict[str, Any] = {}
        self.paths = SimpleNamespace()
        self.workspace = SimpleNamespace(root=Path.cwd())
        self.workspace_paths = SimpleNamespace()

    def run(self, objective: str) -> _Result:
        self.objective = objective
        return self.result

    def close(self) -> None:
        self.closed = True


def test_version_and_help_do_not_construct_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"AgentApplication não deveria ser construída: {args!r} {kwargs!r}")

    monkeypatch.setattr(cli, "_create_application", forbidden)

    with pytest.raises(SystemExit) as version_exit:
        cli.main(["--version"])
    assert version_exit.value.code == 0
    assert "llm-agent 0.1.0" in capsys.readouterr().out

    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    assert help_exit.value.code == 0
    assert "run" in capsys.readouterr().out


def test_config_path_is_side_effect_free_and_does_not_construct_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "not-created"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"AgentApplication não deveria ser construída: {args!r} {kwargs!r}")

    monkeypatch.setattr(cli, "_create_application", forbidden)

    assert cli.main(["config", "path", "--home", str(home)]) == 0

    assert Path(capsys.readouterr().out.strip()) == (home / "config" / "config.json").resolve()
    assert not home.exists()


def test_config_init_then_validate_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "app"

    assert cli.main(["--home", str(home), "config", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "validate", "--home", str(home)]) == 0

    assert (home / "config" / "config.json").is_file()
    assert "Configuração válida" in capsys.readouterr().out


def test_chat_missing_config_interactive_yes_uses_canonical_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    prompts: list[str] = []
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda prompt: prompts.append(prompt) or "y",
    )

    assert cli.main(["chat", "--home", str(home)]) == 0

    config = home / "config" / "config.json"
    assert json.loads(config.read_text(encoding="utf-8"))["schema_version"] == 1
    output = capsys.readouterr()
    assert "Configuração criada" in output.out
    assert "llm-agent config validate" in output.out
    assert "llm-agent doctor" in output.out
    assert output.err == ""
    assert prompts == ["Deseja criar a configuração padrão agora? [Y/n] "]


def test_chat_missing_config_interactive_no_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "n")

    assert cli.main(["chat", "--home", str(home)]) == 0

    assert not (home / "config" / "config.json").exists()
    assert "llm-agent config init" in capsys.readouterr().out


def test_chat_missing_config_interactive_eof_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(cli.console, "input", eof)

    assert cli.main(["chat", "--home", str(home)]) == 0
    assert not (home / "config" / "config.json").exists()
    assert "llm-agent config init" in capsys.readouterr().out


def test_chat_missing_config_noninteractive_is_actionable_without_prompt_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: False)
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("non-interactive prompt")),
    )

    assert cli.main(["chat", "--home", str(home)]) == 2

    assert not (home / "config" / "config.json").exists()
    captured = capsys.readouterr()
    assert "llm-agent config init" in captured.err
    assert captured.out == ""


def test_run_missing_config_json_is_actionable_without_prompt_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("headless prompt")),
    )

    assert cli.main(["run", "--json", "--home", str(home), "oi"]) == 2

    assert not (home / "config" / "config.json").exists()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "llm-agent config init" in payload["error"]
    assert captured.err == ""


def test_explicit_missing_config_never_uses_first_run_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    explicit = tmp_path / "explicit.json"
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("explicit config prompt")),
    )

    assert cli.main(["chat", "--config", str(explicit)]) == 2

    assert not explicit.exists()
    assert f"llm-agent config init --config {explicit}" in capsys.readouterr().err


def test_invalid_config_is_not_treated_as_first_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    config = home / "config" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"schema_version": 1, "max_tokens": "invalid"}', encoding="utf-8")
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("invalid config prompt")),
    )

    assert cli.main(["chat", "--home", str(home)]) == 2

    captured = capsys.readouterr()
    assert "llm-agent config init" not in captured.err
    assert "Configuração criada" not in captured.out


def test_existing_config_keeps_normal_chat_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "app"
    assert cli.main(["config", "init", "--home", str(home)]) == 0
    entered: list[bool] = []
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: entered.append(True))
    monkeypatch.setattr(
        cli.console,
        "input",
        lambda _prompt: "1",
    )

    assert cli.main(["chat", "--home", str(home)]) == 0
    assert entered == [True]


def test_first_run_init_failure_preserves_error_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent.interfaces.cli import maintenance

    home = tmp_path / "app"
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "y")
    monkeypatch.setattr(
        maintenance,
        "initialize_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("sem permissão")),
    )

    assert cli.main(["chat", "--home", str(home)]) == 2

    captured = capsys.readouterr()
    assert "sem permissão" in captured.err
    assert "Configuração criada" not in captured.out
    assert not (home / "config" / "config.json").exists()


def test_run_json_emits_one_document_and_disables_logging(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = _Application(_Result(True, "feito"))
    captured: dict[str, Any] = {}

    def create(_: Any, *, configure_logging: bool) -> _Application:
        captured["configure_logging"] = configure_logging
        return application

    monkeypatch.setattr(cli, "_create_application", create)

    assert cli.main(["run", "analise", "o projeto", "--json"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("\n") == 1
    assert json.loads(output.out) == {
        "answer": "feito",
        "error": None,
        "receipt": {},
        "report_path": None,
        "status": "succeeded",
        "success": True,
    }
    assert application.objective == "analise o projeto"
    assert application.closed is True
    assert captured["configure_logging"] is False


def test_run_failure_returns_one_and_still_closes_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = _Application(_Result(False, error="falhou"))
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)

    assert cli.main(["run", "objetivo"]) == 1

    assert capsys.readouterr().err.strip() == "falhou"
    assert application.closed is True


def test_human_run_projects_operational_receipt_without_hiding_model_answer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _Result(
        True,
        "Modifiquei e validei sample.py.",
        receipt={
            "workspace": "/tmp/workspace",
            "status": "succeeded",
            "tools": [{"tool": "code_task", "status": "succeeded", "executed": True, "invocation_id": "inv-1"}],
            "files_affected": ["sample.py"],
            "validation": {"ran": True, "outcome": "passed"},
            "rollback": {"occurred": False, "outcome": None},
        },
        report_path="/tmp/report.json",
    )
    application = _Application(result)
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)

    assert cli.main(["run", "modifique", "sample.py"]) == 0
    output = capsys.readouterr()
    assert "Modifiquei e validei sample.py." in output.out
    assert "files_affected: sample.py" in output.out
    assert "validation: passed" in output.out
    assert "executed=True" in output.out
    assert "report_path: /tmp/report.json" in output.out


def test_human_run_exposes_read_only_truth_against_model_mutation_claim(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _Result(
        True,
        "Modifiquei sample.py.",
        receipt={
            "workspace": "/tmp/workspace",
            "status": "succeeded",
            "tools": [{"tool": "file_reader", "status": "succeeded", "executed": True, "invocation_id": "inv-read"}],
            "files_affected": [],
            "validation": {"ran": False, "outcome": None},
            "rollback": {"occurred": False, "outcome": None},
        },
    )
    application = _Application(result)
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)

    assert cli.main(["run", "leia", "sample.py"]) == 0
    output = capsys.readouterr().out
    assert "Modifiquei sample.py." in output
    assert "files_affected: []" in output
    assert "validation: not_run" in output


def test_default_command_is_chat_and_closes_application(monkeypatch: pytest.MonkeyPatch) -> None:
    application = _Application()
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)
    monkeypatch.setattr(cli, "_chat_loop", lambda context: seen.setdefault("context", context))

    assert cli.main([]) == 0

    assert seen["context"].application is application
    assert seen["context"].workspace is application.workspace
    assert seen["context"].app_paths is application.paths
    assert seen["context"].workspace_paths is application.workspace_paths
    assert application.closed is True


def test_doctor_json_is_one_document_and_maps_diagnostics_to_exit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent.health_check

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    config = tmp_path / "config.json"
    workspace.mkdir()
    captured_options: dict[str, Any] = {}

    def health(**kwargs: Any) -> dict[str, Any]:
        captured_options.update(kwargs)
        return {
            "errors": 1,
            "warnings": 0,
            "readiness": {"offline_ready": False},
        }

    monkeypatch.setattr(agent.health_check, "run_health_check", health)

    assert (
        cli.main(
            [
                "doctor",
                "--json",
                "--home",
                str(home),
                "--workspace",
                str(workspace),
                "--config",
                str(config),
                "--profile",
                "alternate",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "errors": 1,
        "readiness": {"offline_ready": False},
        "warnings": 0,
    }
    assert captured_options["write_report"] is False
    assert captured_options["verbose"] is False
    assert captured_options["app_paths"].config_file == (home / "config" / "config.json").resolve()
    assert captured_options["workspace"] == workspace
    assert captured_options["config_path"] == str(config)
    assert captured_options["profile"] == "alternate"


def test_doctor_writes_report_only_when_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.health_check

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requested: list[bool] = []

    def health(**kwargs: Any) -> dict[str, Any]:
        requested.append(kwargs["write_report"])
        return {"readiness": {"offline_ready": True}}

    monkeypatch.setattr(agent.health_check, "run_health_check", health)

    assert cli.main(["doctor", "--workspace", str(workspace)]) == 0
    assert cli.main(["doctor", "--workspace", str(workspace), "--write-report"]) == 0

    assert requested == [False, True]


def test_state_migrate_copies_into_workspace_scope_and_preserves_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "app"
    workspace = tmp_path / "workspace"
    source = tmp_path / "legacy"
    workspace.mkdir()
    source.mkdir()
    legacy_memory = source / "agent_memory.json"
    legacy_memory.write_text("{}", encoding="utf-8")

    result = cli.main(
        [
            "state",
            "migrate",
            "--from",
            str(source),
            "--home",
            str(home),
            "--workspace",
            str(workspace),
        ]
    )

    assert result == 0
    assert legacy_memory.read_text(encoding="utf-8") == "{}"
    assert list((home / "data" / "workspaces").glob("*/agent_memory.json"))
    assert "Origem mantida" in capsys.readouterr().out


def test_json_bootstrap_error_is_a_single_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("config ausente")

    monkeypatch.setattr(cli, "_create_application", fail)

    assert cli.main(["run", "objetivo", "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "error": "config ausente",
        "status": "failed",
        "success": False,
    }


def test_run_yes_is_the_only_headless_auto_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.application import AgentApplication
    from agent.approval import AutoApprove, RequireExplicitApproval

    policies: list[Any] = []
    application = _Application()

    def create(**kwargs: Any) -> _Application:
        policies.append(kwargs["approval_policy"])
        return application

    monkeypatch.setattr(AgentApplication, "create", create)

    cli._create_application(
        cli.build_parser().parse_args(["run", "objetivo"]),
        configure_logging=False,
    )
    cli._create_application(
        cli.build_parser().parse_args(["run", "objetivo", "--yes"]),
        configure_logging=False,
    )

    assert isinstance(policies[0], RequireExplicitApproval)
    assert isinstance(policies[1], AutoApprove)


def test_run_task_authority_is_explicit_product_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.application import AgentApplication

    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> _Application:
        captured.update(kwargs)
        return _Application()

    monkeypatch.setattr(AgentApplication, "create", create)

    cli._create_application(
        cli.build_parser().parse_args(
            ["run", "--task-authority", "read", "--task-authority", "process", "objetivo"]
        ),
        configure_logging=False,
    )

    assert captured["task_authority_capabilities"] == ["read", "process"]
