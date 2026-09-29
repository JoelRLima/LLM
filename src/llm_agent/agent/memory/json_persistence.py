"""Direct compatibility facade for generic JSON persistence."""

from llm_agent.storage.json_persistence import (
    AtomicJsonWriteError,
    AtomicWriteError,
    JsonObjectReadError,
    read_json_object,
    write_json_atomic,
    write_text_atomic,
)

__all__ = [
    "AtomicJsonWriteError",
    "AtomicWriteError",
    "JsonObjectReadError",
    "read_json_object",
    "write_json_atomic",
    "write_text_atomic",
]
