"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import ui_mailbox as _core
from llm_agent.interactive.ui_mailbox import (
    MailboxStats as MailboxStats,
)
from llm_agent.interactive.ui_mailbox import (
    TaskActivityUISink as TaskActivityUISink,
)
from llm_agent.interactive.ui_mailbox import (
    UIEventEnvelope as UIEventEnvelope,
)
from llm_agent.interactive.ui_mailbox import (
    UIEventMailbox as UIEventMailbox,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
