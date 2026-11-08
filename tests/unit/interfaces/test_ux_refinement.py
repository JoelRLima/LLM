from __future__ import annotations

import io
import re
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from llm_agent.interfaces.cli import (
    command_handlers,
    discovery_ui,
    interactive_commands,
    interactive_resources,
    startup_rendering,
    ui,
    workspace_entry,
)
from llm_agent.interfaces.cli.discovery_projection import discover_commands
from llm_agent.interfaces.cli.interactive_shell import InteractiveShell
from llm_agent.interfaces.cli.selector import SelectorItem, TerminalSelector
from llm_agent.interfaces.cli.ui_plane import RunViewModel


def _choose(answers, items=None, **kwargs):
    output = []
    iterator = iter(answers)
    selector = TerminalSelector(prompt_line=lambda *a, **k: next(iterator), emit=output.append)
    result = selector.choose(items if items is not None else (SelectorItem('action:run.cancel', '/cancel', 'Cancelar tarefa'),), title='Comandos', **kwargs)
    return result, '\n'.join(output)


@pytest.mark.parametrize('answer', ['', 'q', 'cancelar', None])
def test_selector_can_return_to_chat(answer):
    result, output = _choose([answer])
    assert result.cancelled
    assert 'número' in output and 'Ctrl+C' in output
    assert 'action:' not in output
    assert 'Cancelar tarefa' in output


def test_selector_prepares_typed_command_without_selecting_or_executing():
    result, _ = _choose(['/ls'], allow_command_draft=True)
    assert result.draft == '/ls' and result.item_id is None and not result.cancelled


def test_selector_preserves_ids_and_handles_ambiguous_disabled_and_huge_input():
    items = (SelectorItem('first', 'Modelo'), SelectorItem('second', 'Modo'), SelectorItem('disabled', 'Indisponível', disabled_reason='Ocupado'))
    result, output = _choose(['M', '3', '9' * 5000, 'Modelo'], items)
    assert result.item_id == 'first'
    assert 'ambígua' in output and 'Ocupado' in output and 'inválida' in output


def test_selector_empty_returns_without_reading_and_default_requires_enter():
    assert _choose([], ())[0].cancelled
    assert _choose([''], default_id='action:run.cancel')[0].item_id == 'action:run.cancel'


def test_primary_menu_is_filtered_before_discovery_result_limit():
    ids = frozenset(f'action:{action_id}' for action_id in ui.PRIMARY_ACTION_IDS)
    result = discover_commands(entry_ids=ids)
    assert {candidate.entry.entry_id for candidate in result.candidates} == ids
    assert any(candidate.entry.entry_id not in ids for candidate in discover_commands('memória').candidates)


def test_cli_sources_have_no_known_mojibake():
    source = Path(ui.__file__).parent
    for path in source.glob('*.py'):
        assert not re.search(r'Ã[^\x00-\x7F]|Â[^\x00-\x7F]|�', path.read_text(encoding='utf-8')), path


@pytest.mark.parametrize('answer,expected', [('/attention approve', '/attention approve'), ('1', '/cancel'), ('q', 'meu rascunho')])
def test_palette_selection_only_drafts_and_cancel_restores(monkeypatch, answer, expected):
    entry = SimpleNamespace(entry_id='action:run.cancel', preferred_invocation='/cancel', description='Cancelar tarefa', source_kind=SimpleNamespace(value='action'))
    result = SimpleNamespace(candidates=(SimpleNamespace(entry=entry, available=True),))
    monkeypatch.setattr(discovery_ui, 'discover_commands', lambda *a, **k: result)
    monkeypatch.setattr(discovery_ui, '_engineering_views', lambda c: ())
    drafts = []
    ctx = SimpleNamespace(shell=SimpleNamespace(prompt_line=lambda *a, **k: answer, print_background=lambda v: None, set_draft=drafts.append, take_palette_return_draft=lambda: 'meu rascunho'))
    discovery_ui.commands_action('/commands', ctx)
    assert drafts == [expected]


