"""Keyboard behavior for chat and composer-owned single-line prompts."""

from typing import Any


def _interrupt(shell: Any, event: Any) -> None:
    if shell._line_prompt_active:
        event.app.exit(exception=KeyboardInterrupt())
        return
    handler = shell._interrupt_handler
    if handler is not None:
        try:
            handler()
        except Exception:
            pass
    else:
        event.current_buffer.text = ""
    event.app.invalidate()


def _command_palette(shell: Any, event: Any) -> None:
    if shell._line_prompt_active:
        return
    request = getattr(shell, "request_command_palette", None)
    if callable(request):
        request(event)
        event.app.invalidate()
        return
    handler = shell._f2_handler
    if handler is not None:
        try:
            handler()
        except Exception:
            pass
    event.app.invalidate()


def key_bindings(shell: Any, key_bindings_type: Any) -> Any:
    bindings = key_bindings_type()

    @bindings.add("enter")
    def accept(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def newline(event: Any) -> None:
        if not shell._line_prompt_active:
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

    @bindings.add("c-c")
    def interrupt(event: Any) -> None:
        _interrupt(shell, event)

    @bindings.add("f2")
    def command_palette(event: Any) -> None:
        _command_palette(shell, event)

    return bindings
