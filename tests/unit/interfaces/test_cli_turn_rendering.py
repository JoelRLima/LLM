from __future__ import annotations

import ast
import inspect
import io
from types import SimpleNamespace
from typing import Any

from rich.console import Console

from agent.interfaces.cli import app, chat, inspector_rendering, turn_rendering
from agent.interfaces.cli.commands import handle_command


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


def _console_with_stream(*, width: int = 120) -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    return Console(file=stream, record=True, width=width, color_system=None), stream


def _context(*, diagnostic: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=SimpleNamespace(root="/tmp/project"),
        orchestrator=SimpleNamespace(operational_mode_label="READ ONLY"),
        session=SimpleNamespace(thinking_budget=2048),
        modo_diagnostico=diagnostic,
    )


def _result(
    *,
    success: bool = True,
    answer: str = "answer",
    error: str | None = None,
    receipt: dict[str, Any] | None = None,
    resolution: Any = None,
) -> SimpleNamespace:
    run_result = None if receipt is None else SimpleNamespace(receipt=receipt, report_path=None)
    return SimpleNamespace(
        success=success,
        status="succeeded" if success else "failed",
        answer=answer,
        error=error,
        reason_code=None,
        resolution=resolution,
        interaction_usage={"model_calls": 1, "accounted_tokens": 12, "token_usage_complete": True},
        run_result=run_result,
    )


class _Application:
    def __init__(self, result: Any, chunks: tuple[str, ...] = ()) -> None:
        self.result = result
        self.chunks = chunks
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def interact(self, text: str, **kwargs: Any) -> Any:
        self.calls.append((text, kwargs))
        callback = kwargs["stream_callback"]
        for chunk in self.chunks:
            callback(chunk)
        return self.result


def test_ux_t01_compact_startup() -> None:
    console = _console()
    turn_rendering.render_startup_status(console, _context())
    output = console.export_text()

    assert "LLM Agent" in output
    assert "Workspace" in output and "/tmp/project" in output
    assert "READ ONLY" in output and "Think ALTO" in output and "Diag OFF" in output
    assert "/help" in output
    assert "Comandos Disponíveis" not in output


def test_ux_t02_help_is_explicit_and_t03_chat_does_not_auto_help(monkeypatch) -> None:
    calls: list[bool] = []
    from agent.interfaces.cli import commands

    monkeypatch.setattr(commands, "exibir_menu", lambda: calls.append(True))
    handled, should_exit = handle_command("/help", SimpleNamespace())
    assert handled is True and should_exit is False and calls == [True]

    console = _console()
    monkeypatch.setattr(app, "console", console)
    monkeypatch.setattr(app, "_prompt", lambda _ctx: None)
    app._chat_loop(_context())
    assert calls == [True]


