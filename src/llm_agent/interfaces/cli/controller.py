"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import controller as _core
from llm_agent.interactive.controller import (
    MAX_INPUT_CHARS as MAX_INPUT_CHARS,
)
from llm_agent.interactive.controller import (
    MAX_PENDING_CHARS as MAX_PENDING_CHARS,
)
from llm_agent.interactive.controller import (
    MAX_PENDING_ITEMS as MAX_PENDING_ITEMS,
)
from llm_agent.interactive.controller import (
    ControllerState as ControllerState,
)
from llm_agent.interactive.controller import (
    InteractiveExecutionController as InteractiveExecutionController,
)
from llm_agent.interactive.controller import (
    PendingCapacityError as PendingCapacityError,
)
from llm_agent.interactive.controller import (
    PendingItem as PendingItem,
)
from llm_agent.interactive.controller import (
    PendingStore as PendingStore,
)
from llm_agent.interactive.controller import (
    SubmissionEnvelope as SubmissionEnvelope,
)
from llm_agent.interactive.controller import (
    SubmissionOutcome as SubmissionOutcome,
)
from llm_agent.interactive.controller import (
    WorkerMessage as WorkerMessage,
)
from llm_agent.interactive.controller import (
    WorkerSettlementTimeout as WorkerSettlementTimeout,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
