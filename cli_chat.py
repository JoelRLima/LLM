"""Compatibility alias for :mod:`agent.interfaces.cli.chat`."""

import sys

from agent.interfaces.cli import chat as _implementation

sys.modules[__name__] = _implementation
