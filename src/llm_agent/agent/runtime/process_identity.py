"""Historical direct compatibility reexports for the Platform lock owner."""

from llm_agent.process.process_identity import (
    OwnerLiveness as OwnerLiveness,
)
from llm_agent.process.process_identity import (
    OwnerStatus as OwnerStatus,
)
from llm_agent.process.process_identity import (
    ProcessOwnerLiveness as ProcessOwnerLiveness,
)
from llm_agent.process.process_identity import (
    current_process_start_id as current_process_start_id,
)
from llm_agent.process.process_identity import (
    process_start_id as process_start_id,
)

__all__ = ['OwnerLiveness', 'OwnerStatus', 'ProcessOwnerLiveness', 'current_process_start_id', 'process_start_id']