def test_ux_t04_prompt_is_compact_and_mode_aware(monkeypatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(app, "console", SimpleNamespace(input=lambda prompt: prompts.append(prompt) or ""))
    app._prompt(_context(diagnostic=1))
    assert prompts and "Você [READ ONLY] [DIAG] >" in prompts[0]
    assert "Pensar" not in prompts[0]


def test_ux_t05_natural_boundary_and_ux_t06_streamed_answer_once() -> None:
    result = _result(answer="abcdef")
    application = _Application(result, ("abc", "def"))
    console, stream = _console_with_stream()
    ctx = SimpleNamespace(application=application, modo_diagnostico=0)

    chat.run_agent_turn(console, ctx, "oi")
    output = stream.getvalue()

    assert len(application.calls) == 1
    assert application.calls[0][1]["boundary"] == "natural"
    assert callable(application.calls[0][1]["stream_callback"])
    assert output.count("abcdef") == 1


def test_ux_t07_empty_chunks_and_t08_non_stream_answer_once() -> None:
    for chunks in (("", ""), ()):
        result = _result(answer="fallback")
        console, stream = _console_with_stream()
        chat.run_agent_turn(console, SimpleNamespace(application=_Application(result, chunks), modo_diagnostico=0), "oi")
        assert stream.getvalue().count("fallback") == 1


def test_ux_t09_completion_uses_result_state_not_answer() -> None:
    console = _console()
    turn_rendering.render_turn_result(console, _result(success=True, answer="falhou"), 0)
    assert "Concluído" in console.export_text()

    console = _console()
    turn_rendering.render_turn_result(console, _result(success=False, answer="deu tudo certo"), 0)
    output = console.export_text()
    assert "Falhou" in output and "deu tudo certo" not in output


def test_ux_t10_t12_model_claims_cannot_create_effects() -> None:
    console = _console()
    turn_rendering.render_turn_result(console, _result(answer="Editei sample.py e validei."), 0)
    output = console.export_text().casefold()
    assert "arquivo" not in output and "validação aprovada" not in output and "tool" not in output

    receipt = {
        "tools": [{"tool": "file_reader", "status": "succeeded", "executed": True}],
        "files_affected": [],
        "validation": {"ran": False, "outcome": "passed"},
    }
    console = _console()
    turn_rendering.render_turn_result(console, _result(answer="Editei sample.py.", receipt=receipt), 0)
    output = console.export_text().casefold()
    assert "arquivo" not in output and "validação aprovada" not in output


def test_ux_t11_t13_t14_receipt_summary_is_canonical() -> None:
    receipt = {
        "tools": [{"tool": "code_task"}, {"tool": "validator"}],
        "files_affected": ["sample.py"],
        "validation": {"ran": True, "outcome": "passed"},
        "rollback": {"occurred": False, "outcome": None},
        "replan": None,
    }
    console = _console()
    turn_rendering.render_turn_result(console, _result(receipt=receipt), 0)
    output = console.export_text()
    assert "2 ferramentas" in output and "1 arquivo" in output and "validação aprovada" in output
    assert "rollback" not in output


def test_ux_t15_t16_diagnostics_are_bounded_public_data() -> None:
    resolution = SimpleNamespace(
        action="respond",
        boundary="natural",
        provenance="deterministic",
        ambiguity="none",
        directive=None,
        deliberation_profile="normal",
        reason_code=None,
        evidence="private resolver evidence",
    )
    receipt = {
        "tools": [{"tool": "code_task", "status": "succeeded", "executed": True, "invocation_id": "secret"}],
        "files_affected": ["sample.py"],
        "validation": {"ran": True, "outcome": "passed"},
    }
    console = _console()
    turn_rendering.render_turn_result(console, _result(receipt=receipt, resolution=resolution), 2)
    output = console.export_text()
    assert "respond" in output and "natural" in output and "deterministic" in output
    assert "private resolver evidence" not in output and "secret" not in output


def test_ux_t17_no_transcript_append_and_t24_no_productive_streaming_display() -> None:
    tree = ast.parse(inspect.getsource(chat.run_agent_turn))
    names = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not names.intersection({"add_user_message", "add_assistant_message", "commit_one_pair", "StreamingDisplay"})


def test_ux_t23_inspector_rendering_is_observation_only() -> None:
    snapshot = SimpleNamespace(
        run=SimpleNamespace(run_id="run-1", status="succeeded", completeness="complete", liveness={"state": "live"}, mode="READ ONLY"),
        heartbeat={"observer_heartbeat": "ok", "semantic_activity": "none", "silence": "0s"},
        timeline=(), current={}, plan_steps={}, model_calls={}, tools={}, validation={}, recovery={}, changes={}, metrics={}, convergence={},
        warnings=(), selected_detail=None, issues=(), mutation_calls=0,
    )
    console = _console()
    inspector_rendering.render_snapshot(snapshot, console)
    assert snapshot.mutation_calls == 0
    assert "run-1" in console.export_text()


def test_corrective_streamed_answer_is_literal_and_not_rich_wrapped() -> None:
    answer = ":smile:" + ("x" * 200) + "\t[/broken]\r\nend"
    console, stream = _console_with_stream(width=20)

    chat.run_agent_turn(
        console,
        SimpleNamespace(application=_Application(_result(answer=answer), (answer,)), modo_diagnostico=0),
        "oi",
    )

    output = stream.getvalue()
    assert output.count(answer) == 1
    assert ":smile:" in output
    assert "😄" not in output


def test_corrective_fallback_answer_is_literal_once_with_only_cli_newline() -> None:
    answer = ":smile: [bold red]literal[/bold red]\t[/broken]\r\nend"
    console, stream = _console_with_stream(width=20)

    chat.run_agent_turn(
        console,
        SimpleNamespace(application=_Application(_result(answer=answer)), modo_diagnostico=0),
        "oi",
    )

    output = stream.getvalue()
    start = output.index(answer)
    assert output.count(answer) == 1
    assert output[start + len(answer)] == "\n"
    assert "😄" not in output


def test_corrective_inspector_dynamic_content_is_literal() -> None:
    snapshot = SimpleNamespace(
        run=SimpleNamespace(run_id="run-1", status="succeeded", completeness="complete", liveness={"state": "live"}, mode="READ ONLY"),
        heartbeat={"observer_heartbeat": "ok", "semantic_activity": "none", "silence": "0s"},
        timeline=(),
        current={},
        plan_steps={},
        model_calls={},
        tools={},
        validation={},
        recovery={},
        changes={},
        metrics={},
        convergence={},
        warnings=(),
        selected_detail={"payload": "[/broken]"},
        issues=(),
        mutation_calls=0,
    )
    console = _console()

    inspector_rendering.render_snapshot(snapshot, console)

    assert "[/broken]" in console.export_text()


def test_corrective_verbose_receipt_dynamic_content_is_literal() -> None:
    receipt = {
        "files_affected": ["[/broken]"],
        "validation": {"ran": True, "outcome": "passed"},
    }
    result = _result(receipt=receipt)
    result.run_result.report_path = "[/broken]"
    console = _console()

    turn_rendering.render_turn_result(console, result, 2)

    assert "[/broken]" in console.export_text()


def test_ux_t25_turn_renderer_has_no_forbidden_owner_imports() -> None:
    forbidden = (
        "agent.runtime",
        "agent.planning",
        "agent.tools",
        "agent.memory",
        "agent.observability",
        "agent.reporting",
        "agent.application",
        "agent.orchestration",
        "agent.orchestrator",
        "agent.llm",
    )
    tree = ast.parse(inspect.getsource(turn_rendering))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)


