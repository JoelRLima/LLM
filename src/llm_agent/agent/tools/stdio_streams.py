"""Compatibility facade for the canonical stream lifecycle implementation."""

from llm_agent.process.streams import (
    STREAM_CHUNK_BYTES,
    StreamCapture,
    close_pipes,
    drain_stream,
    send_request,
    start_readers,
)

__all__ = [
    "STREAM_CHUNK_BYTES",
    "StreamCapture",
    "drain_stream",
    "start_readers",
    "close_pipes",
    "send_request",
]
