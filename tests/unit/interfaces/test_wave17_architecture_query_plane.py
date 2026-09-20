from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from scripts import check_wave17_architecture as wave17


def _put(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(source).lstrip(), encoding="utf-8")


def _candidate(
    tmp_path: Path,
    *,
    submission_source: str | None = None,
    admission_source: str | None = None,
    extracted_owner: bool = False,
) -> Path:
    root = tmp_path / "candidate"
    _put(
        root,
        "agent/application_services/queries.py",
        """
        import subprocess

        def git_status() -> None:
            subprocess.Popen(
                [],
                shell=False,
                stdin=subprocess.DEVNULL,
                env={
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_EXTERNAL_DIFF": "",
                    "core.fsmonitor=false": "",
                },
            )
        """,
    )
    _put(root, "agent/application_services/query_git.py", "")
    _put(
        root,
        "agent/interfaces/cli/query_executor.py",
        """
        from queue import Queue

        class BoundedQueryExecutor:
            def __init__(self) -> None:
                self._channel = Queue(maxsize=1)
                self._result_pending = False
        """,
    )
    _put(
        root,
        "agent/interfaces/cli/query_rendering.py",
        submission_source
        or """
        def submit_query(text, ctx, command_id):
            executor = ctx.query_executor
            service = ctx.query_service
            return executor.submit(text, command_id, execute=service.execute)
        """,
    )
    if extracted_owner:
        rendering_source = ""
        admission_default = """
        from agent.interfaces.cli import query_rendering

        def _handle_controller_input(text, ctx, controller):
            return query_rendering.submit_query(text, ctx, "query.read")

        def handle_input(text, ctx):
            return _handle_controller_input(text, ctx, None)
        """
    else:
        rendering_source = "from agent.interfaces.cli.query_rendering import submit_query\n"
        admission_default = """
        from agent.interfaces.cli import interactive_rendering

        def _handle_controller_input(text, ctx, controller):
            return interactive_rendering.submit_query(text, ctx, "query.read")

        def handle_input(text, ctx):
            return _handle_controller_input(text, ctx, None)
        """
    _put(root, "agent/interfaces/cli/interactive_rendering.py", rendering_source)
    _put(root, "agent/interfaces/cli/interactive_admission.py", admission_source or admission_default)
    return root


def _w17_arch17(root: Path) -> list[wave17.ArchitectureViolation]:
    return [finding for finding in wave17._check_query_plane(root) if finding.rule_id == "W17-ARCH-17"]


def test_w19_query_submission_candidate_passes_w17_arch17() -> None:
    assert _w17_arch17(wave17.ROOT) == []


def test_direct_service_bypass_of_bounded_executor_fails(tmp_path: Path) -> None:
    root = _candidate(
        tmp_path,
        submission_source="""
        def submit_query(text, ctx, command_id):
            return ctx.query_service.execute(text, None)
        """,
    )

    findings = _w17_arch17(root)

    assert any(finding.path.endswith("query_rendering.py") for finding in findings)


def test_query_execution_in_prompt_path_fails(tmp_path: Path) -> None:
    root = _candidate(
        tmp_path,
        admission_source="""
        from agent.interfaces.cli import interactive_rendering

        def _handle_controller_input(text, ctx, controller):
            ctx.query_service.execute(text, None)
            return interactive_rendering.submit_query(text, ctx, "query.read")

        def handle_input(text, ctx):
            return _handle_controller_input(text, ctx, None)
        """,
    )

    findings = _w17_arch17(root)

    assert any(finding.path.endswith("interactive_admission.py") for finding in findings)


def test_legitimately_extracted_query_owner_remains_accepted(tmp_path: Path) -> None:
    root = _candidate(tmp_path, extracted_owner=True)

    assert _w17_arch17(root) == []
