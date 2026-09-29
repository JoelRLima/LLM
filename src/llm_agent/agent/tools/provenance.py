"""Small descriptor-owned argument provenance contracts.

The planner may synthesize ordinary arguments, but selected arguments can
declare a narrower set of mechanically checkable origins.  This module is a
value contract only; validation remains at the planning boundary.
"""

from __future__ import annotations

from llm_agent.agent.operation.provenance import ArgumentOrigin, normalize_argument_provenance

__all__ = ["ArgumentOrigin", "normalize_argument_provenance"]
