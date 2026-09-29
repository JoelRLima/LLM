"""Provisional product package root.

The root deliberately exposes only product identity.  Importing it must not
construct or import the Agent subsystem so platform/product probes can run in
an Agent-free process.
"""

from ._version import VERSION

__version__ = VERSION

__all__ = ["VERSION", "__version__"]
