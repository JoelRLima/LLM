"""Compatibility alias for :mod:`agent.interfaces.cli.command_handlers`."""

import sys

from agent.interfaces.cli import command_handlers as _implementation

sys.modules[__name__] = _implementation
