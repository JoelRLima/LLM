"""Compatibility alias for :mod:`agent.interfaces.cli.streaming`."""

import sys

from agent.interfaces.cli import streaming as _implementation

sys.modules[__name__] = _implementation