def test_idle_where_is_actionable_and_has_no_unknown():
    output = []
    ctx = SimpleNamespace(view_model=RunViewModel(), shell=SimpleNamespace(print_background=output.append))
    interactive_commands.where('/where', ctx)
    assert 'Nenhuma tarefa em execução' in output[0]
    assert 'unknown' not in output[0]


@pytest.mark.parametrize('width', [32, 80, 120])
def test_help_is_short_and_complete_help_preserves_advanced_actions(monkeypatch, width):
    stream = io.StringIO()
    monkeypatch.setattr(ui, 'console', Console(file=stream, width=width, color_system=None))
    ui.exibir_menu()
    text = stream.getvalue()
    assert '/help all' in text and '/commands' in text and '/cancel' in text
    assert '/attention approve' not in text
    stream.seek(0)
    stream.truncate()
    ui.exibir_menu(complete=True)
    assert '/code' in stream.getvalue() and 'aliases:' in stream.getvalue()


def test_default_model_does_not_claim_effective_identity_or_connectivity(monkeypatch):
    monkeypatch.setattr(startup_rendering, 'read_conversation', lambda c: SimpleNamespace(model='default', provider='openai_compatible'))
    text = startup_rendering.model_summary(None)
    assert 'alias do servidor' in text and 'não confirmado' in text
    assert 'Conectividade não verificada' in text


def test_cancelled_input_does_not_accept_model_default():
    reader = interactive_resources.prompt_line(lambda: SimpleNamespace(prompt_line=lambda *a, **k: None), True)
    result = TerminalSelector(prompt_line=reader, emit=lambda v: None).choose(
        (SelectorItem('default', 'Modelo padrão'),), title='Modelo', default_id='default',
    )
    assert result.cancelled and result.item_id is None


@pytest.mark.parametrize('text,online', [('/doctor', False), ('/doctor --online', True)])
def test_chat_doctor_only_requests_network_when_explicit(monkeypatch, text, online):
    requests = []
    def diagnose(request):
        requests.append(request)
        return SimpleNamespace(rendered_report='Diagnóstico simulado')
    monkeypatch.setattr('llm_agent.application.health.run_health_diagnostics', diagnose)
    command_handlers.doctor(text, SimpleNamespace(config={}))
    assert len(requests) == 1 and requests[0].online is online


@pytest.mark.parametrize('key', ['\x03', '\x1bOQq\r'])
def test_selector_ctrl_c_and_f2_do_not_call_chat_handlers(key):
    events = []
    with create_pipe_input() as pipe:
        shell = InteractiveShell(input=pipe, output=DummyOutput())
        shell.set_interrupt_handler(lambda: events.append('cancel task'))
        result = []
        thread = Thread(target=lambda: result.append(shell.prompt_line('Escolha: ')), daemon=True)
        thread.start()
        pipe.send_text(key)
        thread.join(5)
        shell.close()
        assert not thread.is_alive()
        assert result == ([None] if key == '\x03' else ['q'])
        assert events == []


def test_workspace_cancel_and_dynamic_range(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_entry, 'native_picker_available', lambda: False)
    output = []
    answers = iter(['99', 'q'])
    with pytest.raises(workspace_entry.WorkspaceSelectionCancelled):
        workspace_entry.choose_workspace(console=SimpleNamespace(print=lambda v, **k: output.append(v)), current=tmp_path, prompt=lambda m: next(answers))
    assert any('1 a 2' in value for value in output)
    assert workspace_entry.is_system_workspace(r'C:\Windows\System32')
    assert not workspace_entry.is_system_workspace(tmp_path)


def test_system_workspace_is_never_implicit_default(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_entry, 'is_system_workspace', lambda p: True)
    monkeypatch.setattr(workspace_entry, 'native_picker_available', lambda: False)
    answers = iter(['', 'q'])
    output = []
    with pytest.raises(workspace_entry.WorkspaceSelectionCancelled):
        workspace_entry.choose_workspace(console=SimpleNamespace(print=lambda v, **k: output.append(v)), current=tmp_path, prompt=lambda m: next(answers))
    assert any('Seleção cancelada' in value for value in output)
