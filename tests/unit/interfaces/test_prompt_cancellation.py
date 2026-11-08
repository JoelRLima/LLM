"""Cancellation preserves files/session state; Enter still confirms defaults."""

from argparse import Namespace
from copy import deepcopy
from threading import Thread
from types import SimpleNamespace

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from llm_agent.agent.llm.session import ChatSession
from llm_agent.application.context import AppPaths
from llm_agent.application.conversation import bind_conversation
from llm_agent.application.task_execution import _retain_runtime
from llm_agent.interactive import local_commands
from llm_agent.interfaces.cli import command_handlers, first_run, interactive_input, memory_commands, workspace_entry
from llm_agent.interfaces.cli.interactive_shell import InteractiveShell, prompt_from
from llm_agent.interfaces.cli.selector import SelectorItem, TerminalSelector
from tests.support.offline_scenarios import OfflineChatGateway


def _console():
    output = []
    return SimpleNamespace(print=lambda *v, **k: output.append(' '.join(map(str, v)))), output


def _answer(value):
    def read(*args, **kwargs):
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value()
        return value
    return read


def _context(tmp_path, reader):
    session = ChatSession('original', {}, gateway=OfflineChatGateway('sem chamada de modelo'))
    session.add_user_message('mensagem preservada')
    runtime = _retain_runtime(SimpleNamespace(session=session))
    return SimpleNamespace(
        task_execution=runtime,
        conversation=bind_conversation(runtime),
        prompt_line=reader,
        workspace_paths=SimpleNamespace(chat_history_file=tmp_path / 'history.json', memory_file=tmp_path / 'memory.json'),
    ), session


@pytest.mark.parametrize('value', [None, KeyboardInterrupt, EOFError, 'n', 'talvez'])
def test_first_run_cancellation_or_invalid_confirmation_creates_nothing(tmp_path, value):
    paths = AppPaths.discover(app_home=tmp_path / 'home', env={})
    console, output = _console()
    args = Namespace(home=None, config=None, _first_run_guided=True)
    assert first_run.recover_first_run_config(args, console=console, app_paths=paths, prompt=_answer(value)) == 0
    assert not paths.config_file.exists()
    assert args._first_run_guided is False
    assert any('Nenhum arquivo foi criado' in line for line in output)


@pytest.mark.parametrize('stage', range(3))
@pytest.mark.parametrize('cancel', [None, KeyboardInterrupt, EOFError])
def test_guided_cancel_preserves_only_previously_confirmed_default(tmp_path, monkeypatch, stage, cancel):
    paths = AppPaths.discover(app_home=tmp_path / 'home', env={})
    initial = []
    initialize = first_run.initialize_configuration
    def create(*args):
        path = initialize(*args)
        initial.append(path.read_bytes())
        return path
    monkeypatch.setattr(first_run, 'initialize_configuration', create)
    answers = iter(['yes'] + [''] * stage + [cancel])
    # Legacy one-argument reader exercises the fallback on every prompt.
    def prompt(message):
        return _answer(next(answers))()
    console, output = _console()
    args = Namespace(home=None, config=None)
    first_run.recover_first_run_config(args, console=console, app_paths=paths, prompt=prompt)
    assert paths.config_file.read_bytes() == initial[0]
    assert args._first_run_guided is False
    assert any('guiada cancelada' in line for line in output)
    assert not any('guiada validada' in line for line in output)


def test_guided_enter_confirms_defaults_and_invalid_profile_is_not_applied(tmp_path):
    for name, answers, guided in [('enter', ['', '', '', ''], True), ('invalid', ['yes', 'perfil-inexistente'], False)]:
        paths = AppPaths.discover(app_home=tmp_path / name, env={})
        console, output = _console()
        values = iter(answers)
        args = Namespace(home=None, config=None)
        first_run.recover_first_run_config(args, console=console, app_paths=paths, prompt=lambda message, values=values, **kwargs: next(values))
        assert paths.config_file.is_file()
        assert args._first_run_guided is guided
        view = first_run.read_first_run_configuration(paths)
        assert view.default_profile in {profile.name for profile in view.profiles}
        if not guided:
            assert any('Profile desconhecido' in line for line in output)


@pytest.mark.parametrize('value', [None, KeyboardInterrupt, EOFError])
@pytest.mark.parametrize('action', ['save_history', 'load_history', 'toggle_thinking', 'system_prompt'])
def test_effectful_conversation_prompts_cancel_without_file_or_session_changes(tmp_path, value, action):
    ctx, session = _context(tmp_path, _answer(value))
    path = ctx.workspace_paths.chat_history_file
    path.write_text('[{"role":"system","content":"outro histórico"}]', encoding='utf-8')
    before = path.read_bytes(), deepcopy(session.messages), session.thinking_budget
    getattr(command_handlers, action)('/command', ctx)
    assert (path.read_bytes(), session.messages, session.thinking_budget) == before


@pytest.mark.parametrize('value', ['', 'inválido', 'M', '120'])
def test_thinking_enter_and_invalid_input_keep_existing_fallback(tmp_path, value):
    ctx, session = _context(tmp_path, _answer(value))
    command_handlers.toggle_thinking('/thinking', ctx)
    expected = command_handlers.THINKING_PRESET_BY_KEY.get(value.upper(), command_handlers.DEFAULT_THINKING_BUDGET)
    assert session.thinking_budget == (120 if value == '120' else expected)


def test_history_enter_really_saves_and_loads_default_file(tmp_path):
    ctx, session = _context(tmp_path, _answer(''))
    messages = deepcopy(session.messages)
    command_handlers.save_history('/save', ctx)
    assert ctx.workspace_paths.chat_history_file.is_file()
    session.add_user_message('depois de salvar')
    command_handlers.load_history('/load', ctx)
    assert session.messages == messages