def test_ux_t26_turn_renderer_has_no_fake_semantic_phase_labels() -> None:
    source = inspect.getsource(turn_rendering).casefold()
    forbidden = (
        "investigando",
        "investigating",
        "planejando",
        "planning",
        "executando ferramenta",
        "executing tool",
        "validando",
        "validating",
        "corrigindo",
        "correcting",
        "replanejando",
        "replanning",
    )

    assert "processando" in source
    assert not any(label in source for label in forbidden)


def test_ux_t27_failed_interaction_is_visible_once_without_fake_facts() -> None:
    failure = _result(success=False, answer="Falha: [/broken]", error="Falha: [/broken]")
    console, stream = _console_with_stream()

    chat.run_agent_turn(
        console,
        SimpleNamespace(application=_Application(failure), modo_diagnostico=0),
        "oi",
    )

    output = stream.getvalue()
    assert "✕ Falhou" in output
    assert "✓ Concluído" not in output
    assert output.count("Falha: [/broken]") == 1
    assert "ferramenta" not in output
    assert "arquivo" not in output
    assert "validação" not in output


def test_ux_t28_prompt_preserves_eof_and_keyboard_interrupt_shutdown(monkeypatch) -> None:
    for interruption in (EOFError, KeyboardInterrupt):
        stream = io.StringIO()
        rendered = Console(file=stream, record=True, width=120, color_system=None)

        def raise_interruption(_prompt: str, exc: type[BaseException] = interruption) -> str:
            raise exc

        monkeypatch.setattr(app, "console", SimpleNamespace(input=raise_interruption, print=rendered.print))

        assert app._prompt(_context()) is None
        assert "Encerrando..." in stream.getvalue()
