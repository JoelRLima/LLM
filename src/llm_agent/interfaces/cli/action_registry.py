"""Terminal handlers over the shared command metadata and parser."""

import importlib
from typing import Any

from llm_agent.interactive import commands as _core
from llm_agent.interactive.commands import (
    BUSY_POLICIES as BUSY_POLICIES,
)
from llm_agent.interactive.commands import (
    BUSY_SUBMITS as BUSY_SUBMITS,
)
from llm_agent.interactive.commands import (
    MODEL_POLICIES as MODEL_POLICIES,
)
from llm_agent.interactive.commands import (
    MUTATION_POLICIES as MUTATION_POLICIES,
)
from llm_agent.interactive.commands import (
    ROUTING_KINDS as ROUTING_KINDS,
)
from llm_agent.interactive.commands import (
    CliActionBinding as CliActionBinding,
)
from llm_agent.interactive.commands import (
    CliActionMatch as CliActionMatch,
)


class CliActionRegistry(_core.CliActionRegistry):
    def resolve_handler(self, binding: _core.CliActionBinding) -> Any:
        if binding.handler_owner is None:
            return None
        module_name, function_name = binding.handler_owner.rsplit(".", 1)
        return getattr(importlib.import_module(module_name), function_name)


DEFAULT_CLI_ACTION_REGISTRY = CliActionRegistry(
    _core.DEFAULT_ACTION_CATALOG, _core.DEFAULT_CLI_ACTION_REGISTRY._bindings
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
