"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import controller_models as _core
from llm_agent.interactive.controller_models import (
    MAX_INPUT_CHARS as MAX_INPUT_CHARS,
)
from llm_agent.interactive.controller_models import (
    MAX_PENDING_CHARS as MAX_PENDING_CHARS,
)
from llm_agent.interactive.controller_models import (
    MAX_PENDING_ITEMS as MAX_PENDING_ITEMS,
)
from llm_agent.interactive.controller_models import (
    PendingCapacityError as PendingCapacityError,
)
from llm_agent.interactive.controller_models import (
    PendingItem as PendingItem,
)
from llm_agent.interactive.controller_models import (
    PendingStore as PendingStore,
)
from llm_agent.interactive.controller_models import (
    SubmissionEnvelope as SubmissionEnvelope,
)
from llm_agent.interactive.controller_models import (
    SubmissionOutcome as SubmissionOutcome,
)
from llm_agent.interactive.controller_models import (
    WorkerMessage as WorkerMessage,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
