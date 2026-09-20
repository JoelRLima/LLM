"""The single prompt-toolkit owner for interactive chat sessions.

The shell owns stdin, prompt rendering and composer state.  It deliberately
does not know how an agent task is executed; callers provide a toolbar
projection and consume submitted text through :meth:`prompt`.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator


class InteractiveShell:
    """One prompt session with safe background output and bounded UI state."""

    def __init__(
        self,
        *,
        registry: Any | None = None,
        session: Any | None = None,
        toolbar: Callable[[], Any] | None = None,
        history: Any | None = None,
        input: Any | None = None,
        output: Any | None = None,
    ) -> None:
        # PTK remains a lazy dependency for headless CLI imports.
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings

        self._lock = RLock()
        self._toolbar = toolbar
        self._registry = registry
        self._closed = False
        self._next_default = ""
        self._background_pump: Callable[[], None] | None = None
        self._interrupt_handler: Callable[[], None] | None = None
        self._background_hook_installed = False

        class _RegistryCompleter(Completer):
            def get_completions(inner_self, document: Any, complete_event: Any) -> Any:
                del complete_event
                text_before_cursor = document.text_before_cursor
                if not text_before_cursor.startswith("/"):
                    return
                for item in registry.completion_items(text_before_cursor) if registry is not None else ():
                    yield Completion(item, start_position=-len(text_before_cursor), display=item)

        bindings = KeyBindings()

        @bindings.add("enter")
        def _accept(event: Any) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _interrupt(event: Any) -> None:
            handler = self._interrupt_handler
            if handler is not None:
                try:
                    handler()
                except Exception:
                    # A control action is advisory; it must not tear down the
                    # prompt if a projection or cancellation seam fails.
                    pass
            else:
                event.current_buffer.text = ""
            event.app.invalidate()

        if session is None:
            session = PromptSession(
                multiline=True,
                input=input,
                output=output,
                enable_history_search=True,
                mouse_support=False,
                key_bindings=bindings,
                completer=_RegistryCompleter(),
                complete_while_typing=False,
                refresh_interval=0.1,
                bottom_toolbar=self._toolbar_text,
                history=history or InMemoryHistory(),
            )
        self.session = session

    def _toolbar_text(self) -> Any:
        if self._toolbar is None:
            return ""
        try:
            return self._toolbar()
        except Exception:
            # A stale projection must never break the composer.
            return ""

    @property
    def output(self) -> Any:
        return getattr(self.session, "output", None)

    def prompt(self, message: str = "", *, default: str = "") -> str | None:
        """Read one submitted composer value without holding the shell lock."""

        with self._lock:
            if self._closed:
                return None
            effective_default = default or self._next_default
            self._next_default = ""
        try:
            value = self.session.prompt(message, default=effective_default)
        except EOFError:
            return None
        except KeyboardInterrupt:
            # PromptSession's fallback SIGINT path must remain an escape, not
            # an implicit chat shutdown.  The key binding handles normal PTK
            # input; this fallback is for signals and test doubles.
            handler = self._interrupt_handler
            if handler is not None:
                try:
                    handler()
                except Exception:
                    pass
            return self.current_draft()
        return str(value)

    def current_draft(self) -> str:
        buffer = getattr(self.session, "default_buffer", None)
        return str(getattr(buffer, "text", "") or "")

    def set_interrupt_handler(self, callback: Callable[[], None] | None) -> None:
        self._interrupt_handler = callback

    def set_background_pump(self, callback: Callable[[], None] | None) -> None:
        """Run the consumer on PTK's UI event loop while ``prompt`` blocks."""

        self._background_pump = callback
        app = getattr(self.session, "app", None)
        if app is None or self._background_hook_installed:
            return

        def after_render(_app: Any) -> None:
            pump = self._background_pump
            if pump is not None:
                try:
                    pump()
                except Exception:
                    # Rendering/observation failures cannot break stdin.
                    pass

        app.after_render += after_render
        self._background_hook_installed = True

    def clear_draft(self) -> None:
        with self._lock:
            buffer = getattr(self.session, "default_buffer", None)
            if buffer is not None:
                buffer.text = ""

    def _prompt_line_value(self, message: str, default: str) -> str | None:
        with self._lock:
            if self._closed:
                return None
        try:
            value = self.session.prompt(message, default=default, multiline=False)
        except (EOFError, KeyboardInterrupt):
            return None
        return str(value)

    def set_draft(self, value: str) -> None:
        """Load one bounded pending value into the next composer prompt."""

        with self._lock:
            self._next_default = str(value)

    def prompt_line(self, message: str = "", *, default: str = "") -> str | None:
        """Read a selector value through the same composer-owned stdin."""

        return self._prompt_line_value(message, default)

    def print_background(self, text: object = "", *, end: str = "\n") -> None:
        """Print literal worker output above the prompt without consuming stdin."""

        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.shortcuts import print_formatted_text

        with self._lock:
            if self._closed:
                return
            output = self.output
            if output is None:
                return
            print_formatted_text(FormattedText([("", str(text))]), output=output, end=end)

    def print_stream(self, text: object = "") -> None:
        """Append one assistant chunk without inventing a line boundary."""

        self.print_background(text, end="")

    @contextmanager
    def temporary_prompt(self) -> Iterator["InteractiveShell"]:
        """Keep selector ownership explicit while preserving the current draft."""

        # PromptSession restores the buffer after a nested prompt returns.  A
        # context manager makes that invariant visible to selector callers.
        yield self

    def refresh(self) -> None:
        """Request a redraw after an externally updated toolbar projection."""

        app = getattr(self.session, "app", None)
        if app is not None and getattr(app, "is_running", False):
            app.invalidate()

    def close(self) -> None:
        with self._lock:
            self._closed = True


def prompt_from(
    console: Any,
    prompt: str,
    *,
    default: str = "",
    suppress_interrupt: bool = False,
) -> str | None:
    """Compatibility bridge for non-shell unit/legacy surfaces.

    The chat path always injects ``InteractiveShell.prompt_line``.  This
    bridge exists for the pre-existing standalone selector APIs and does not
    become a second reader when the shell is active.
    """

    reader = getattr(console, "input", None)
    if not callable(reader):
        return None
    try:
        value = reader(prompt)
    except (EOFError, KeyboardInterrupt):
        if not suppress_interrupt:
            raise
        printer = getattr(console, "print", None)
        if callable(printer):
            printer("Encerrando...")
        return None
    return str(value) if value is not None else default


__all__ = ["InteractiveShell", "prompt_from"]
