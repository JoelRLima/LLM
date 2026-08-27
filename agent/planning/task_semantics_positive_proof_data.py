"""Closed vocabulary and lexical constants for positive authority parsing."""

from __future__ import annotations

import re

_MAX_OBJECTIVE_CHARS = 4_096
_MAX_SCOPE_CHARS = 1_024

_LEXEME_RE = re.compile(
    r"[\w./\\-]+\.[A-Za-z0-9]{1,16}|[\w/\\-]+|[^\w\s]",
    re.UNICODE,
)
_PATH_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,16}", re.UNICODE)
_MUTATION_VERBS = frozenset(
    {
        "adicione",
        "adicionar",
        "ajuste",
        "ajustar",
        "alter",
        "alterar",
        "altere",
        "aplicar",
        "aplique",
        "change",
        "corrija",
        "corrigir",
        "create",
        "crie",
        "criar",
        "delete",
        "edit",
        "edite",
        "editar",
        "fix",
        "modify",
        "modifique",
        "modificar",
        "refactor",
        "remove",
        "remova",
        "remover",
        "replace",
        "substitua",
        "substituir",
        "touch",
        "toque",
        "update",
    }
)
_OUTPUT_VERBS = frozenset(
    {
        "create",
        "crie",
        "criar",
        "escreva",
        "escrever",
        "generate",
        "gere",
        "gerar",
        "produce",
        "produza",
        "produzir",
        "save",
        "salve",
        "salvar",
        "store",
        "guarde",
        "guardar",
        "write",
    }
)
_MEMORY_DIRECT_VERBS = frozenset(
    {
        "esqueca",
        "esquecer",
        "forget",
        "lembre",
        "lembrar",
        "memorise",
        "memorize",
        "memorizar",
        "remember",
    }
)
_MEMORY_CONTEXT_VERBS = frozenset(
    {
        "apague",
        "apagar",
        "delete",
        "guarde",
        "guardar",
        "remove",
        "remova",
        "remover",
        "save",
        "salve",
        "salvar",
        "store",
    }
)
_AUTHORITY_VERBS = _MUTATION_VERBS | _OUTPUT_VERBS | _MEMORY_DIRECT_VERBS
_NEGATION_TOKENS = frozenset(
    {
        "avoid",
        "forbid",
        "forbidden",
        "jamais",
        "nao",
        "never",
        "not",
        "proibido",
        "prohibited",
        "sem",
        "without",
    }
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
_RESPONSE_VERBS = frozenset(
    {
        "analise",
        "analisar",
        "analyze",
        "descreva",
        "describe",
        "explique",
        "explain",
        "resuma",
        "resumir",
        "summarise",
        "summarize",
        "use",
    }
)
_READ_VERBS = frozenset(
    {"consulte", "examine", "inspect", "inspecione", "leia", "ler", "read"}
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
