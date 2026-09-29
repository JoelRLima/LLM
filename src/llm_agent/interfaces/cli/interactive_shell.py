"""The single prompt-toolkit owner for interactive chat sessions.

The shell owns stdin, prompt rendering and composer state.  It deliberately
does not know how an agent task is executed; callers provide a toolbar
projection and consume submitted text through :meth:`prompt`.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator


def prompt_from(
    console: Any,
    prompt: str,
    *,
    default: str = "",
    suppress_interrupt: bool = False,
) -> str | None:
    """Read through the single supplied console without creating a second shell."""
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


def _registry_completer(registry: Any, completer_type: Any, completion_type: Any) -> Any:
    class RegistryCompleter(completer_type):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            del self, complete_event
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            for item in registry.completion_items(text) if registry is not None else ():
                yield completion_type(item, start_position=-len(text), display=item)

    return RegistryCompleter()


def _key_bindings(shell: Any, key_bindings_type: Any) -> Any:
    bindings = key_bindings_type()

    @bindings.add("enter")
    def accept(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def interrupt(event: Any) -> None:
        handler = shell._interrupt_handler
        if handler is not None:
            try:
                handler()
            except Exception:
                pass
        else:
            event.current_buffer.text = ""
        event.app.invalidate()

    @bindings.add("f2")
    def command_palette(event: Any) -> None:
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

    return bindings


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
        f2_handler: Callable[[], None] | None = None,
    ) -> None:
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
        self._f2_handler = f2_handler
        self._palette_return_draft: str | None = None
        self._background_hook_installed = False
        if session is None:
            bindings = _key_bindings(self, KeyBindings)
            session = PromptSession(
                multiline=True,
                input=input,
                output=output,
                enable_history_search=True,
                mouse_support=False,
                key_bindings=bindings,
                completer=_registry_completer(registry, Completer, Completion),
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

    def set_f2_handler(self, callback: Callable[[], None] | None) -> None:
        self._f2_handler = callback

    def request_command_palette(self, event: Any | None = None) -> None:
        """Submit /commands through the active buffer without nested stdin."""

        buffer = getattr(self.session, "default_buffer", None)
        if buffer is None:
            return
        self._palette_return_draft = str(getattr(buffer, "text", "") or "")
        buffer.text = "/commands"
        if event is not None and callable(getattr(buffer, "validate_and_handle", None)):
            buffer.validate_and_handle()

    def take_palette_return_draft(self) -> str | None:
        value = self._palette_return_draft
        self._palette_return_draft = None
        return value

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

    def prompt_path(self, message: str = "", *, default: str = "") -> str | None:
        """Read a directory path through this shell's sole PromptSession."""
        from prompt_toolkit.completion import PathCompleter

        with self._lock:
            if self._closed:
                return None
        try:
            value = self.session.prompt(
                message,
                default=default,
                multiline=False,
                completer=PathCompleter(only_directories=True, expanduser=True),
            )
        except (EOFError, KeyboardInterrupt):
            return None
        return str(value)

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

        # PromptSession restores the buffer after nested prompts; this context manager exposes that invariant.
        yield self

    def refresh(self) -> None:
        """Request a redraw after an externally updated toolbar projection."""
        app = getattr(self.session, "app", None)
        if app is not None and getattr(app, "is_running", False):
            app.invalidate()

    def close(self) -> None:
        with self._lock:
            self._closed = True

__all__ = ["InteractiveShell", "prompt_from"]
