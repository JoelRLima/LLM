"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import worker_stream as _core
from llm_agent.interactive.worker_stream import (
    MAX_WORKER_STREAM_CHARS as MAX_WORKER_STREAM_CHARS,
)
from llm_agent.interactive.worker_stream import (
    MAX_WORKER_STREAM_CHUNK_CHARS as MAX_WORKER_STREAM_CHUNK_CHARS,
)
from llm_agent.interactive.worker_stream import (
    MAX_WORKER_STREAM_ITEMS as MAX_WORKER_STREAM_ITEMS,
)
from llm_agent.interactive.worker_stream import (
    BoundedTextBuffer as BoundedTextBuffer,
)
from llm_agent.interactive.worker_stream import (
    BoundedWorkerStream as BoundedWorkerStream,
)
from llm_agent.interactive.worker_stream import (
    WorkerStreamChunk as WorkerStreamChunk,
)
from llm_agent.interactive.worker_stream import (
    WorkerStreamKind as WorkerStreamKind,
)
from llm_agent.interactive.worker_stream import (
    WorkerStreamStatus as WorkerStreamStatus,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
