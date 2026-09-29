"""Canonical immutable lexical classifications for task semantics.

The candidate-inference and positive-authority routes intentionally remain
independent consumers.  They share this typed lexical owner, while the
consumer-specific class memberships preserve the pre-existing closed
vocabulary where the two routes historically differed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class LexemeClass(str, Enum):
    """Closed semantic classes understood by the lexical owner."""

    MUTATION = "mutation"
    OUTPUT = "output"
    PERSISTENCE = "persistence"
    MEMORY_DIRECT = "memory_direct"
    MEMORY_CONTEXT = "memory_context"
    READ = "read"
    RESPONSE = "response"
    NEGATION = "negation"


class LexiconRoute(str, Enum):
    """Independent consumers of the canonical lexical classification."""

    CANDIDATE_INFERENCE = "candidate_inference"
    POSITIVE_AUTHORITY = "positive_authority"


@dataclass(frozen=True, slots=True)
class CanonicalLexeme:
    """One immutable token classification for both semantic consumers."""

    value: str
    candidate_classes: frozenset[LexemeClass]
    authority_classes: frozenset[LexemeClass]

    def classes(self, route: LexiconRoute) -> frozenset[LexemeClass]:
        """Return the closed class set for one independent consumer."""

        return (
            self.candidate_classes
            if route is LexiconRoute.CANDIDATE_INFERENCE
            else self.authority_classes
        )


@dataclass(frozen=True, slots=True)
class CanonicalSemanticLexicon:
    """Immutable owner of normalized lexical knowledge."""

    entries: Mapping[str, CanonicalLexeme]

    def classify(self, value: str) -> CanonicalLexeme | None:
        """Return a known token classification, or ``None`` for UNKNOWN."""

        return self.entries.get(value)

    def has(
        self,
        value: str,
        route: LexiconRoute,
        lexeme_class: LexemeClass,
    ) -> bool:
        """Check one route-specific class without fuzzy or fallback matching."""

        entry = self.classify(value)
        return entry is not None and lexeme_class in entry.classes(route)

    def tokens(
        self,
        route: LexiconRoute,
        *classes: LexemeClass,
    ) -> frozenset[str]:
        """Project an immutable exact-token set for a route and class union."""

        requested = frozenset(classes)
        if not requested:
            return frozenset()
        return frozenset(
            entry.value
            for entry in self.entries.values()
            if entry.classes(route) & requested
        )

    def effect_terms(self, route: LexiconRoute) -> Mapping[str, str]:
        """Project the existing durable-effect vocabulary for candidate inference."""

        effect_classes = {
            LexemeClass.MUTATION,
            LexemeClass.OUTPUT,
            LexemeClass.PERSISTENCE,
        }
        return MappingProxyType(
            {
                token: "write"
                for token in self.tokens(route, *effect_classes)
            }
        )


def _add_classes(
    table: dict[str, dict[LexiconRoute, set[LexemeClass]]],
    route: LexiconRoute,
    lexeme_class: LexemeClass,
    values: Iterable[str],
) -> None:
    for value in values:
        table.setdefault(value, {}).setdefault(route, set()).add(lexeme_class)


def _build_lexicon() -> CanonicalSemanticLexicon:
    table: dict[str, dict[LexiconRoute, set[LexemeClass]]] = {}

    # These are the exact pre-C1 vocabularies.  Route-specific memberships
    # retain historical candidate/authority differences without maintaining
    # two lexical owners.
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.MUTATION,
        {
            "adicione", "adicionar", "ajuste", "ajustar", "alter", "alterar",
            "altere", "aplicar", "aplique", "aplicacao", "change", "changing",
            "corrija", "corrigir", "delete", "edit", "edite", "editar", "fix",
            "modify", "modifique", "modificar", "modificacao", "mudanca", "remove",
            "remova", "remover", "refactor", "replace", "substitua", "substituir",
            "touch", "atualize", "atualizar", "update", "tocar", "toque", "configure",
            "configurar", "mude", "mudar",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.OUTPUT,
        {
            "create", "crie", "criar", "escreva", "escrever", "write", "produza",
            "produzir", "produce", "gere", "gerar", "gera", "generate",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.PERSISTENCE,
        {"salve", "salvar", "save", "guardar", "guarde", "store", "armazenar"},
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.MEMORY_DIRECT,
        {
            "lembre", "lembrar", "remember", "memorize", "memorise", "memorizar",
            "esqueca", "esquecer", "forget",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.MEMORY_CONTEXT,
        {
            "salve", "salvar", "save", "guardar", "guarde", "store", "armazenar",
            "remova", "remover", "remove", "delete", "apague", "apagar",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.READ,
        {
            "leia", "ler", "read", "inspect", "inspecione", "examinar", "examine",
            "consulte", "consultar",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.RESPONSE,
        {
            "resuma", "resumir", "summarize", "summarise", "explique", "explicar",
            "explain", "analise", "analisar", "analyze", "descreva", "descrever",
            "describe",
        },
    )
    _add_classes(
        table,
        LexiconRoute.CANDIDATE_INFERENCE,
        LexemeClass.NEGATION,
        {"nao", "sem", "never", "nunca", "jamais", "without", "no"},
    )

    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.MUTATION,
        {
            "adicione", "adicionar", "ajuste", "ajustar", "alter", "alterar",
            "altere", "aplicar", "aplique", "change", "corrija", "corrigir",
            "create", "crie", "criar", "delete", "edit", "edite", "editar", "fix",
            "modify", "modifique", "modificar", "refactor", "remove", "remova",
            "remover", "replace", "substitua", "substituir", "touch", "toque", "update",
            "atualize", "atualizar", "configure", "configurar", "mude", "mudar",
        },
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.OUTPUT,
        {
            "create", "crie", "criar", "escreva", "escrever", "generate", "gere",
            "gerar", "produce", "produza", "produzir", "save", "salve", "salvar",
            "store", "guarde", "guardar", "write",
        },
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.MEMORY_DIRECT,
        {
            "esqueca", "esquecer", "forget", "lembre", "lembrar", "memorise",
            "memorize", "memorizar", "remember",
        },
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.MEMORY_CONTEXT,
        {
            "apague", "apagar", "delete", "guarde", "guardar", "remove", "remova",
            "remover", "save", "salve", "salvar", "store",
        },
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.READ,
        {"consulte", "examine", "inspect", "inspecione", "leia", "ler", "read"},
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.RESPONSE,
        {
            "analise", "analisar", "analyze", "descreva", "describe", "explique",
            "explain", "resuma", "resumir", "summarise", "summarize", "use",
        },
    )
    _add_classes(
        table,
        LexiconRoute.POSITIVE_AUTHORITY,
        LexemeClass.NEGATION,
        {
            "avoid", "forbid", "forbidden", "jamais", "nao", "never", "not",
            "proibido", "prohibited", "sem", "without",
        },
    )

    entries = {
        value: CanonicalLexeme(
            value=value,
            candidate_classes=frozenset(routes.get(LexiconRoute.CANDIDATE_INFERENCE, ())),
            authority_classes=frozenset(routes.get(LexiconRoute.POSITIVE_AUTHORITY, ())),
        )
        for value, routes in table.items()
    }
    return CanonicalSemanticLexicon(MappingProxyType(entries))


CANONICAL_SEMANTIC_LEXICON = _build_lexicon()


__all__ = [
    "CANONICAL_SEMANTIC_LEXICON",
    "CanonicalLexeme",
    "CanonicalSemanticLexicon",
    "LexemeClass",
    "LexiconRoute",
]
