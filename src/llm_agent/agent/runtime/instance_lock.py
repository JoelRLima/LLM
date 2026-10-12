"""Historical direct compatibility reexports for the Platform lock owner."""

from llm_agent.process.instance_lock import (
    InstanceLock as InstanceLock,
)
from llm_agent.process.instance_lock import (
    InstanceLockError as InstanceLockError,
)

__all__ = ['InstanceLock', 'InstanceLockError']
