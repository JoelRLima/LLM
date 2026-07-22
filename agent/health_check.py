"""Public health-check facade and command-line entry point."""

from __future__ import annotations

import sys
from typing import Any, Dict

from agent.health.core import (
    CheckResult,
)
from agent.health.reporting import run_checks
from agent.health.runtime_checks import (
    check_logs,
    check_orphan_dirs,
    check_permissions,
    check_skills,
)
from agent.health.state_checks import (
    check_config,
    check_file_hashes,
    check_memory,
    check_python_version,
)

__all__ = [
    "CheckResult", "check_config", "check_file_hashes", "check_logs", "check_memory",
    "check_orphan_dirs", "check_permissions", "check_python_version", "check_skills",
    "run_health_check",
]


def run_health_check(write_report: bool = True, verbose: bool = True) -> Dict[str, Any]:
    checks = [
        ("python_version", check_python_version), ("config", check_config),
        ("memory", check_memory), ("file_hashes", check_file_hashes),
        ("orphan_dirs", check_orphan_dirs), ("permissions", check_permissions),
        ("skills", check_skills), ("logs", check_logs),
    ]
    return run_checks(checks, write_report=write_report, verbose=verbose)


def main() -> int:
    report = run_health_check(write_report=True, verbose=True)
    return 0 if report["errors"] == 0 and report["warnings"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
