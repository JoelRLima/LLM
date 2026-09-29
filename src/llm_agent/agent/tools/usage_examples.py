"""Canonical, bounded synthetic usage examples for tool descriptors.

Examples are descriptor metadata, not observations.  They are validated at
the same planning boundary as the descriptor schema and frozen with the
other canonical metadata so they cannot become a mutable side channel.
"""

from __future__ import annotations

from llm_agent.agent.operation.usage_examples import (
    MAX_USAGE_EXAMPLE_CHARS,
    MAX_USAGE_EXAMPLE_PURPOSE_CHARS,
    MAX_USAGE_EXAMPLES,
    normalize_usage_examples,
)

__all__ = [
    "MAX_USAGE_EXAMPLES",
    "MAX_USAGE_EXAMPLE_CHARS",
    "MAX_USAGE_EXAMPLE_PURPOSE_CHARS",
    "normalize_usage_examples",
]
