from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.interfaces.cli.output_viewer import render_output_viewer
from agent.outputs.models import OutputContentPolicy, OutputKind, OutputPublishRequest, OutputSource
from agent.outputs.service import OutputService
from agent.runtime.paths import AppPaths


class _Shell:
    def __init__(self) -> None:
        self.values: list[object] = []

    def print_background(self, value: object) -> None:
        self.values.append(value)


def _context(tmp_path: Path) -> tuple[SimpleNamespace, OutputService, _Shell]:
    service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))
    shell = _Shell()
    ctx = SimpleNamespace(
        shell=shell,
        application=SimpleNamespace(output_service=lambda: service),
    )
    return ctx, service, shell


def test_valid_output_viewer_reads_service_and_presents_literal_payload(tmp_path: Path) -> None:
    ctx, service, shell = _context(tmp_path)
    publication = service.publish(
        OutputPublishRequest(
            kind=OutputKind.TEXT,
            source=OutputSource.OTHER_PUBLIC,
            title="literal",
            text="[bold red]literal[/bold red]",
            content_policy=OutputContentPolicy.PUBLIC_TEXT,
            force_artifact=True,
        )
    )
    assert publication.artifact is not None
    assert render_output_viewer(f"/inspect output {publication.artifact.output_id}", ctx)
    assert "[bold red]literal[/bold red]" in shell.values
    assert all("<rich" not in str(value) for value in shell.values)


def test_invalid_output_syntax_does_not_call_service(tmp_path: Path) -> None:
    called = False

    def fail() -> object:
        nonlocal called
        called = True
        raise AssertionError("invalid viewer syntax touched the service")

    ctx = SimpleNamespace(shell=_Shell(), application=SimpleNamespace(output_service=fail))
    assert render_output_viewer("/inspect output ../../secret", ctx)
    assert called is False
    assert render_output_viewer("/inspect", ctx) is False
