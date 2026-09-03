"""Bounded, read-only observability primitives.

The observability package is deliberately separate from the semantic runtime
event model.  ``RuntimeEvent`` remains the source of semantic truth; the
types exported here describe how already-existing facts are safely observed.
"""

from agent.observability.bookmarks import Bookmark, BookmarkStore
from agent.observability.diagnostics import (
    DiagnosticCategory,
    DiagnosticRecord,
    DiagnosticSeverity,
)
from agent.observability.envelopes import (
    TRACE_SCHEMA_VERSION,
    GapMarker,
    ObservationEnvelope,
    ObservationRecord,
    ObservationSource,
)
from agent.observability.live import (
    ObservationAttachment,
    ObservationSession,
    SilenceLevel,
    SilencePolicy,
    SilenceStatus,
)
from agent.observability.liveness import (
    LivenessState,
    TraceLiveness,
    TraceLivenessPolicy,
    TraceLivenessState,
)
from agent.observability.modes import (
    OBSERVABILITY_MODES,
    ObservabilityMode,
    resolve_observability_mode,
)
from agent.observability.redaction import (
    OMITTED_VALUE,
    REDACTED_VALUE,
    REDACTION_POLICY_VERSION,
    canonical_json,
    freeze_observation_value,
    redact_observation_value,
    redact_text,
    unfreeze_observation_value,
)
from agent.observability.trace_store import (
    CompletenessStatus,
    TraceCatalog,
    TraceClosedError,
    TraceCompleteness,
    TraceCorruptError,
    TraceMetadata,
    TraceReadResult,
    TraceRetentionPolicy,
    TraceStatus,
    TraceStore,
    TraceStoreError,
    TraceStoreReader,
    TraceUnavailableError,
    safe_run_key,
)

__all__ = [
    "DiagnosticCategory",
    "DiagnosticRecord",
    "DiagnosticSeverity",
    "Bookmark",
    "BookmarkStore",
    "CompletenessStatus",
    "GapMarker",
    "OBSERVABILITY_MODES",
    "ObservationEnvelope",
    "ObservationRecord",
    "ObservationAttachment",
    "ObservationSession",
    "ObservationSource",
    "OMITTED_VALUE",
    "REDACTED_VALUE",
    "REDACTION_POLICY_VERSION",
    "TRACE_SCHEMA_VERSION",
    "TraceCatalog",
    "TraceClosedError",
    "TraceCompleteness",
    "TraceCorruptError",
    "TraceMetadata",
    "TraceReadResult",
    "TraceRetentionPolicy",
    "TraceStore",
    "TraceStoreReader",
    "TraceStoreError",
    "TraceStatus",
    "TraceUnavailableError",
    "DiagnosticExporter",
    "ExportReceipt",
    "ObservabilityMode",
    "SilenceLevel",
    "SilencePolicy",
    "SilenceStatus",
    "LivenessState",
    "TraceLiveness",
    "TraceLivenessPolicy",
    "TraceLivenessState",
    "canonical_json",
    "freeze_observation_value",
    "redact_observation_value",
    "redact_text",
    "resolve_observability_mode",
    "safe_run_key",
    "unfreeze_observation_value",
]


def __getattr__(name: str) -> object:
    """Load the export adapter only when a caller explicitly requests it."""

    if name in {"DiagnosticExporter", "ExportReceipt"}:
        from agent.observability.export import DiagnosticExporter, ExportReceipt

        return {"DiagnosticExporter": DiagnosticExporter, "ExportReceipt": ExportReceipt}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
