"""Historical direct compatibility reexports for the Platform lock owner."""

from llm_agent.process.lock_record import (
    InvalidLockRecord as InvalidLockRecord,
)
from llm_agent.process.lock_record import (
    LockRecord as LockRecord,
)
from llm_agent.process.lock_record import (
    read_lock_record as read_lock_record,
)

__all__ = ['InvalidLockRecord', 'LockRecord', 'read_lock_record']
