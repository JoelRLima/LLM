from __future__ import annotations

import copy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from agent.interfaces.cli import command_handlers, first_run, interactive_admission
from agent.interfaces.cli.controller import InteractiveExecutionController, PendingStore, SubmissionEnvelope
from agent.interfaces.cli.manifest import DEFAULT_COMMAND_REGISTRY
from agent.interfaces.cli.query_plane import QueryRequest, QueryResult, ReadOnlyWorkspaceQueryService
from agent.runtime.config_errors import ConfigVersionError
from agent.runtime.config_repository import ConfigError, ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


class _Console:
    def __init__(self) -> None:
        self.output: list[str] = []

    def print(self, *values: object, **_: object) -> None:
        self.output.append(" ".join(map(str, values)))


def _envelope(text: str) -> SubmissionEnvelope:
    return SubmissionEnvelope(0, text, "natural_text", "AGENTIC", "AGENTIC_SUBMIT", "PENDING_EXACT_TEXT", text, "natural", "owner")


def test_config_update_is_typed_atomic_and_reload_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = ConfigRepository(AppPaths.discover(app_home=tmp_path / "app"))
    repository.initialize()
    before = repository.path.read_bytes()

    with pytest.raises(ConfigVersionError):
        repository.update({"schema_version": 99})
    assert repository.path.read_bytes() == before

    with pytest.raises(ConfigError):
        repository.update({"max_tokens": "many"})
    assert repository.path.read_bytes() == before

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("atomic write blocked")

    monkeypatch.setattr(repository, "_write_atomic", fail)
    with pytest.raises(OSError, match="atomic write blocked"):
        repository.update({"model": "candidate"})
    assert repository.path.read_bytes() == before


def test_model_profile_switch_is_idle_canonical_and_rebootstrap_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "app")
    repository = ConfigRepository(paths)
    repository.initialize()
    current = repository.load(environment={}).to_dict()
    current_profile = copy.deepcopy(current["model_profiles"][current["default_model_profile"]])
    current["model_profiles"]["alternate"] = current_profile
    repository.update({"model_profiles": {"alternate": current_profile}})
    config = repository.load(environment={}).to_dict()
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    context = SimpleNamespace(
        session=SimpleNamespace(model_profile=SimpleNamespace(model="default", provider="openai_compatible")),
        config=config,
        controller=SimpleNamespace(is_busy=lambda: False),
        app_paths=paths,
        config_path=None,
        shell=None,
    )

    command_handlers.model("/model select alternate", context)

    assert context.rebootstrap_profile == "alternate"
    assert repository.load(environment={}).to_dict()["default_model_profile"] == "alternate"

    before = repository.path.read_bytes()
    busy_context = SimpleNamespace(**{**context.__dict__, "controller": SimpleNamespace(is_busy=lambda: True)})
    busy_context.__dict__.pop("rebootstrap_profile", None)
    command_handlers.model("/model select alternate", busy_context)
    assert not hasattr(busy_context, "rebootstrap_profile")
    assert repository.path.read_bytes() == before


def test_workspace_switch_is_quiescent_and_does_not_mutate_active_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    target.mkdir()
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    context = SimpleNamespace(
        workspace=SimpleNamespace(root=current.resolve()),
        controller=SimpleNamespace(is_busy=lambda: False),
        approval_broker=SimpleNamespace(current=lambda: None),
        query_executor=SimpleNamespace(is_busy=lambda: False),
        shell=None,
    )

    command_handlers.show_workspace(f"/workspace switch {target}", context)
    assert context.workspace.root == current.resolve()
    assert context.rebootstrap_workspace == target.resolve()

    busy_context = SimpleNamespace(
        workspace=SimpleNamespace(root=current.resolve()),
        controller=SimpleNamespace(is_busy=lambda: True),
        approval_broker=SimpleNamespace(current=lambda: None),
        query_executor=SimpleNamespace(is_busy=lambda: False),
        shell=None,
    )
    command_handlers.show_workspace(f"/workspace switch {target}", busy_context)
    assert not hasattr(busy_context, "rebootstrap_workspace")


def test_guided_first_run_uses_canonical_update_and_marks_ready(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "app")
    args = SimpleNamespace(home=str(tmp_path / "app"), config=None)
    console = _Console()
    answers = iter(("y", "local_8gb", "guided-model", "http://127.0.0.1:8080/v1"))

    def prompt(_message: str, default: str = "") -> str:
        value = next(answers)
        return value or default

    assert first_run.recover_first_run_config(args, console=console, app_paths=paths, prompt=prompt) == 0
    assert args._first_run_guided is True
    document = ConfigRepository(paths).load(environment={}).to_dict()
    assert document["default_model_profile"] == "local_8gb"
    assert document["model_profiles"]["local_8gb"]["model"] == "guided-model"


