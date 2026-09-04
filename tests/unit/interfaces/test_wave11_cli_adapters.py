from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent import application_result
from agent.interfaces.cli import app as cli
from agent.interfaces.cli import command_handlers, commands
from agent.interfaces.cli.parser import build_parser
from agent.interfaces.task_directives import (
    TASK_DIRECTIVE_CONFLICT,
    TaskRequestAction,
)
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)


@dataclass
class _Result:
    success: bool = True
    answer: str = "answer"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "succeeded" if self.success else "failed",
            "success": self.success,
            "answer": self.answer,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


class _Application:
    def __init__(self, result: _Result | None = None) -> None:
        self.result = result or _Result()
        self.closed = 0
        self.run_calls: list[tuple[str, TaskRunDirective | None]] = []
        self.resume_calls = 0

    def run(
        self,
        objective: str | None,
        *,
        task_run_directive: TaskRunDirective | None = None,
    ) -> _Result:
        assert objective is not None
        self.run_calls.append((objective, task_run_directive))
        return self.result

    def resume(self) -> _Result:
        self.resume_calls += 1
        return self.result

    def close(self) -> None:
        self.closed += 1


def test_headless_run_passes_typed_directive_and_preserves_model_profile(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = _Application(_Result(metadata={"task_directive": "read", "deliberation_profile": "smart"}))
    seen: dict[str, Any] = {}

    def create(args: Any, *, configure_logging: bool) -> _Application:
        seen["args"] = args
        seen["configure_logging"] = configure_logging
        return application

    monkeypatch.setattr(cli, "_create_application", create)

    assert cli.main(
        [
            "run",
            "--json",
            "--profile",
            "configured_model",
            "/read",
            "/smart",
            "Analyze",
            "repo",
        ]
    ) == 0

    request = application.run_calls == [
        (
            "Analyze repo",
            TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "Analyze repo"),
        )
    ]
    assert request
    assert seen["args"].profile == "configured_model"
    assert seen["configure_logging"] is False
    assert application.closed == 1
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_headless_default_keeps_objective_joining_and_auto_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application()
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)

    assert cli.main(["run", "Analyze", "the", "repo"]) == 0

    objective, directive = application.run_calls[0]
    assert objective == "Analyze the repo"
    assert directive == TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.NORMAL, objective)
    assert application.closed == 1


def test_headless_recognized_directive_never_downgrades_to_old_run_signature(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    executed: list[str] = []

    class OldStyleFacade:
        def run(self, subject: str) -> _Result:
            executed.append(subject)
            return _Result()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: OldStyleFacade())

    assert cli.main(["run", "--json", "/read", "inspect", "source"]) == 1

    document = json.loads(capsys.readouterr().out)
    assert document["success"] is False
    assert executed == []


def test_headless_parser_error_happens_before_application_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    created: list[object] = []

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        created.append(True)
        raise AssertionError("parser errors must not create an application")

    monkeypatch.setattr(cli, "_create_application", forbidden)

    assert cli.main(["run", "--json", "/read", "/do", "Analyze repo"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["success"] is False
    assert payload["reason_code"] == TASK_DIRECTIVE_CONFLICT
    assert created == []


def test_headless_first_unknown_slash_token_preserves_baseline_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application()
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)

    assert cli.main(["run", "/custom", "/read", "Analyze", "repo"]) == 0

    objective, directive = application.run_calls[0]
    assert objective == "/custom /read Analyze repo"
    assert directive == TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.NORMAL, objective)


def test_headless_do_keeps_existing_approval_flag_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application()
    seen: dict[str, Any] = {}

    def create(args: Any, **_kwargs: Any) -> _Application:
        seen["assume_yes"] = getattr(args, "assume_yes", False)
        return application

    monkeypatch.setattr(cli, "_create_application", create)

    assert cli.main(["run", "/do", "Apply the change"]) == 0

    assert seen["assume_yes"] is False
    assert application.run_calls[0][1] == TaskRunDirective(
        TaskDirective.DO,
        DeliberationProfile.NORMAL,
        "Apply the change",
    )


def test_headless_continue_delegates_to_w10_without_creating_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.interfaces.cli import task_continuity

    captured: dict[str, Any] = {}

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("CONTINUE must use the W10 adapter")

    def delegated(args: Any, **kwargs: Any) -> int:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 7

    monkeypatch.setattr(cli, "_create_application", forbidden)
    monkeypatch.setattr(task_continuity, "run_task_resume", delegated)

    assert cli.main(["run", "--json", "/continue"]) == 7
    assert captured["args"].objective == ["/continue"]
    assert captured["kwargs"]["create_application"] is forbidden


