"""Run the offline Workbench beta regression with workspace-local scratch dirs."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))


class WorkspaceScratch:
    @pytest.fixture
    def tmp_path(self):
        path = REPOSITORY / (".pytest-workbench-f1-" + uuid.uuid4().hex)
        path.mkdir()
        try:
            yield path
        finally:
            resolved = path.resolve()
            if resolved.parent != REPOSITORY:
                raise AssertionError("pytest scratch path escaped repository root")
            shutil.rmtree(resolved)


def main() -> int:
    os.chdir(REPOSITORY)
    return int(
        pytest.main(
            [
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:tmpdir",
                "tests/integration/test_workbench_webview_beta.py",
                "tests/integration/test_workbench_vertical_slice.py",
                "tests/unit/interfaces/test_workbench.py",
                "tests/unit/interfaces/test_shared_interactive_core.py",
                "tests/unit/interfaces/test_availability.py",
                "prototypes/workbench-comparison/test_webview_bridge.py",
                "prototypes/workbench-comparison/test_webview_endpoints.py",
            ],
            plugins=[WorkspaceScratch()],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
