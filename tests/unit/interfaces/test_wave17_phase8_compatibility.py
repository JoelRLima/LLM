from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

from agent.interfaces.cli import app, ui


def test_headless_json_path_does_not_import_prompt_toolkit(monkeypatch, tmp_path: Path, capsys) -> None:
    original_import = builtins.__import__

    def no_prompt_toolkit(name, *args, **kwargs):
        if name == "prompt_toolkit" or name.startswith("prompt_toolkit."):
            raise AssertionError("headless path imported prompt_toolkit")
        return original_import(name, *args, **kwargs)

    class Result:
        answer = "ok"
        error = None
        receipt = {}
        report_path = None
        status = "succeeded"
        success = True

        def to_dict(self):
            return {"answer": self.answer, "error": self.error, "receipt": self.receipt, "report_path": self.report_path, "status": self.status, "success": self.success}

    class Application:
        def interact(self, *_args, **_kwargs):
            return Result()

        def close(self):
            return None

    monkeypatch.setattr(builtins, "__import__", no_prompt_toolkit)
    monkeypatch.setattr(app, "_create_application", lambda *_args, **_kwargs: Application())

    assert app.main(["run", "--json", "--workspace", str(tmp_path), "hello"]) == 0
    output = capsys.readouterr()
    assert "\x1b" not in output.out
    assert output.err == ""


def test_dynamic_code_result_text_is_literal_without_rich_markup(monkeypatch) -> None:
    rendered: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(ui, "console", SimpleNamespace(print=lambda value, **kwargs: rendered.append((value, kwargs))))
    result = SimpleNamespace(
        status=SimpleNamespace(value="failed"),
        summary="[model] user text [/model]",
        error=None,
        artifacts=(),
        diagnostics=({"code": "X", "message": "[red]literal[/red]"},),
    )

    ui.render_code_result(result)

    assert "[model] user text [/model]" in str(rendered[0][0])
    assert rendered[0][1]["markup"] is False
    assert "[red]literal[/red]" in str(rendered[1][0])
    assert rendered[1][1]["markup"] is False
