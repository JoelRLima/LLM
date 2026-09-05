"""Closed vocabulary and lexical constants for positive authority parsing."""

from __future__ import annotations

import re

from agent.planning.task_semantics_lexicon import (
    CANONICAL_SEMANTIC_LEXICON,
    LexemeClass,
    LexiconRoute,
)

_MAX_OBJECTIVE_CHARS = 4_096
_MAX_SCOPE_CHARS = 1_024

_LEXEME_RE = re.compile(
    r"[\w./\\-]+\.[A-Za-z0-9]{1,16}|[\w/\\-]+|[^\w\s]",
    re.UNICODE,
)
_PATH_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,16}", re.UNICODE)
_AUTHORITY_ROUTE = LexiconRoute.POSITIVE_AUTHORITY
_MUTATION_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.MUTATION
)
_OUTPUT_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.OUTPUT
)
# Only these output verbs can describe a source-only response without a
# durable destination. Persistence-shaped verbs remain in ``_OUTPUT_VERBS``
# so they are still parsed by the durable-output grammar, but they may not
# fall through to the neutral source-only production.
_NEUTRAL_SOURCE_ONLY_OUTPUT_VERBS = frozenset(
    {
        "generate",
        "gere",
        "gerar",
        "produce",
        "produza",
        "produzir",
    }
)
_MEMORY_DIRECT_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.MEMORY_DIRECT
)
_MEMORY_CONTEXT_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.MEMORY_CONTEXT
)
_AUTHORITY_VERBS = (
    _MUTATION_VERBS | _OUTPUT_VERBS | _MEMORY_DIRECT_VERBS
)
_NEGATION_TOKENS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.NEGATION
)
_DIRECT_REQUEST_PREFIXES = frozenset(
    {
        (),
        ("kindly",),
        ("please",),
        ("por", "favor"),
        ("depois",),
        ("then",),
        ("subsequently",),
    }
)
_ARTICLES = frozenset(
    {"a", "an", "as", "o", "os", "the", "um", "uma", "arquivo", "file"}
)
_OUTPUT_GRAMMAR_WORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "as",
        "arquivo",
        "copy",
        "com",
        "da",
        "das",
        "de",
        "description",
        "do",
        "dos",
        "em",
        "file",
        "findings",
        "from",
        "in",
        "into",
        "lista",
        "list",
        "na",
        "no",
        "o",
        "of",
        "os",
        "relatorio",
        "report",
        "resumo",
        "summary",
        "the",
        "these",
        "to",
        "um",
        "uma",
        "using",
        "with",
    }
)
_DESTINATION_RELATIONS = frozenset({"em", "in", "into", "na", "no", "to"})
_RESPONSE_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.RESPONSE
)
_READ_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(
    _AUTHORITY_ROUTE, LexemeClass.READ
)
_VALIDATION_TAILS = (
    ("e", "valide"),
    ("e", "valide", "a", "modificacao", "localmente"),
    ("e", "valide", "a", "modificacao"),
    ("e", "valide", "a", "mudanca", "localmente"),
    ("e", "valide", "a", "mudanca"),
    ("and", "validate"),
    ("and", "validate", "the", "change", "locally"),
    ("and", "validate", "the", "change"),
)
_PUNCTUATION = frozenset({",", ".", ":", "!", "?"})
_QUOTE_PAIRS = (("\"", "\""), ("`", "`"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))
