"""Canonical lexical constants and effect inference value types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from llm_agent.agent.planning.task_semantics_lexicon import CANONICAL_SEMANTIC_LEXICON, LexemeClass, LexiconRoute

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


_FILE_RE = re.compile(r"(?<!\w)([\w./\\-]+\.[A-Za-z0-9]{1,16})(?!\w)", re.UNICODE)


_CANDIDATE_ROUTE = LexiconRoute.CANDIDATE_INFERENCE


_NEGATION_WORDS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.NEGATION)


_COPULA_TOKEN = "__copula__"


_PROHIBITION_WORDS = frozenset(
    {
        "avoid",
        "abstenha",
        "evite",
        "evitar",
        "forbid",
        "forbids",
        "forbidden",
        "prohibit",
        "prohibits",
        "proibida",
        "proibidas",
        "proibido",
        "proibidos",
        "prohibited",
    }
)


_AMBIGUITY_WORDS = frozenset(
    {
        "analise",
        "analisar",
        "analyze",
        "consider",
        "considere",
        "could",
        "deve",
        "devemos",
        "must",
        "need",
        "needs",
        "maybe",
        "might",
        "perhaps",
        "poderia",
        "precisa",
        "should",
        "talvez",
        "would",
        "condition",
        "conditionally",
        "discretion",
        "optional",
        "optionally",
    }
)


_UNSUPPORTED_SCOPE_WORDS = frozenset({"caso", "except", "if", "only", "provided", "unless", "when", "se", "otherwise"})


_GOVERNED_POSITIVE_WORDS = frozenset(
    {"asked", "ask", "pediu", "pediram", "requested", "request", "solicitou", "solicitar"}
)


_IMPERATIVE_FILLERS = frozenset({"please", "por", "favor", "kindly"})


_PREDICATE_OPERATOR_TOKENS = frozenset(
    {"contain", "contains", "contem", "conter", "contiver", "equal", "equals", "for", "is", _COPULA_TOKEN}
)


_DIRECT_TEXT_WORDS = frozenset({"exatamente", "exactly"})


_MUTATION_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.MUTATION)


_OUTPUT_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.OUTPUT)


_PERSISTENCE_VERBS = frozenset(CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.PERSISTENCE))


_RESPONSE_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.RESPONSE)


_EXPLICIT_TARGET_WORDS = frozenset(
    {
        "arquivo",
        "arquivos",
        "file",
        "files",
        "path",
        "caminho",
        "workspace",
        "diretorio",
        "diretorios",
        "directory",
        "module",
        "modulo",
        "documento",
        "document",
    }
)


_OUTPUT_ONLY_NOUNS = frozenset(
    {
        "resumo",
        "summary",
        "lista",
        "list",
        "explicacao",
        "explanation",
        "answer",
        "resposta",
        "texto",
        "text",
        "funcoes",
        "functions",
        "resultado",
        "result",
        "relatorio",
        "report",
        "descricao",
        "description",
        "informacao",
        "information",
    }
)


_NEGATED_DURABLE_OUTPUT_MARKERS = frozenset({"nada", "anything", "arquivos", "files", "arquivo", "file"})


_EFFECT_TERMS = CANONICAL_SEMANTIC_LEXICON.effect_terms(_CANDIDATE_ROUTE)


_MEMORY_CONTEXT_WORDS = frozenset({"memoria", "memory"})


_MEMORY_DIRECT_TERMS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.MEMORY_DIRECT)


_MEMORY_CONTEXT_TERMS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.MEMORY_CONTEXT)


_OBLIGATION_TERMS = {
    "leia": "read",
    "ler": "read",
    "read": "read",
    "inspect": "read",
    "inspecione": "read",
    "examinar": "read",
    "examine": "read",
    "consulte": "read",
    "consultar": "read",
    "procure": "search",
    "procurar": "search",
    "busque": "search",
    "buscar": "search",
    "pesquise": "search",
    "pesquisar": "search",
    "search": "search",
    "find": "search",
    "encontre": "search",
    "encontrar": "search",
    "compare": "compare",
    "comparar": "compare",
    "diff": "compare",
    "analise": "analyze",
    "analisar": "analyze",
    "analyze": "analyze",
}


_READ_VERBS = CANONICAL_SEMANTIC_LEXICON.tokens(_CANDIDATE_ROUTE, LexemeClass.READ)


_SEARCH_FILLER = frozenset(
    {
        "o",
        "a",
        "os",
        "as",
        "um",
        "uma",
        "nos",
        "nas",
        "no",
        "na",
        "em",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "outros",
        "outras",
        "arquivos",
        "arquivo",
        "workspace",
        "workspaces",
        "esse",
        "essa",
        "este",
        "esta",
        "pela",
        "pelo",
        "por",
        "para",
        "que",
        "ele",
        "ela",
        "eles",
        "elas",
        "palavra",
        "texto",
        "ocorrencia",
        "correspondente",
        "contem",
        "contém",
        "observado",
        "observada",
        "observados",
        "observadas",
        "observacao",
        "observacoes",
        "evidencia",
        "evidencias",
        "evidaancia",
        "evidaancias",
        "resultado",
        "resultados",
        "informacao",
        "informacoes",
        "conteudo",
        "conteudos",
        "e",
        "and",
        "informe",
        "informar",
        "diga",
        "dizer",
    }
)


class PathRole(str, Enum):
    """Bounded semantic role of a concrete path mention in an objective."""

    SOURCE = "SOURCE"
    TOPIC = "TOPIC"
    DESTINATION = "DESTINATION"
    MUTATION_TARGET = "MUTATION_TARGET"
    MEMORY = "MEMORY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class _PathMention:
    value: str
    token_index: int
    role: PathRole
    effect_index: int | None = None


@dataclass(frozen=True, slots=True)
class _PredicateClaim:
    predicate_id: str
    expected: bool
    condition: str


MAX_CONDITION_TOKENS = 32
