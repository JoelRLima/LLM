"""Exercise the history UI state machine with a minimal DOM and Node builtins."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("harness", ["workbench_history_ui.cjs", "workbench_commands_ui.cjs", "workbench_panels_ui.cjs", "workbench_buttons_ui.cjs"])
def test_history_ui_pagination_and_refresh_races(harness: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node executable unavailable; the same harness can run in the Node REPL")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [node, str(root / "tests/support" / harness),
         str(root / "src/llm_agent/interfaces/workbench/static/workbench.js"),
         str(root / "src/llm_agent/interfaces/workbench/static/workbench-controls.js"),
         str(root / "src/llm_agent/interfaces/workbench/static/index.html")],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        cwd=root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
