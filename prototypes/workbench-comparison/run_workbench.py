# ruff: noqa: E402, I001
"""Source-checkout convenience launcher for the packaged Workbench UI."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from llm_agent.interfaces.workbench.webview import main  # noqa: E402


if __name__ == "__main__":
    main(sys.argv[1:])
