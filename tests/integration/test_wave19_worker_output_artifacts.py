from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.interfaces.cli.interactive_rendering import render_worker_message
from agent.outputs.service import OutputService
from agent.runtime.paths import AppPaths


class _Shell:
    def __init__(self) -> None:
        self.values: list[object] = []

    def print_background(self, value: object) -> None:
        self.values.append(value)


def test_settled_non_assistant_worker_output_uses_output_service(tmp_path: Path) -> None:
    service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))
    shell = _Shell()
    ctx = SimpleNamespace(
        shell=shell,
        application=SimpleNamespace(output_service=lambda: service),
        view_model=None,
    )
    result = SimpleNamespace(status="succeeded", summary="done")
    message = SimpleNamespace(
        result=SimpleNamespace(result=result, stdout="x" * 8_001, stderr="", assistant_streamed=False),
        run_generation=1,
        assistant_streamed=False,
        assistant_stream_truncated=False,
    )
    render_worker_message(ctx, message)
    artifacts = service.list()
    assert len(artifacts) == 1
    assert artifacts[0].source.value == "worker_diagnostic"
    assert artifacts[0].kind.value == "log"


def test_assistant_streamed_payload_is_not_implicitly_artifactized(tmp_path: Path) -> None:
    service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))
    ctx = SimpleNamespace(
        shell=_Shell(),
        application=SimpleNamespace(output_service=lambda: service),
        view_model=None,
    )
    result = SimpleNamespace(status="succeeded", answer="answer")
    message = SimpleNamespace(
        result=SimpleNamespace(result=result, stdout="a" * 9_000, stderr="", assistant_streamed=True),
        run_generation=1,
        assistant_streamed=True,
        assistant_stream_truncated=False,
    )
    render_worker_message(ctx, message)
    assert service.list() == ()
