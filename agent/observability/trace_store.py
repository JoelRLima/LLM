"""Compatibility facade for the split trace-store owners.

The public imports remain stable while the implementation is divided into
path, type, writer, reader, lifecycle, runtime, and catalog modules.
"""

from agent.observability.trace_catalog import TraceCatalog
from agent.observability.trace_paths import (
    TRACE_STORE_SCHEMA_VERSION,
    TraceClosedError,
    TraceCorruptError,
    TraceStoreError,
    TraceUnavailableError,
    safe_run_key,
)
from agent.observability.trace_reader import (
    MAX_TRACE_QUERY_LIMIT,
    MAX_TRACE_READ_BYTES,
)
from agent.observability.trace_runtime import (
    DEFAULT_QUEUE_CAPACITY,
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    TraceStore,
    TraceStoreReader,
)
from agent.observability.trace_types import (
    CompletenessStatus,
    TraceCompleteness,
    TraceMetadata,
    TraceReadResult,
    TraceRetentionPolicy,
    TraceStatus,
)

__all__ = [
    "CompletenessStatus",
    "DEFAULT_QUEUE_CAPACITY",
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "MAX_TRACE_QUERY_LIMIT",
    "MAX_TRACE_READ_BYTES",
    "TRACE_STORE_SCHEMA_VERSION",
    "TraceCatalog",
    "TraceClosedError",
    "TraceCompleteness",
    "TraceCorruptError",
    "TraceMetadata",
    "TraceReadResult",
    "TraceRetentionPolicy",
    "TraceStatus",
    "TraceStore",
    "TraceStoreReader",
    "TraceStoreError",
    "TraceUnavailableError",
    "safe_run_key",
]
