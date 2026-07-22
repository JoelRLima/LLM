"""Compatibility alias for :mod:`agent.interfaces.cli.commands`."""

import sys

from agent.interfaces.cli import commands as _implementation

sys.modules[__name__] = _implementation
