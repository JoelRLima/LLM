"""C10 CLI dispatch, mode gates and cleanup parity through Application handles."""

from types import SimpleNamespace

import pytest

from llm_agent.application import task_execution as api
from llm_agent.application.conversation import ConversationRuntime
from llm_agent.interfaces.cli import app, command_handlers, interactive_worker
from llm_agent.interfaces.cli.commands import CommandContext


@pytest.mark.parametrize("mode,write,full", [(None, True, False), ("read-only", False, False),
                                           ("editor", True, False), ("full", True, True)])
def test_sync_worker_c7_live_gate_parity(monkeypatch, mode, write, full):
    current = {"mode": mode}
    # Application capability seam is what these Interface consumers observe.
    runtime = api._retain_runtime(SimpleNamespace())
    def capabilities(handle):
        assert handle is runtime
        selected = current["mode"]
        return api.TaskExecutionCapabilities("FULL" if selected is None else selected,
                                             selected != "read-only", selected == "full")
    monkeypatch.setattr(command_handlers, "read_execution_capabilities", capabilities)
    monkeypatch.setattr(interactive_worker, "read_execution_capabilities", capabilities)
    observed = []
    def execute(_text, **kwargs):
        observed.append((kwargs["allows_write_validate"](), kwargs["is_full_mode"]()))
        current["mode"] = "read-only"
        assert not kwargs["allows_write_validate"]()
        current["mode"] = mode
        return SimpleNamespace(kind="help", help_text="help")
    monkeypatch.setattr(command_handlers, "execute_code_command", execute)
    monkeypatch.setattr(interactive_worker, "execute_code_command", execute)
    monkeypatch.setattr(command_handlers.console, "print", lambda *_args, **_kw: None)
    ctx = SimpleNamespace(task_execution=runtime, config={}, conversation=ConversationRuntime(SimpleNamespace()),
                          workspace=None, approval_broker=None)
    command_handlers.code_command("/code help", ctx)
    interactive_worker._execute_code(ctx, SimpleNamespace(visible_text="/code help"),
                                     SimpleNamespace(is_set=lambda: False), lambda _: None)
    assert observed == [(write, full), (write, full)]


def test_headless_close_before_output_and_visible_prefix(monkeypatch, capsys):
    order = []
    raw = {"success": True, "status": "succeeded", "answer": "done", "error": None, "receipt": {}}
    calls = []
    owner = SimpleNamespace(interact=lambda *args, **kw: calls.append((args, kw)) or
                            SimpleNamespace(to_dict=lambda: dict(raw)), close=lambda: order.append("close"))
    runtime = api._retain_runtime(owner)
    monkeypatch.setattr(app, "_create_application", lambda *_args, **_kw: runtime)
    monkeypatch.setattr(app, "_print_json", lambda value: order.append(("output", value)))
    assert app.main(["run", "--workspace", ".", "--json", "/read", "/smart", "source"]) == 0
    assert calls == [(("source",), {"boundary": "task", "visible_user_text": "/read /smart source",
                                   "task_payload": "/read /smart source"})]
    assert order == ["close", ("output", raw)]
    assert capsys.readouterr().err == ""


def test_invalid_dispatch_never_starts_and_context_has_no_raw_fields(monkeypatch):
    monkeypatch.setattr(app, "_create_application", lambda *_args, **_kw: pytest.fail("created"))
    assert app.main(["run", "--workspace", ".", "/read", "/do", "source"]) == 2
    runtime = api._retain_runtime(SimpleNamespace())
    conversation = ConversationRuntime(SimpleNamespace())
    ctx = CommandContext(conversation, runtime, {})
    assert ctx.task_execution is runtime and ctx.conversation is conversation
    assert not any(hasattr(ctx, name) for name in ("application", "orchestrator", "session"))


@pytest.mark.parametrize("name,expected", [(None, "tests_denied"), ("read-only", "mode_denied"),
                                         ("editor", "tests_denied"), ("full", "execution")])