def test_pending_edit_loads_composer_draft_without_auto_send(monkeypatch: pytest.MonkeyPatch) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    store = PendingStore()
    store.add(_envelope("follow-up to edit"))
    controller = InteractiveExecutionController(pending=store)
    context = SimpleNamespace(controller=controller, shell=None)

    command_handlers.pending("/pending edit 1", context)

    assert context.draft_text == "follow-up to edit"
    assert [item.visible_text for item in store.list()] == ["follow-up to edit"]
    assert any("loaded into the composer" in line for line in output.output)


def test_diff_defaults_to_bounded_file_first_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)
    captured: dict[str, object] = {}

    def fake_git(request: QueryRequest, arguments: list[str], _cancel: Event) -> QueryResult:
        captured["arguments"] = arguments
        return QueryResult(request.query_generation, request.workspace_id, request.workspace_generation, "diff", True, "1\t2\ta.py\n-\t-\tb.bin\n")

    monkeypatch.setattr(service, "_git", fake_git)
    result = service.diff(_request := QueryRequest(1, workspace.workspace_id, 1, "diff", {"paths": ()}, False), Event())

    assert result.ok
    assert "--numstat" in captured["arguments"]
    assert result.data["file_count"] == 2
    assert result.data["files"][0] == {"file": "a.py", "added": 1, "deleted": 2}
    assert result.data["files"][1]["added"] is None


def test_command_registry_is_argument_aware_and_unknown_slash_fails_closed_with_draft_preserved() -> None:
    for text, command_id in (
        ("/ls subdir", "list_files"),
        ("/diff --full src/app.py", "diff"),
        ("/workspace switch C:/workspace", "workspace"),
    ):
        entry, match = DEFAULT_COMMAND_REGISTRY.lookup(text)
        assert entry is not None and match == "prefix" and entry.canonical_command_id == command_id

    preserved: list[str] = []
    output: list[str] = []
    shell = SimpleNamespace(
        set_draft=lambda value: preserved.append(value),
        print_background=lambda value: output.append(str(value)),
    )
    context = SimpleNamespace(
        controller=SimpleNamespace(poll_result=lambda: None),
        shell=shell,
        draft_text="",
        view_model=None,
        event_mailbox=None,
    )
    assert interactive_admission.handle_input("/not-a-command payload", context) is False
    assert context.draft_text == "/not-a-command payload"
    assert preserved == ["/not-a-command payload"]
    assert any("entrada preservada" in value for value in output)


def test_busy_workspace_switch_rejects_and_preserves_the_exact_multiline_draft() -> None:
    preserved: list[str] = []
    shell = SimpleNamespace(set_draft=lambda value: preserved.append(value), print_background=lambda _value: None)
    context = SimpleNamespace(
        controller=SimpleNamespace(poll_result=lambda: None, is_busy=lambda: True),
        shell=shell,
        draft_text="",
        view_model=None,
        event_mailbox=None,
    )
    text = "/workspace switch C:/busy\nkeep-this-draft"
    assert interactive_admission.handle_input(text, context) is False
    assert context.draft_text == text
    assert preserved == [text]


def test_busy_pending_send_rejected_preserves_exact_unicode_multiline_command() -> None:
    release = Event()
    controller = InteractiveExecutionController()
    controller.submit(_envelope("active"), lambda *_args: (release.wait(2), "done")[1])
    pending = controller.submit(_envelope("follow-up"), lambda *_args: "later")
    assert pending.pending_id == 1
    preserved: list[str] = []
    output: list[str] = []
    shell = SimpleNamespace(
        set_draft=lambda value: preserved.append(value),
        print_background=lambda value: output.append(str(value)),
    )
    context = SimpleNamespace(
        controller=controller,
        shell=shell,
        draft_text="",
        view_model=None,
        event_mailbox=None,
    )
    text = "/pending send 1\nrevisar — mañana"

    assert interactive_admission.handle_input(text, context) is False
    assert context.draft_text == text
    assert preserved == [text]
    assert any("entrada preservada" in value for value in output)

    release.set()
    assert controller.wait_for_settlement(timeout_seconds=2) is not None
