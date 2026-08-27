"""Compatibility imports for the runtime-owned terminal outcome projector.

The reporting package may render this value, but it does not define execution
status or mutation truth.
"""

from agent.runtime.operational_outcome import (
    OperationalOutcome,
    artifact_metadata,
    has_canonical_commit_incident,
    local_failure_permitted,
    metadata_is_persisted_mutation,
    normalize_terminal_status,
    project_operational_outcome,
)
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES, PUBLIC_TERMINAL_STATUSES

__all__ = [
    "NON_SUCCESS_STATUSES",
    "PUBLIC_TERMINAL_STATUSES",
    "OperationalOutcome",
    "artifact_metadata",
    "has_canonical_commit_incident",
    "local_failure_permitted",
    "metadata_is_persisted_mutation",
    "normalize_terminal_status",
    "project_operational_outcome",
]
