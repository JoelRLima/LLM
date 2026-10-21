from __future__ import annotations

import inspect

import pytest

from llm_agent.agent.runtime.worker_output import bind_worker_output, emit_worker_output
from llm_agent.application import interactive_worker


def test_nested_worker_output_binding_restores_previous_sink_on_normal_and_exception_exit() -> None:
    output_a: list[str] = []
    output_b: list[str] = []
    output_c: list[str] = []

    with bind_worker_output(output_a.append):
        emit_worker_output("A1")
        with bind_worker_output(output_b.append):
            emit_worker_output("B1")
        emit_worker_output("A2")
        with pytest.raises(RuntimeError, match="nested failure"):
            with bind_worker_output(output_c.append):
                emit_worker_output("C1")
                raise RuntimeError("nested failure")
        emit_worker_output("A3")

    assert output_a == ["A1\n", "A2\n", "A3\n"]
    assert output_b == ["B1\n"]
    assert output_c == ["C1\n"]


def test_emitter_preserves_formatting_fallback_and_end_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    value = object()
    calls: list[tuple[object, str]] = []
    sink_output: list[str] = []

    emit_worker_output(value, end="!", fallback=lambda item, item_end: calls.append((item, item_end)))
    assert calls == [(value, "!")]

    with bind_worker_output(sink_output.append):
        emit_worker_output(value, end="", fallback=lambda *_: pytest.fail("fallback must not run"))
        emit_worker_output("")
    assert sink_output == [f"{value}", "\n"]

    printed: list[tuple[str, str]] = []
    monkeypatch.setattr("builtins.print", lambda text, end="\n": printed.append((text, end)))
    emit_worker_output("plain", end="!")
    assert printed == [("plain!", "")]


def test_sink_failure_does_not_try_fallback_and_fallback_failure_propagates() -> None:
    fallback_calls: list[object] = []

    def broken_sink(_text: str) -> None:
        raise LookupError("sink failure")

    def broken_fallback(_value: object, _end: str) -> None:
        raise ArithmeticError("fallback failure")

    with bind_worker_output(broken_sink):
        with pytest.raises(LookupError, match="sink failure"):
            emit_worker_output("x", fallback=lambda *args: fallback_calls.append(args))
    assert fallback_calls == []

    with pytest.raises(ArithmeticError, match="fallback failure"):
        emit_worker_output("x", fallback=broken_fallback)


def test_format_conversion_failure_precedes_fallback() -> None:
    class BadString:
        def __str__(self) -> str:
            raise RuntimeError("format failure")

    calls: list[object] = []
    with pytest.raises(RuntimeError, match="format failure"):
        emit_worker_output(BadString(), fallback=lambda *args: calls.append(args))
    assert calls == []


def test_interactive_worker_executes_inside_scope_and_preserves_return_and_exception() -> None:
    published: list[str] = []
    value = object()
    observations: list[str] = []

    def operation() -> object:
        emit_worker_output("inside")
        observations.append("ran")
        return value

    assert interactive_worker.run_interactive_worker(operation, publish_text=published.append) is value
    assert published == ["inside\n"]
    assert observations == ["ran"]
    after_scope: list[tuple[object, str]] = []
    emit_worker_output("after return", fallback=lambda item, end: after_scope.append((item, end)))
    assert after_scope == [("after return", "\n")]

    error = ValueError("operation failure")

    def failing_operation() -> None:
        emit_worker_output("before failure")
        raise error

    with pytest.raises(ValueError) as raised:
        interactive_worker.run_interactive_worker(failing_operation, publish_text=published.append)
    assert raised.value is error
    assert published[-1] == "before failure\n"
    emit_worker_output("after exception", fallback=lambda item, end: after_scope.append((item, end)))
    assert after_scope[-1] == ("after exception", "\n")
    assert interactive_worker.__all__ == ["run_interactive_worker"]
    public_annotations = str(inspect.signature(interactive_worker.run_interactive_worker))
    assert all(hidden not in public_annotations for hidden in ("ContextVar", "Token", "WorkerOutput"))