def test_headless_continue_absent_checkpoint_keeps_w10_failure_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application_created = False

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal application_created
        application_created = True
        raise AssertionError("absent CONTINUE must not create an application")

    monkeypatch.setattr(cli, "_create_application", forbidden)

    assert cli.main(
        [
            "run",
            "--json",
            "--home",
            str(tmp_path / "home"),
            "--workspace",
            str(workspace),
            "/continue",
        ]
    ) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["reason_code"] == "CHECKPOINT_ABSENT"
    assert payload["success"] is False
    assert application_created is False


def test_bare_interactive_read_stays_with_file_reader_and_skips_w11_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, Any] = {}

    def fail_parser(_raw: str) -> Any:
        raise AssertionError("bare /read must not enter the W11 parser")

    def fake_skill_result(ctx: Any, name: str, args: dict[str, Any], **_kwargs: Any) -> None:
        called.update({"ctx": ctx, "name": name, "args": args})

    monkeypatch.setattr(command_handlers, "parse_task_request", fail_parser)
    monkeypatch.setattr(command_handlers, "_skill_result", fake_skill_result)

    context = SimpleNamespace(orchestrator=SimpleNamespace())
    handled, should_exit = commands.handle_command("/read README.md", context)

    assert (handled, should_exit) == (True, False)
    assert called["name"] == "file_reader"
    assert called["args"] == {"file_path": "README.md"}


def test_interactive_agent_read_uses_application_run_with_w11_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application()
    messages: list[str] = []
    context = SimpleNamespace(
        application=application,
        session=SimpleNamespace(add_assistant_message=messages.append),
    )

    commands.handle_command("/agent /read Analyze README.md", context)

    assert application.run_calls == [
        (
            "Analyze README.md",
            TaskRunDirective(TaskDirective.READ, DeliberationProfile.NORMAL, "Analyze README.md"),
        )
    ]
    assert application.resume_calls == 0
    assert messages == ["answer"]


def test_interactive_agent_continue_uses_application_resume_without_override() -> None:
    application = _Application()
    messages: list[str] = []
    context = SimpleNamespace(
        application=application,
        session=SimpleNamespace(add_assistant_message=messages.append),
    )

    commands.handle_command("/agent /continue", context)

    assert application.resume_calls == 1
    assert application.run_calls == []
    assert messages == ["answer"]


def test_canonical_result_metadata_comes_from_restored_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "read source")
    orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(task_run_directive=directive),
        _canonical_run_snapshot=None,
    )
    application = SimpleNamespace(
        orchestrator=orchestrator,
        workspace=SimpleNamespace(root=tmp_path),
    )
    monkeypatch.setattr(application_result, "ensure_runtime_correlation", lambda _owner: object())
    monkeypatch.setattr(
        application_result,
        "build_canonical_run_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(status="succeeded"),
    )

    def finalize(
        _result_type: Any,
        workspace: str,
        _orchestrator: Any,
        _status: str,
        _answer: str,
        **kwargs: Any,
    ) -> Any:
        return _result_type(
            status="succeeded",
            answer="done",
            workspace=str(workspace),
            metadata=kwargs["metadata"],
        )

    monkeypatch.setattr(application_result, "finalize_run_result", finalize)

    result = application_result.finalize_application_result(
        application,
        "succeeded",
        "done",
        metadata={"task_directive": "do", "unrelated": "kept"},
    )

    assert result.metadata == {
        "task_directive": "read",
        "deliberation_profile": "smart",
        "unrelated": "kept",
    }


def test_run_help_documents_slash_profiles_and_continue_without_relabeling_do(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["run", "--help"])

    assert exited.value.code == 0
    output = capsys.readouterr().out
    assert 'llm-agent run "/read /smart Analise o repositorio"' in output
    assert 'llm-agent run "/continue"' in output
    assert "/do nao substitui --yes" in output


def test_parser_keeps_model_profile_flag_separate_from_task_profile() -> None:
    args = build_parser().parse_args(
        ["run", "--profile", "configured_model", "/smart", "Analyze repo"]
    )

    assert args.profile == "configured_model"
    request = cli.parse_task_request(" ".join(args.objective))
    assert request.action is TaskRequestAction.RUN
    assert request.directive_state is not None
    assert request.directive_state.deliberation_profile is DeliberationProfile.SMART
