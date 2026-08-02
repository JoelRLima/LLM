import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from agent.cancellation import CancellationToken
from agent.code.discovery import ProjectDiscovery
from agent.code.validation import (
    CommandSpec,
    ProcessRunner,
    ProjectValidator,
    ValidationProfile,
    ValidationStatus,
)


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlinks indisponíveis neste ambiente: {exc}")


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


def test_validator_rejects_external_changed_path_before_starting_process(
    tmp_path: Path,
) -> None:
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-validator-sentinel.py"
    outside.write_text("SENTINEL = True\n", encoding="utf-8")
    profile = ProjectDiscovery(tmp_path).discover()

    report = ProjectValidator(tmp_path).validate(profile, [str(outside)])

    assert report.status == ValidationStatus.FAILED
    assert report.checks == ()
    assert report.diagnostics[0].code == "VALIDATION_PATH_ESCAPE"


def test_validator_rejects_external_test_root_from_untrusted_profile(
    tmp_path: Path,
) -> None:
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside_tests = tmp_path.parent / f"{tmp_path.name}-validator-tests"
    outside_tests.mkdir()
    (outside_tests / "test_outside.py").write_text(
        "def test_outside():\n    assert True\n",
        encoding="utf-8",
    )
    profile = replace(
        ProjectDiscovery(tmp_path).discover(),
        test_roots=(str(outside_tests),),
    )

    report = ProjectValidator(tmp_path).validate(
        profile,
        [],
        include_tests=True,
    )

    assert report.status == ValidationStatus.FAILED
    assert report.checks == ()
    assert report.diagnostics[0].code == "VALIDATION_PATH_ESCAPE"


def test_pytest_validation_ignores_project_addopts_that_escape_workspace(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_safe.py").write_text(
        "def test_safe():\n    assert True\n",
        encoding="utf-8",
    )
    marker = tmp_path.parent / f"{tmp_path.name}-pytest-marker"
    outside_test = tmp_path.parent / f"{tmp_path.name}-outside-test.py"
    outside_test.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('escaped', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text(
        f"[pytest]\naddopts = ../{outside_test.name}\n",
        encoding="utf-8",
    )
    profile = ProjectDiscovery(tmp_path).discover()

    report = ProjectValidator(tmp_path).validate(
        profile,
        [],
        include_tests=True,
    )

    assert report.status == ValidationStatus.PASSED
    assert not marker.exists()
    assert not (tmp_path / ".pytest_cache").exists()


def test_pytest_validation_rejects_external_symlink_before_loading_conftest(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_safe.py").write_text(
        "def test_safe():\n    assert True\n",
        encoding="utf-8",
    )
    marker = tmp_path.parent / f"{tmp_path.name}-conftest-marker"
    outside_conftest = tmp_path.parent / f"{tmp_path.name}-conftest.py"
    outside_conftest.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('escaped', encoding='utf-8')\n",
        encoding="utf-8",
    )
    _symlink_or_skip(tests / "conftest.py", outside_conftest)
    profile = ProjectDiscovery(tmp_path).discover()

    report = ProjectValidator(tmp_path).validate(
        profile,
        [],
        include_tests=True,
    )

    assert report.status == ValidationStatus.FAILED
    assert report.checks == ()
    assert report.diagnostics[0].code == "VALIDATION_PATH_ESCAPE"
    assert not marker.exists()


def test_validation_prefixes_dash_filename_and_leaves_no_bytecode_cache(
    tmp_path: Path,
) -> None:
    (tmp_path / "--help.py").write_text("VALUE = 1\n", encoding="utf-8")
    profile = ProjectDiscovery(tmp_path).discover()

    report = ProjectValidator(tmp_path).validate(profile, ["--help.py"])

    assert report.status == ValidationStatus.PASSED
    assert not (tmp_path / "__pycache__").exists()


def test_process_runner_rejects_marked_external_argument_and_cwd(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-runner"
    outside.mkdir()
    marker = outside / "marker"
    script = outside / "write_marker.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('escaped', encoding='utf-8')\n",
        encoding="utf-8",
    )
    runner = ProcessRunner(tmp_path)

    argument_result = runner.run(
        CommandSpec(
            "external-argument",
            (sys.executable, str(script)),
            workspace_arg_indices=(1,),
        )
    )
    cwd_result = runner.run(
        CommandSpec(
            "external-cwd",
            (
                sys.executable,
                "-c",
                "from pathlib import Path; Path('marker').write_text('escaped')",
            ),
            cwd=str(outside),
        )
    )

    assert argument_result.status == ValidationStatus.FAILED
    assert cwd_result.status == ValidationStatus.FAILED
    assert not marker.exists()


def test_process_runner_sanitizes_python_and_pytest_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(tmp_path.parent))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path.parent))
    monkeypatch.setenv("PYTEST_ADDOPTS", "../outside")
    monkeypatch.setenv("PYTEST_PLUGINS", "outside_plugin")
    code = (
        "import os;"
        "print('|'.join(str(os.environ.get(name)) for name in "
        "('PYTHONPATH','PYTHONHOME','PYTEST_ADDOPTS','PYTEST_PLUGINS')));"
        "print(os.environ['PYTHONDONTWRITEBYTECODE']);"
        "print(os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'])"
    )

    result = ProcessRunner(tmp_path).run(
        CommandSpec("environment", (sys.executable, "-c", code))
    )

    assert result.status == ValidationStatus.PASSED
    assert result.stdout.splitlines() == [
        "None|None|None|None",
        "1",
        "1",
    ]
