"""Historical direct compatibility reexports for the Platform lock owner."""

from llm_agent.process.file_lock import (
    OPEN_BINARY as OPEN_BINARY,
)
from llm_agent.process.file_lock import (
    lock_descriptor as lock_descriptor,
)
from llm_agent.process.file_lock import (
    read_descriptor as read_descriptor,
)
from llm_agent.process.file_lock import (
    try_lock_descriptor as try_lock_descriptor,
)
from llm_agent.process.file_lock import (
    unlock_descriptor as unlock_descriptor,
)

__all__ = ['OPEN_BINARY', 'lock_descriptor', 'read_descriptor', 'try_lock_descriptor', 'unlock_descriptor']
