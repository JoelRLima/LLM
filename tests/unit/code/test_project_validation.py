import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.cancellation import CancellationToken
from agent.code.discovery import ProjectDiscovery
from agent.code.validation import (
    CommandSpec,
    ProcessRunner,
    ProjectValidator,
    ValidationProfile,
    ValidationStatus,
)


def test_python_validation_passes_and_fails_with_structured_status(tmp_path: Path):
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    profile = ProjectDiscovery(tmp_path).discover()
    validator = ProjectValidator(tmp_path)

    passed = validator.validate(profile, ["module.py"])
    source.write_text("def broken(:\n", encoding="utf-8")
    failed = validator.validate(profile, ["module.py"])

    assert passed.status == ValidationStatus.PASSED
    assert failed.status == ValidationStatus.FAILED
    assert failed.diagnostics[0].source == "python-syntax"


def test_missing_command_is_unavailable_not_success(tmp_path: Path):
    profile = ProjectDiscovery(tmp_path).discover()
    report = ProjectValidator(tmp_path).validate(
        profile,
        [],
        profile=ValidationProfile(
            (CommandSpec("missing", ("agent-command-that-does-not-exist",)),)
        ),
    )

    assert report.status == ValidationStatus.UNAVAILABLE
    assert report.passed is False


def test_timeout_terminates_process(tmp_path: Path):
    result = ProcessRunner(tmp_path).run(
        CommandSpec(
            "timeout",
            (sys.executable, "-c", "import time; time.sleep(10)"),
            timeout_seconds=0.1,
        )
    )

    assert result.status == ValidationStatus.TIMED_OUT
    assert result.duration_seconds < 3


def test_cancellation_stops_in_flight_process(tmp_path: Path):
    token = CancellationToken()
    runner = ProcessRunner(tmp_path, cancellation=token)
    command = CommandSpec(
        "cancel",
        (sys.executable, "-c", "import time; time.sleep(10)"),
        timeout_seconds=20,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, command)
        token.cancel()
        result = future.result(timeout=3)

    assert result.status == ValidationStatus.CANCELLED


def test_validation_ignores_deleted_source_after_python_move(tmp_path: Path):
    destination = tmp_path / "pkg" / "new.py"
    destination.parent.mkdir()
    destination.write_text("value = 1\n", encoding="utf-8")
    profile = ProjectDiscovery(tmp_path).discover()

    report = ProjectValidator(tmp_path).validate(
        profile,
        ["old.py", "pkg/new.py"],
    )

    assert report.status == ValidationStatus.PASSED