def test_tests_flag_uses_original_live_mode_in_both_cli_routes(monkeypatch, name, expected):
    from threading import Event

    from llm_agent.agent.orchestration.operational_modes import OperationalModeMixin
    from llm_agent.agent.tools.authority import OperationalMode
    from llm_agent.application import code_commands

    orchestrator = OperationalModeMixin()
    orchestrator._operational_mode = None if name is None else OperationalMode.parse(name)
    runtime = api._retain_runtime(SimpleNamespace(orchestrator=orchestrator))
    ctx = SimpleNamespace(task_execution=runtime, config={}, conversation=ConversationRuntime(SimpleNamespace(gateway=None)),
                          workspace=None, approval_broker=None)

    def execution_reached(*args):
        raise RuntimeError("execution admitted")

    monkeypatch.setattr(code_commands, "build_code_context", execution_reached)
    monkeypatch.setattr(command_handlers.console, "print", lambda *args, **kwargs: None)
    text = "/code modify a.py --tests -- change"
    envelope = SimpleNamespace(visible_text=text)
    if expected == "execution":
        with pytest.raises(RuntimeError, match="execution admitted"):
            command_handlers.code_command(text, ctx)
        with pytest.raises(RuntimeError, match="execution admitted"):
            interactive_worker._execute_code(ctx, envelope, Event(), lambda callback: None)
    else:
        assert interactive_worker._execute_code(ctx, envelope, Event(), lambda callback: None).kind == expected
        command_handlers.code_command(text, ctx)


def test_rebootstrap_settles_detaches_and_closes_before_new_identity(monkeypatch, tmp_path):
    from argparse import Namespace

    from llm_agent.application.context import AppPaths
    from llm_agent.interfaces.cli import bootstrap, interactive_resources, interactive_session

    events = []
    contexts = []
    runtimes = []
    paths = AppPaths.discover(tmp_path / "home", env={})
    monkeypatch.setattr(interactive_session.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(interactive_session.first_run, "prepare_chat_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactive_resources.workspace_entry, "remember_workspace", lambda *args: None)
    monkeypatch.setattr(interactive_resources, "build_application_services", lambda *args:
                        SimpleNamespace(output_service=None, query_service=None))
    for operation, label in (("_settle_query", "query"), ("_settle_controller", "worker")):
        original = getattr(interactive_resources, operation)

        def settle(*args, original=original, label=label):
            result = original(*args)
            events.append(label)
            return result

        monkeypatch.setattr(interactive_resources, operation, settle)

    def create(*args, **kwargs):
        index = len(runtimes)
        events.append(f"startup{index}")
        dispatcher = SimpleNamespace(add_sink=lambda sink: events.append(f"bind{index}"),
                                     remove_sink=lambda sink: events.append(f"detach{index}"))
        owner = SimpleNamespace(config={}, paths=paths, workspace=SimpleNamespace(root=tmp_path, workspace_id="w"),
                                workspace_paths=object(), session=SimpleNamespace(),
                                orchestrator=SimpleNamespace(event_dispatcher=dispatcher),
                                tool_invocation_gateway=SimpleNamespace(), close=lambda: events.append(f"close{index}"))
        runtime = api._retain_runtime(owner)
        runtimes.append(runtime)
        return runtime

    def loop(context):
        contexts.append(context)
        if len(contexts) == 1:
            context.rebootstrap_profile = "alternate"

    args = Namespace(workspace=str(tmp_path), config=None, profile=None)
    assert interactive_session.run_chat(args, value=lambda args, name, default=None: getattr(args, name, default),
                                        app_paths=lambda args: paths, create_application=create,
                                        context_from_application=bootstrap.context_from_application,
                                        chat_loop_fn=loop) == 0
    assert events == ["startup0", "bind0", "query", "worker", "detach0", "close0",
                      "startup1", "bind1", "query", "worker", "detach1", "close1"]
    assert contexts[0].task_execution is runtimes[0]
    assert contexts[1].task_execution is runtimes[1]
    assert contexts[0].conversation is not contexts[1].conversation
    assert contexts[1].conversation._session is runtimes[1]._owner.session


def test_toolbar_consults_replacement_runtime_holder(monkeypatch):
    from llm_agent.interfaces.cli import interactive_resources, interactive_shell

    monkeypatch.setattr(interactive_shell, "InteractiveShell", lambda **kwargs:
                        SimpleNamespace(toolbar=kwargs["toolbar"], set_f2_handler=lambda value: None))
    old = api._retain_runtime(SimpleNamespace(orchestrator=SimpleNamespace(operational_mode_label="READ ONLY")))
    new = api._retain_runtime(SimpleNamespace(orchestrator=SimpleNamespace(operational_mode_label="EDITOR")))
    holder = {"task_execution": old}
    shell = interactive_resources.get_shell({"shell": None},
                                            {"view": SimpleNamespace(render_toolbar=lambda **kwargs: kwargs["mode"])},
                                            holder, {"controller": None})
    assert shell.toolbar() == "READ ONLY"
    holder["task_execution"] = new
    assert shell.toolbar() == "EDITOR"
