"""Closed persona identity to prompt mapping."""

from __future__ import annotations

from typing import cast

from agent.llm.prompts import CODER_PROMPT, GENERAL_PROMPT, RESEARCHER_PROMPT, SECURITY_AUDITOR_PROMPT
from agent.routing.persona.contracts import PersonaId

_PROMPTS = {
    PersonaId.CODER: CODER_PROMPT,
    PersonaId.RESEARCHER: RESEARCHER_PROMPT,
    PersonaId.GENERAL: GENERAL_PROMPT,
    PersonaId.SECURITY_AUDITOR: SECURITY_AUDITOR_PROMPT,
}


def persona_prompt_for(persona: PersonaId) -> str:
    if not isinstance(persona, PersonaId):
        raise TypeError("persona must be PersonaId")
    return cast(str, _PROMPTS[persona])


def coerce_persona_id(value: object) -> PersonaId | None:
    if isinstance(value, PersonaId):
        return value
    if not isinstance(value, str):
        return None
    try:
        return PersonaId(value)
    except ValueError:
        return None


__all__ = ["coerce_persona_id", "persona_prompt_for"]