@pytest.mark.parametrize('action', ['save_history', 'load_history'])
def test_invalid_history_path_or_content_reports_failure_without_default_effect(tmp_path, monkeypatch, action):
    path = tmp_path / 'missing-parent' / 'history.json' if action == 'save_history' else tmp_path / 'invalid.json'
    if action == 'load_history':
        path.write_text('{JSON inválido', encoding='utf-8')
    ctx, session = _context(tmp_path, _answer(str(path)))
    messages = deepcopy(session.messages)
    console, output = _console()
    monkeypatch.setattr(command_handlers, 'console', console)
    getattr(command_handlers, action)('/history', ctx)
    assert session.messages == messages
    assert not ctx.workspace_paths.chat_history_file.exists()
    if action == 'save_history':
        assert not path.exists()
    assert any('Erro ao ' in line for line in output)


@pytest.mark.parametrize('action', ['save_memory', 'load_memory', 'forget'])
@pytest.mark.parametrize('value', [None, KeyboardInterrupt, EOFError])
def test_memory_cancel_never_reaches_effect_port(tmp_path, monkeypatch, value, action):
    ctx, _ = _context(tmp_path, _answer(value))
    def forbidden(*args, **kwargs):
        pytest.fail('Operação de memória executada após cancelar')
    monkeypatch.setattr(local_commands, 'execute_memory_command', forbidden)
    getattr(memory_commands, action)('/memory', ctx)
    assert not ctx.workspace_paths.memory_file.exists()


@pytest.mark.parametrize('value', [None, KeyboardInterrupt, EOFError, '', ' caminho '])
def test_console_fallback_preserves_cancel_and_enter(monkeypatch, value):
    console = SimpleNamespace(input=_answer(value))
    monkeypatch.setattr(interactive_input, 'console', console)
    expected = value.strip() if isinstance(value, str) else None
    assert interactive_input.prompt_value(SimpleNamespace(), 'Caminho:', default='padrão') == expected
    if value is None:
        assert prompt_from(console, 'Caminho:', default='padrão') is None


@pytest.mark.parametrize('disabled', [None, 'Indisponível'])
def test_duplicate_exact_labels_are_ambiguous_including_disabled_items(disabled):
    values = iter(['Mesmo nome', 'second'])
    output = []
    result = TerminalSelector(prompt_line=lambda *a, **k: next(values), emit=output.append).choose(
        (SelectorItem('first', 'Mesmo nome', disabled_reason=disabled), SelectorItem('second', 'Mesmo nome')),
        title='Escolha',
    )
    assert result.item_id == 'second'
    assert any('ambígua' in line for line in output)


def test_exact_id_precedes_label_collision():
    result = TerminalSelector(prompt_line=lambda *a, **k: 'chosen', emit=lambda v: None).choose(
        (SelectorItem('other', 'chosen'), SelectorItem('chosen', 'Outro nome')), title='Escolha',
    )
    assert result.item_id == 'chosen'


@pytest.mark.parametrize('stage', ['menu', 'path'])
@pytest.mark.parametrize('value', [None, KeyboardInterrupt, EOFError])
def test_workspace_console_fallback_cancels_without_selecting(tmp_path, monkeypatch, stage, value):
    monkeypatch.setattr(workspace_entry, 'native_picker_available', lambda: False)
    console, _ = _console()
    if stage == 'menu':
        console.input = _answer(value)
    else:
        answers = iter(['2', value])
        console.input = lambda *args: _answer(next(answers))()
    with pytest.raises(workspace_entry.WorkspaceSelectionCancelled):
        workspace_entry.choose_workspace(console=console, current=tmp_path)


def test_system_directory_hint_does_not_reject_explicit_choice(tmp_path, monkeypatch):
    assert workspace_entry.is_system_workspace(r'C:\Windows\System32')
    assert not workspace_entry.is_system_workspace(r'D:\projetos\Windows\demo')
    assert not workspace_entry.is_system_workspace(r'D:\projetos\Program Files\demo')
    monkeypatch.setattr(workspace_entry, 'is_system_workspace', lambda path: True)
    monkeypatch.setattr(workspace_entry, 'native_picker_available', lambda: False)
    assert workspace_entry.choose_workspace(console=_console()[0], current=tmp_path, prompt=lambda message: '1') == tmp_path.resolve()


@pytest.mark.parametrize('action', ['toggle_thinking', 'save_history', 'load_history', 'setup'])
def test_real_ctrl_c_returns_to_chat_without_side_effects(tmp_path, action):
    with create_pipe_input() as pipe:
        shell = InteractiveShell(input=pipe, output=DummyOutput())
        ctx, session = _context(tmp_path, shell.prompt_line)
        before = deepcopy(session.messages)
        paths = AppPaths.discover(app_home=tmp_path / 'home', env={})
        failures = []
        def execute():
            try:
                if action == 'setup':
                    first_run.recover_first_run_config(Namespace(home=None, config=None), console=_console()[0], app_paths=paths, prompt=shell.prompt_line)
                else:
                    getattr(command_handlers, action)('/command', ctx)
            except BaseException as exc:
                failures.append(exc)
        thread = Thread(target=execute, daemon=True)
        thread.start()
        pipe.send_text('\x03')
        thread.join(5)
        try:
            assert not thread.is_alive() and not failures
            assert session.messages == before and session.thinking_budget == 0
            assert not ctx.workspace_paths.chat_history_file.exists()
            assert not paths.config_file.exists()
            pipe.send_text('voltei\r')
            assert shell.prompt('Chat: ') == 'voltei'
        finally:
            shell.close()
