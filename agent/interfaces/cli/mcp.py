"""Base-safe CLI admission for the optional MCP extra."""

from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace

MCP_EXTRA_REQUIRED = "ENGINEERING_MCP_EXTRA_REQUIRED"


def run_mcp_engineering(args: Namespace) -> int:
    if importlib.util.find_spec("mcp") is None:
        print(
            f"{MCP_EXTRA_REQUIRED}: install the optional MCP extra with `pip install local-llm-agent[mcp]`.",
            file=sys.stderr,
        )
        return 2
    from agent.interfaces.mcp.engineering_server import serve_engineering_stdio

    return serve_engineering_stdio(args.workspace, home=getattr(args, "home", None))


__all__ = ["MCP_EXTRA_REQUIRED", "run_mcp_engineering"]
