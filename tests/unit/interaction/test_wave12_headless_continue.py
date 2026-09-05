from __future__ import annotations

from agent.interfaces.cli import app as cli


def test_exact_headless_continue_is_preflighted_by_existing_w10_adapter(monkeypatch) -> None:
    from agent.interfaces.cli import task_continuity

    seen = []
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("bootstrap")))
    monkeypatch.setattr(task_continuity, "run_task_resume", lambda args, **kwargs: seen.append(args) or 2)
    assert cli.main(["run", "/continue"]) == 2
    assert seen
