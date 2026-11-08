"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import attention as _core
from llm_agent.interactive.attention import (
    MAX_REVIEW_DIFF_CHARS as MAX_REVIEW_DIFF_CHARS,
)
from llm_agent.interactive.attention import (
    ApprovalBroker as ApprovalBroker,
)
from llm_agent.interactive.attention import (
    AttentionIdentity as AttentionIdentity,
)
from llm_agent.interactive.attention import (
    AttentionSnapshot as AttentionSnapshot,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
