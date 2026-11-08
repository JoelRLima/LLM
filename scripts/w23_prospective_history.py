"""v010-only verification of immutable W23 predecessor evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import w23_tooling_successor as legacy


def verify_archived_history(root: Path) -> dict[str, Any]:
    """Authenticate archived predecessor bytes without projecting current source onto them.

    The F01/F02 ``verify_successor`` contract remains unchanged and continues to
    validate its original current inventory. v010 calls this narrower operation
    for the immutable v001 witness and retired inputs, then authenticates the new
    source independently through its complete candidate manifest and Git tree.
    """
    historical = legacy.verify_historical_files(root)
    retired_sources = legacy.read_historical_retired_sources(root)
    retirement_inputs = legacy.read_historical_retirement_inputs(root)
    legacy._verify_git_transport(root)
    return {
        "historical_integrity": "PASS_ARCHIVED_BYTES",
        "witness": {"path": legacy.WITNESS, "sha256": legacy.WITNESS_SHA256},
        "predecessor": historical["predecessor"],
        "archived_tooling_files": len(historical["current_tooling"]),
        "retired_source_files": len(retired_sources),
        "retirement_inputs": len(retirement_inputs),
        "git_transport": "PASS_PINNED_ATTRIBUTES",
    }
