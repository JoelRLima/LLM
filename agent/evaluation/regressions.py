"""Curated permanent reproducers for the Marco 3 Regression Set.

The cases intentionally point to focused pytest nodes rather than wrapping the
test runner in a second execution framework.  Capability evals use the real
AgentApplication adapter; regression reproducers remain focused unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    category: str
    pytest_node: str
    reason: str


CURATED_REGRESSION_SET: tuple[RegressionCase, ...] = (
    RegressionCase(
        "authority-gateway-denial",
        "R-AUTHORITY",
        "tests/unit/tools/test_invocation_gateway.py::test_gateway_permission_denied",
        "capability denial must happen before an unauthorized effect",
    ),
    RegressionCase(
        "terminal-late-completion",
        "R-TERMINALITY",
        "tests/unit/tools/test_invocation_gateway.py::test_gateway_timeout_has_one_terminal_publication_and_discards_late_completion",
        "timeout has exactly one terminal publication",
    ),
    RegressionCase(
        "stdio-detached-descendant",
        "R-PROCESS",
        "tests/unit/tools/test_stdio_adapter.py::test_stdio_adapter_success_terminates_detached_descendant",
        "successful stdio invocation owns its process tree",
    ),
    RegressionCase(
        "writer-validation-bypass",
        "R-WRITER",
        "tests/integration/test_standalone_application.py::test_model_planned_file_writer_is_excluded_with_auto_approval_and_no_mutation",
        "file_writer is excluded from the model view even with approval available, without mutation",
    ),
    RegressionCase(
        "git-remerge-denial",
        "R-SHELL-GIT",
        "tests/unit/skills/test_git_workspace.py::test_git_log_remerge_driver_is_rejected_before_execution",
        "workspace merge-driver execution is rejected before Git runs",
    ),
    RegressionCase(
        "stdio-protocol-identity",
        "R-STDIO",
        "tests/unit/tools/test_stdio_adapter.py::test_stdio_adapter_rejects_unknown_invocation_id",
        "external protocol identity is enforced",
    ),
    RegressionCase(
        "installed-slice-contract",
        "R-INSTALLED",
        "tests/policy/test_installed_package_gate.py::test_installed_probe_covers_external_stdio_slice_d",
        "installed A-D probe remains structurally present",
    ),
    RegressionCase(
        "measurement-projection",
        "R-MEASUREMENT",
        "tests/unit/runtime/test_reporting_paths.py::test_task_report_projects_invocation_and_output_bounds",
        "invocation and output-bound measurement remains reportable",
    ),
)


__all__ = ["CURATED_REGRESSION_SET", "RegressionCase"]
