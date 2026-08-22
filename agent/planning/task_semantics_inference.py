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
_FILE_RE = re.compile(r"(?<!\w)([\w./\\-]+\.[A-Za-z0-9]{1,16})(?!\w)", re.UNICODE)
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
_READ_VERBS = frozenset(
    {"leia", "ler", "read", "inspect", "inspecione", "examinar", "examine", "consulte", "consultar"}
)
_SEARCH_FILLER = frozenset(
    {
        "o", "a", "os", "as", "um", "uma", "nos", "nas", "no", "na", "em", "de", "do", "da", "dos", "das",
        "outros", "outras", "arquivos", "arquivo", "workspace", "workspaces", "esse", "essa", "este", "esta", "pela", "pelo", "por", "para", "que", "ele", "ela",
        "eles", "elas", "palavra", "texto", "ocorrencia", "correspondente", "contem", "contém",
        "observado", "observada", "observados", "observadas", "observacao", "observacoes",
        "evidencia", "evidencias", "evidaancia", "evidaancias", "resultado", "resultados",
        "informacao", "informacoes", "conteudo", "conteudos", "e", "and", "informe", "informar", "diga", "dizer",
    }
)


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
    tokens = _tokens(objective)
    paths = _file_targets(objective)
    obligations: list[TaskObligation] = []
    has_explicit_read = any(
        token in _READ_VERBS and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )
    has_compare_reads = any(
        token == "compare" and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    ) and len(paths) >= 2
    if has_explicit_read or has_compare_reads:
        obligations.extend(
            TaskObligation(
                id=f"read:{index + 1}",
                kind="read",
                target=path,
                description=f"Ler o arquivo {path} do objetivo original.",
            )
            for index, path in enumerate(paths)
        )

    search_spec = _search_spec(objective, tokens)
    if search_spec is not None:
        query, query_source = search_spec
        obligations.append(
            TaskObligation(
                id="requirement:search",
                kind="search",
                query=query,
                query_source=query_source,
                description="Cumprir o requisito de busca do objetivo original.",
            )
        )

    if has_compare_reads:
        obligations.append(
            TaskObligation(
                id="requirement:compare",
                kind="compare",
                operands=tuple(paths[:2]),
                condition="equals",
                description=f"Comparar {paths[0]} e {paths[1]} conforme o objetivo original.",
            )
        )
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


def _file_targets(objective: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _FILE_RE.finditer(objective):
        value = match.group(1).strip(".,;:()[]{}")
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    return tuple(values)


def _search_spec(objective: str, tokens: Sequence[str]) -> tuple[str | None, str | None] | None:
    normalized_objective = _normalize_text(objective)
    for index, token in enumerate(tokens):
        if _OBLIGATION_TERMS.get(token) != "search" or _is_negated(tokens, index):
            continue
        candidates = tokens[index + 1 : index + 8]
        for candidate_index, candidate in enumerate(candidates):
            if candidate not in _SEARCH_FILLER and candidate not in _OBLIGATION_TERMS:
                previous = candidates[candidate_index - 1] if candidate_index else None
                if candidate == "valor" and (
                    "valor observado" in normalized_objective
                    or previous in {"esse", "essa", "este", "esta"}
                ):
                    return None, "previous_read"
                return candidate, None
        if any(
            phrase in normalized_objective
            for phrase in ("palavra que ele contem", "texto observado", "valor observado", "use o texto observado")
        ):
            return None, "previous_read"
        # A generic search request has no bounded identity to bind. Do not
        # manufacture a previous-read obligation that the task never asked for.
        return None
    return None


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
