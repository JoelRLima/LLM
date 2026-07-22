"""Compatibility alias for :mod:`agent.interfaces.cli.ui`."""

import sys

from agent.interfaces.cli import ui as _implementation

sys.modules[__name__] = _implementation
