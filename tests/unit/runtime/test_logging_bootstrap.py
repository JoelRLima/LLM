import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.runtime.logging import (
    LoggingConfigurationError,
    logger,
    setup_logger,
    teardown_logger,
)

PROJECT_ROOT = Path(__file__).parents[3]


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{current}" if current else str(PROJECT_ROOT)
    )
    return environment


def test_importing_logging_does_not_create_runtime_files(tmp_path: Path) -> None:
    script = "import agent.runtime.logging; print('ok')"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_subprocess_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert list(tmp_path.iterdir()) == []


def test_explicit_logging_uses_requested_file_and_stderr(tmp_path: Path, capsys) -> None:
    log_file = tmp_path / "logs" / "agent.log"
    try:
        setup_logger(log_file=log_file)
        logger.warning("visible")
        for handler in logger.handlers:
            handler.flush()

        assert "visible" in log_file.read_text(encoding="utf-8")
        assert "visible" in capsys.readouterr().err
    finally:
        teardown_logger()


def test_importing_cli_in_clean_directory_has_no_side_effects(tmp_path: Path) -> None:
    script = (
        "import json, pathlib; "
        "import agent.interfaces.cli; "
        "print(json.dumps([p.name for p in pathlib.Path('.').iterdir()]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_subprocess_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_same_logging_configuration_is_reference_counted(tmp_path: Path) -> None:
    log_file = tmp_path / "agent.log"
    try:
        setup_logger(log_file=log_file, console=False)
        setup_logger(log_file=log_file, console=False)
        teardown_logger()
        logger.warning("second owner remains")
        for handler in logger.handlers:
            handler.flush()

        assert "second owner remains" in log_file.read_text(encoding="utf-8")
    finally:
        teardown_logger()


def test_live_logging_rejects_a_different_destination(tmp_path: Path) -> None:
    first = tmp_path / "first.log"
    try:
        setup_logger(log_file=first, console=False)

        with pytest.raises(LoggingConfigurationError, match="incompatível"):
            setup_logger(log_file=tmp_path / "second.log", console=False)

        logger.warning("first owner remains")
        for handler in logger.handlers:
            handler.flush()
        assert "first owner remains" in first.read_text(encoding="utf-8")
    finally:
        teardown_logger()
