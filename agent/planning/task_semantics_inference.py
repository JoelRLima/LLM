"""Objective-only effect and obligation inference for task semantics."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

from agent.planning.task_semantics_types import (
    EffectSemantics,
    TaskObligation,
    _normalize_text,
)

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)
_NEGATION_WORDS = frozenset({"nao", "sem", "never", "nunca", "without"})
_DIRECT_TEXT_WORDS = frozenset({"exatamente", "exactly"})
_EFFECT_TERMS = {
    "adicione": "write", "adicionar": "write", "ajuste": "write", "ajustar": "write",
    "alterar": "write", "altere": "write", "change": "write", "corrija": "write",
    "corrigir": "write", "create": "write", "crie": "write", "criar": "write",
    "delete": "write", "edit": "write", "edite": "write", "editar": "write",
    "escreva": "write", "escrever": "write", "fix": "write", "modifique": "write",
    "modificar": "write", "modify": "write", "remova": "write", "remover": "write",
    "refactor": "write", "replace": "write", "substitua": "write", "substituir": "write",
    "update": "write", "write": "write",
}
_OBLIGATION_TERMS = {
    "leia": "read", "ler": "read", "read": "read", "inspect": "read",
    "inspecione": "read", "examinar": "read", "examine": "read", "consulte": "read",
    "consultar": "read", "procure": "search", "procurar": "search", "busque": "search",
    "buscar": "search", "pesquise": "search", "pesquisar": "search", "search": "search",
    "find": "search", "encontre": "search", "encontrar": "search", "compare": "compare",
    "comparar": "compare", "diff": "compare", "analise": "analyze", "analisar": "analyze",
    "analyze": "analyze",
}


def _tokens(objective: str) -> tuple[str, ...]:
    normalized = _normalize_text(objective).replace("â€™", "'")
    normalized = normalized.replace("don't", "do not").replace("dont", "do not")
    return tuple(_WORD_RE.findall(normalized))


def _is_negated(tokens: Sequence[str], index: int) -> bool:
    return (index > 0 and tokens[index - 1] in {"nao", "sem", "never", "nunca", "without"}) or (
        index > 1 and tokens[index - 2 : index] == ("do", "not")
    )


def _is_direct_text_request(tokens: Sequence[str], index: int) -> bool:
    return index + 1 < len(tokens) and tokens[index + 1] in _DIRECT_TEXT_WORDS


def infer_effect_semantics(objective: str) -> EffectSemantics:
    """Infer requested and prohibited effects from user intent only."""

    if not isinstance(objective, str):
        from agent.planning.task_semantics_types import TaskSemanticsError

        raise TaskSemanticsError("objetivo deve ser textual")
    tokens = _tokens(objective)
    requested: list[str] = []
    prohibited: list[str] = []
    for index, token in enumerate(tokens):
        effect = _EFFECT_TERMS.get(token)
        if effect is None or _is_direct_text_request(tokens, index):
            continue
        target = prohibited if _is_negated(tokens, index) else requested
        if effect not in target:
            target.append(effect)
    return EffectSemantics(tuple(requested), tuple(prohibited))


def infer_requested_effects(objective: str) -> tuple[str, ...]:
    return infer_effect_semantics(objective).requested


def infer_prohibited_effects(objective: str) -> tuple[str, ...]:
    return infer_effect_semantics(objective).prohibited


def inferred_obligations(objective: str, effects: EffectSemantics) -> list[TaskObligation]:
    kinds: list[str] = []
    for index, token in enumerate(_tokens(objective)):
        kind = _OBLIGATION_TERMS.get(token)
        if kind == "search" and not _is_negated(_tokens(objective), index) and kind not in kinds:
            kinds.append(kind)
    obligations = [
        TaskObligation(
            id=f"requirement:{kind}",
            kind=kind,
            description=f"Cumprir o requisito de {kind} do objetivo original.",
        )
        for kind in kinds
    ]
    obligations.extend(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in effects.requested
    )
    return obligations


def stable_obligation_id(kind: str, description: str, effect: str | None = None) -> str:
    material = f"{kind}|{effect or ''}|{_normalize_text(description)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"requirement:{kind}:{digest}"


__all__ = (
    "infer_effect_semantics",
    "infer_prohibited_effects",
    "infer_requested_effects",
    "inferred_obligations",
)
