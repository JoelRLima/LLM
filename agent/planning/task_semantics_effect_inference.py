"""Bounded effect-intent parsing for objective-derived task semantics."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from agent.planning.task_semantics_types import EffectIntent, _normalize_text

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)
_FILE_RE = re.compile(r"(?<!\w)([\w./\\-]+\.[A-Za-z0-9]{1,16})(?!\w)", re.UNICODE)
_NEGATION_WORDS = frozenset(
    {"nao", "sem", "never", "nunca", "jamais", "without", "no"}
)
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
        "analise", "analisar", "analyze", "consider", "considere", "could",
        "deve", "devemos", "must", "need", "needs", "maybe", "might", "perhaps",
        "poderia", "precisa", "should", "talvez", "would", "condition", "conditionally",
        "discretion", "optional", "optionally",
    }
)
_UNSUPPORTED_SCOPE_WORDS = frozenset(
    {"caso", "except", "if", "only", "provided", "unless", "when", "se", "otherwise"}
)
_GOVERNED_POSITIVE_WORDS = frozenset(
    {"asked", "ask", "pediu", "pediram", "requested", "request", "solicitou", "solicitar"}
)
_IMPERATIVE_FILLERS = frozenset({"please", "por", "favor", "kindly"})
_PREDICATE_OPERATOR_TOKENS = frozenset(
    {"contain", "contains", "contem", "conter", "contiver", "equal", "equals", "for", "is", _COPULA_TOKEN}
)
_DIRECT_TEXT_WORDS = frozenset({"exatamente", "exactly"})
_MUTATION_VERBS = frozenset(
    {
        "adicione", "adicionar", "ajuste", "ajustar", "alter", "alterar", "altere",
        "aplicar", "aplique", "aplicacao", "change", "changing", "corrija",
        "corrigir", "delete", "edit", "edite", "editar", "fix", "modify",
        "modifique", "modificar", "modificacao", "mudanca", "remove", "remova",
        "remover", "refactor", "replace", "substitua", "substituir", "touch",
        "update", "tocar", "toque",
    }
)
_OUTPUT_VERBS = frozenset(
    {
        "create", "crie", "criar", "escreva", "escrever", "write", "produza",
        "produzir", "produce", "gere", "gerar", "gera", "generate",
    }
)
_PERSISTENCE_VERBS = frozenset(
    {"salve", "salvar", "save", "guardar", "guarde", "store", "armazenar"}
)
_RESPONSE_VERBS = frozenset(
    {
        "resuma", "resumir", "summarize", "summarise", "explique", "explicar",
        "explain", "analise", "analisar", "analyze", "descreva", "descrever",
        "describe",
    }
)
_EXPLICIT_TARGET_WORDS = frozenset(
    {
        "arquivo", "arquivos", "file", "files", "path", "caminho", "workspace",
        "diretorio", "diretorios", "directory", "module", "modulo", "documento",
        "document",
    }
)
_OUTPUT_ONLY_NOUNS = frozenset(
    {
        "resumo", "summary", "lista", "list", "explicacao", "explanation", "answer",
        "resposta", "texto", "text", "funcoes", "functions", "resultado", "result",
        "relatorio", "report", "descricao", "description", "informacao", "information",
    }
)
_NEGATED_DURABLE_OUTPUT_MARKERS = frozenset(
    {"nada", "anything", "arquivos", "files", "arquivo", "file"}
)
_EFFECT_TERMS = {
    "adicione": "write", "adicionar": "write", "ajuste": "write", "ajustar": "write",
    "alter": "write", "alterar": "write", "altere": "write", "alteracao": "write", "aplicar": "write",
    "aplique": "write", "aplicacao": "write", "change": "write", "corrija": "write",
    "corrigir": "write", "create": "write", "crie": "write", "criar": "write",
    "delete": "write", "edit": "write", "edite": "write", "editar": "write",
    "escreva": "write", "escrever": "write", "fix": "write", "modifique": "write",
    "modificar": "write", "modificacao": "write", "modify": "write", "mudanca": "write", "remova": "write", "remover": "write",
    "refactor": "write", "replace": "write", "substitua": "write", "substituir": "write",
    "update": "write", "write": "write", "produza": "write", "produzir": "write",
    "gere": "write", "gerar": "write", "gera": "write", "generate": "write",
    "produce": "write", "changing": "write", "touch": "write", "toque": "write", "tocar": "write",
    "salve": "write", "salvar": "write", "save": "write", "guardar": "write",
    "guarde": "write", "store": "write", "armazenar": "write",
}
_MEMORY_CONTEXT_WORDS = frozenset({"memoria", "memory"})
_MEMORY_DIRECT_TERMS = frozenset(
    {
        "lembre", "lembrar", "remember", "memorize", "memorise", "memorizar",
        "esqueca", "esquecer", "forget",
    }
)
_MEMORY_CONTEXT_TERMS = frozenset(
    {
        "salve", "salvar", "save", "guardar", "guarde", "store", "armazenar",
        "remova", "remover", "remove", "delete", "apague", "apagar",
    }
)
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
    objective = _repair_mojibake(objective)
    lexical = objective.casefold().replace("\u00e2\u20ac\u2122", "'")
    lexical = lexical.replace("don't", "do not").replace("dont", "do not")
    # Preserve authority-bearing syntax before accent-insensitive matching.
    # Portuguese ``é`` is a copula, not the conjunction ``e``; collapsing the
    # two made an earlier ``não`` fall outside the governed effect clause.
    return tuple(
        _COPULA_TOKEN if token == "é" else _normalize_text(token)
        for token in _WORD_RE.findall(lexical)
    )


def _repair_mojibake(text: str) -> str:
    """Recover one common UTF-8-as-Latin-1 layer without changing valid text."""

    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    return repaired if repaired != text else text


def _is_negated(tokens: Sequence[str], index: int) -> bool:
    """Resolve bounded clause-level prohibition scope for one effect verb.

    The old suffix-window rule lost ``nao`` as soon as a subject or modal was
    inserted between it and the effect.  We retain a bounded deterministic
    grammar: inspect the current conjunction segment, then accept a
    prohibition cue anywhere before the governed effect.  A new ``e``/``and``
    segment starts a fresh scope, so ``nao edite a.py e edite b.py`` does not
    prohibit the second operation.
    """

    if index < 0 or index >= len(tokens):
        return False
    start = 0
    condition_start = _condition_start(tokens)
    if condition_start is not None and condition_start < len(tokens):
        operator_index = next(
            (
                position
                for position in range(condition_start + 1, index)
                if tokens[position] in _PREDICATE_OPERATOR_TOKENS
            ),
            None,
        )
        if operator_index is not None:
            value_index = operator_index + 1
            if value_index < index and tokens[value_index] in _DIRECT_TEXT_WORDS:
                value_index += 1
            # Predicate negation is consumed by predicate truth. Effect
            # prohibition begins only after the predicate literal.
            start = min(index, value_index + 1)
    for position in range(index - 1, -1, -1):
        if tokens[position] in {"e", "and"}:
            start = max(start, position + 1)
            break
    window = tuple(tokens[start:index])
    if any(token in _PROHIBITION_WORDS for token in window):
        return True
    if any(token in _NEGATION_WORDS or token in {"dont", "not", "cannot"} for token in window):
        return True
    patterns = (
        ("do", "not"),
        ("must", "not"),
        ("should", "not"),
        ("nao", "deve"),
        ("nao", "quero"),
        ("nao", "pode"),
    )
    if any(
        len(pattern) <= len(window)
        and any(
            tuple(window[offset : offset + len(pattern)]) == pattern
            for offset in range(len(window) - len(pattern) + 1)
        )
        for pattern in patterns
    ):
        return True

    # A prohibition can govern an operation from the right as well as from
    # the left.  This is intentionally a small structural check, not a claim
    # that the deny vocabulary is exhaustive.  The positive-admission kernel
    # remains fail-closed when an unfamiliar constraint is not recognized.
    suffix = tuple(tokens[index + 1 :])
    postposed_patterns = (
        ("is", "prohibited"),
        ("is", "forbidden"),
        ("is", "disallowed"),
        ("is", "not", "allowed"),
        ("is", "not", "permitted"),
        ("is", "not", "authorized"),
        ("not", "allowed"),
        ("not", "permitted"),
        ("not", "authorized"),
        ("e", "proibido"),
        ("e", "proibida"),
        (_COPULA_TOKEN, "proibido"),
        (_COPULA_TOKEN, "proibida"),
        ("nao", "e", "permitido"),
        ("nao", "e", "permitida"),
        ("nao", _COPULA_TOKEN, "permitido"),
        ("nao", _COPULA_TOKEN, "permitida"),
        ("under", "no", "circumstances"),
        ("under", "no", "account"),
        ("by", "no", "means"),
    )
    return any(
        len(pattern) <= len(suffix)
        and any(
            tuple(suffix[offset : offset + len(pattern)]) == pattern
            for offset in range(len(suffix) - len(pattern) + 1)
        )
        for pattern in postposed_patterns
    )


def _is_direct_text_request(tokens: Sequence[str], index: int) -> bool:
    return index + 1 < len(tokens) and tokens[index + 1] in _DIRECT_TEXT_WORDS


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


def _path_mentions(clause: str) -> tuple[tuple[str, int], ...]:
    """Return concrete paths and their bounded token positions."""

    repaired = _repair_mojibake(clause)
    word_matches = list(_WORD_RE.finditer(repaired))
    mentions: list[tuple[str, int]] = []
    for match in _FILE_RE.finditer(repaired):
        value = match.group(1).strip(".,;:()[]{}")
        if not value:
            continue
        token_index = next(
            (
                index
                for index, word_match in enumerate(word_matches)
                if word_match.start() < match.end() and word_match.end() > match.start()
            ),
            len(word_matches),
        )
        mentions.append((value, token_index))
    return tuple(mentions)


def _between(tokens: Sequence[str], left: int, right: int) -> tuple[str, ...]:
    return tuple(tokens[left + 1 : right]) if right > left else ()


def _destination_relation(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    between = _between(tokens, effect_index, path_index)
    # These are relation markers in an explicit destination construction.  A
    # role classifier, rather than a growing exception list, owns their use.
    return bool(set(between) & {"em", "no", "na", "nos", "nas", "to", "into", "in"})


def _source_relation(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    return bool(
        set(_between(tokens, effect_index, path_index))
        & {"about", "of", "de", "do", "da", "dos", "das", "sobre", "from", "using", "with"}
    )


def _response_context(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    between = _between(tokens, effect_index, path_index)
    return bool(set(between) & (_OUTPUT_ONLY_NOUNS | _RESPONSE_VERBS))


def _conditional_symbolic_target(tokens: Sequence[str], effect_index: int) -> str | None:
    """Return a bounded symbolic branch target without treating it as a path."""

    if not any(token in {"se", "if", "caso", "otherwise"} for token in tokens[:effect_index]):
        return None
    candidate = tokens[effect_index + 1] if effect_index + 1 < len(tokens) else ""
    if candidate in {
        "a", "o", "as", "os", "um", "uma", "an", "the", "texto", "text",
        "exatamente", "exactly",
    } or candidate in _OUTPUT_ONLY_NOUNS:
        return None
    return candidate or None


def _classify_path_roles(clause: str, tokens: Sequence[str]) -> tuple[_PathMention, ...]:
    """Classify paths before creating any durable effect intent.

    Only destination and explicit mutation roles are authority-bearing.  A
    source/topic or unresolved mention is deliberately non-durable.
    """

    raw_mentions = _path_mentions(clause)
    if not raw_mentions:
        return ()
    effect_positions = tuple(
        (index, token, _EFFECT_TERMS[token])
        for index, token in enumerate(tokens)
        if token in _EFFECT_TERMS
    )
    mutation_positions = tuple(
        index for index, token, _effect in effect_positions if token in _MUTATION_VERBS
    )
    output_positions = tuple(
        (index, token)
        for index, token, _effect in effect_positions
        if token in _OUTPUT_VERBS or token in _PERSISTENCE_VERBS
    )
    response_positions = tuple(
        index for index, token in enumerate(tokens) if token in _RESPONSE_VERBS
    )
    roles: list[_PathMention] = []
    for value, path_index in raw_mentions:
        role = PathRole.UNKNOWN
        governing_effect: int | None = None
        # A concrete path following an explicit mutation verb is governed by
        # that operation until the next effect verb.  Paths in a leading
        # conditional/source clause remain non-targets.
        governing_mutation = next(
            (
                index
                for index in reversed(mutation_positions)
                if index < path_index
                and not any(index < later < path_index for later in mutation_positions)
            ),
            None,
        )
        if governing_mutation is not None:
            role = PathRole.MUTATION_TARGET
            governing_effect = governing_mutation
        elif mutation_positions and not any(index < path_index for index in mutation_positions):
            # A bounded fallback supports the existing imperative form
            # ``controle.txt ... altere`` when the target is the only path and
            # appears as the condition's subject.
            if len(raw_mentions) == 1:
                role = PathRole.MUTATION_TARGET
                governing_effect = mutation_positions[0]
        else:
            governing_output = next(
                (
                    (index, token)
                    for index, token in reversed(output_positions)
                    if index < path_index
                    and not any(index < later < path_index for later, _ in output_positions)
                ),
                None,
            )
            if governing_output is not None:
                output_index, output_token = governing_output
                later_destination = any(
                    later_index > path_index
                    and _destination_relation(tokens, output_index, later_index)
                    for _later_value, later_index in raw_mentions
                )
                if later_destination and path_index < next(
                    later_index
                    for _later_value, later_index in raw_mentions
                    if later_index > path_index
                    and _destination_relation(tokens, output_index, later_index)
                ):
                    # In ``save a copy of source.py to backup.md`` the first
                    # path is source material and the second is the durable
                    # destination.
                    role = PathRole.SOURCE
                    governing_effect = output_index
                elif output_token in _PERSISTENCE_VERBS and (
                    _destination_relation(tokens, output_index, path_index)
                    or not _response_context(tokens, output_index, path_index)
                ):
                    role = PathRole.DESTINATION
                    governing_effect = output_index
                elif _destination_relation(tokens, output_index, path_index):
                    role = PathRole.DESTINATION
                    governing_effect = output_index
                elif _response_context(tokens, output_index, path_index) or _source_relation(
                    tokens, output_index, path_index
                ):
                    role = PathRole.SOURCE
                    governing_effect = output_index
                else:
                    # Bare ``create foo.py`` / ``write foo.py`` is an
                    # explicit concrete target; unknown relation stays closed.
                    role = PathRole.MUTATION_TARGET
                    governing_effect = output_index
            elif response_positions and any(index < path_index for index in response_positions):
                role = PathRole.SOURCE
        roles.append(_PathMention(value, path_index, role, governing_effect))
    return tuple(roles)


def _branch_tokens(
    clause: str,
    tokens: Sequence[str],
    effect_index: int,
    condition: str | None,
) -> tuple[tuple[str, ...], int]:
    """Return the independently governed effect clause for positive parsing."""

    if condition is None:
        branch = tuple(tokens)
        return branch, effect_index
    repaired = _repair_mojibake(clause)
    if "," in repaired:
        branch = _tokens(repaired.split(",", 1)[1])
        effect = tokens[effect_index]
        try:
            return branch, branch.index(effect)
        except ValueError:
            return (), -1
    # Keep the conditional grammar conservative when punctuation is omitted:
    # only the effect tail after the parser's selected verb can be admitted.
    return tuple(tokens[effect_index:]), 0


def _positive_prefix_supported(prefix: Sequence[str]) -> bool:
    """Accept only direct imperatives or a bounded positive request frame."""

    cleaned = tuple(prefix)
    if not cleaned:
        return True
    if any(
        token in _NEGATION_WORDS
        or token in _PROHIBITION_WORDS
        or token in _AMBIGUITY_WORDS
        for token in cleaned
    ):
        return False
    if all(token in _IMPERATIVE_FILLERS for token in cleaned):
        return True
    # A compound direct request may introduce the next imperative after a
    # conjunction (``gere ... e salve ...``).  Only the empty tail after the
    # conjunction is accepted here; modal/prose material still has to pass
    # the governed-request rule below.
    last_conjunction = max(
        (index for index, token in enumerate(cleaned) if token in {"e", "and"}),
        default=-1,
    )
    if last_conjunction >= 0 and not cleaned[last_conjunction + 1 :]:
        return True
    # Reported requests remain advisory candidate syntax only. Canonical
    # objective authority is independently decided by the direct-user proof
    # grammar and never by this candidate projection.
    return any(token in _GOVERNED_POSITIVE_WORDS for token in cleaned)


_POSITIVE_SEQUENCE_FILLERS = frozenset(
    {"apos", "after", "depois", "em", "seguida", "subsequently", "then", "entao"}
)


def _positive_scope_prefix(prefix: Sequence[str]) -> tuple[str, ...]:
    """Keep only the bounded request frame governing the selected verb."""

    cleaned = tuple(prefix)
    last_conjunction = max(
        (index for index, token in enumerate(cleaned) if token in {"e", "and"}),
        default=-1,
    )
    scoped = cleaned[last_conjunction + 1 :] if last_conjunction >= 0 else cleaned
    start = 0
    while start < len(scoped) and scoped[start] in _POSITIVE_SEQUENCE_FILLERS:
        start += 1
    return scoped[start:]


def _positive_candidate_syntax(
    clause: str,
    tokens: Sequence[str],
    effect_index: int,
    target: str,
    role: PathRole,
    condition: str | None,
    path_roles: Sequence[_PathMention],
) -> bool:
    """Classify a bounded positive construction without granting authority.

    This is metadata on an advisory candidate.  The admission owner still
    checks this metadata together with source, scope, conflict, and condition
    invariants before creating durable authority.
    """

    if target == "*" or role is PathRole.UNKNOWN or _is_negated(tokens, effect_index):
        return False
    branch, branch_index = _branch_tokens(clause, tokens, effect_index, condition)
    if not branch or branch_index < 0 or branch_index >= len(branch):
        return False
    token = branch[branch_index]
    prefix = _positive_scope_prefix(branch[:branch_index])
    if any(item in _AMBIGUITY_WORDS for item in (*prefix, *branch[branch_index + 1 :])):
        return False
    if condition is None and any(item in _UNSUPPORTED_SCOPE_WORDS for item in branch):
        return False
    if role is PathRole.MEMORY:
        return token in _MEMORY_DIRECT_TERMS or token in _MEMORY_CONTEXT_TERMS
    if role not in {PathRole.DESTINATION, PathRole.MUTATION_TARGET}:
        return False
    if token == "changing":
        return False
    if token not in (_MUTATION_VERBS | _OUTPUT_VERBS | _PERSISTENCE_VERBS):
        return False
    return _positive_prefix_supported(prefix)


def _is_durable_output_verb(token: str, clause_tokens: Sequence[str], clause: str) -> bool:
    """Compatibility predicate backed by bounded path-role classification."""

    if token not in _OUTPUT_VERBS and token not in _PERSISTENCE_VERBS:
        return True
    roles = _classify_path_roles(clause, clause_tokens)
    if any(
        item.role in {PathRole.DESTINATION, PathRole.MUTATION_TARGET} for item in roles
    ):
        return True
    if any(word in _EXPLICIT_TARGET_WORDS for word in clause_tokens) and not any(
        word in _OUTPUT_ONLY_NOUNS for word in clause_tokens
    ):
        # ``write the file`` is an explicit workspace operation even though
        # the caller supplied no concrete path; retain its generic effect
        # obligation without treating a source/topic path as a target.
        return True
    try:
        index = clause_tokens.index(token)
    except ValueError:
        return False
    return _conditional_symbolic_target(clause_tokens, index) is not None


def _condition_start(tokens: Sequence[str]) -> int | None:
    """Locate a supported conditional marker in a bounded clause."""

    label_offset = 0
    initial = next(
        (
            index
            for index in range(label_offset, len(tokens))
            if tokens[index] in {"se", "if"}
        ),
        None,
    )
    if initial is None:
        return None
    if initial == label_offset:
        return initial
    if any(
        token in _PREDICATE_OPERATOR_TOKENS
        for token in tokens[initial + 1 :]
    ):
        return initial
    return None


def _clause_condition(clause: str) -> str | None:
    tokens = _tokens(clause)
    if not tokens:
        return None
    condition_start = _condition_start(tokens)
    if condition_start is not None or "caso contrario" in " ".join(tokens) or "otherwise" in tokens:
        start = condition_start if condition_start is not None else 0
        return " ".join(tokens[start : start + MAX_CONDITION_TOKENS])
    return None


def _predicate_claim(
    clause: str,
    previous: _PredicateClaim | None,
) -> _PredicateClaim | None:
    """Extract the bounded predicate relation governing one clause.

    Supported forms are intentionally small: a concrete path followed by a
    ``contains/contiver`` (or equality) operator and a literal, plus an
    ``otherwise/caso contrario`` branch linked to the immediately preceding
    predicate.  Anything else receives a stable unresolved identity so it can
    never grant durable authority from prose alone.
    """

    tokens = _tokens(clause)
    condition = _clause_condition(clause)
    if not tokens or condition is None:
        return None
    start = _condition_start(tokens)
    if "caso" in tokens and "contrario" in tokens or "otherwise" in tokens:
        if previous is not None:
            return _PredicateClaim(previous.predicate_id, not previous.expected, condition)
        identity = "condition:" + hashlib.sha256(
            _normalize_text(condition).encode("utf-8")
        ).hexdigest()[:16]
        return _PredicateClaim(identity, False, condition)
    if start is None:
        return None
    if start >= len(tokens) or tokens[start] not in {"se", "if"}:
        return None
    operators = {
        "contiver": "contains", "contem": "contains", "contains": "contains",
        "contain": "contains", "conter": "contains", "equals": "equals",
        "equal": "equals", "for": "equals", "is": "equals", _COPULA_TOKEN: "equals",
    }
    operator_index = next(
        (index for index in range(start + 1, len(tokens)) if tokens[index] in operators),
        None,
    )
    paths = _file_targets(clause)
    if operator_index is not None and paths and operator_index + 1 < len(tokens):
        value_index = operator_index + 1
        # ``contiver exatamente \"literal\"`` is the Portuguese form used
        # by the deferred-condition contract.  ``exatamente``/``exactly`` is
        # a comparison modifier, not the literal being tested.
        if value_index < len(tokens) and tokens[value_index] in _DIRECT_TEXT_WORDS:
            value_index += 1
        if value_index >= len(tokens):
            value_index = operator_index + 1
        value = tokens[value_index]
        identity = f"{_normalize_text(paths[0])}|{operators[tokens[operator_index]]}|{value}"
        predicate_negated = any(
            token in _NEGATION_WORDS or token in {"not", "never"}
            for token in tokens[start + 1 : operator_index]
        )
        return _PredicateClaim(identity, not predicate_negated, condition)
    identity = "condition:" + hashlib.sha256(
        _normalize_text(condition).encode("utf-8")
    ).hexdigest()[:16]
    return _PredicateClaim(identity, True, condition)


MAX_CONDITION_TOKENS = 32


def _intent_clauses(objective: str) -> tuple[str, ...]:
    repaired = _repair_mojibake(objective)
    clauses = re.split(
        r"(?:;|\bmas\b|\bbut\b)",
        repaired,
        flags=re.IGNORECASE,
    )
    return tuple(item.strip() for item in clauses if item.strip())


def _clause_intents(objective: str) -> tuple[EffectIntent, ...]:
    intents: list[EffectIntent] = []
    previous_predicate: _PredicateClaim | None = None
    for clause in _intent_clauses(objective):
        tokens = _tokens(clause)
        paths = _file_targets(clause)
        path_roles = _classify_path_roles(clause, tokens)
        condition = _clause_condition(clause)
        predicate = _predicate_claim(clause, previous_predicate)
        is_otherwise = bool(
            ("caso" in tokens and "contrario" in tokens)
            or "otherwise" in tokens
        )
        # The complement must link to the immediately preceding predicate,
        # regardless of whether that predicate expects TRUE or FALSE.  Any
        # unrelated clause clears the link so an old branch cannot leak into a
        # later condition.
        if predicate is not None and not is_otherwise:
            previous_predicate = predicate
        elif is_otherwise:
            previous_predicate = None
        else:
            previous_predicate = None
        memory_indices = _memory_effect_indices(tokens)
        memory_index_set = set(memory_indices)
        for index, token in enumerate(tokens):
            effect = _EFFECT_TERMS.get(token)
            if effect is None:
                continue
            if (
                effect == "write"
                and token in (_OUTPUT_VERBS | _PERSISTENCE_VERBS)
                and not _is_durable_output_verb(token, tokens, clause)
            ):
                # A negated output verb paired with an explicit durable
                # marker (notably ``não escreva nada`` in a tool-use
                # instruction) is a prohibition, not a positive filesystem
                # request. Ordinary answer-language remains non-durable.
                if not (
                    _is_negated(tokens, index)
                    and set(tokens[index + 1 :]) & _NEGATED_DURABLE_OUTPUT_MARKERS
                ):
                    continue
            if _is_direct_text_request(tokens, index):
                continue
            if effect == "write" and index in memory_index_set and token in _MEMORY_CONTEXT_TERMS:
                continue
            if effect == "write" and token in (_OUTPUT_VERBS | _PERSISTENCE_VERBS):
                targets = tuple(
                    item.value
                    for item in path_roles
                    if item.effect_index == index
                    and item.role in {PathRole.DESTINATION, PathRole.MUTATION_TARGET}
                )
                # An output verb with no concrete authority-bearing role is
                # response language, not a wildcard filesystem write.  Keep a
                # negated ``write nothing`` prohibition as a bounded global
                # safety claim for compatibility with existing policy tests.
                if not targets:
                    symbolic_target = _conditional_symbolic_target(tokens, index)
                    if symbolic_target is not None and not _is_negated(tokens, index):
                        # Preserve the existing conditional-effect contract,
                        # but bind it to the symbolic branch label rather than
                        # manufacturing wildcard workspace authority.
                        targets = (symbolic_target,)
                    elif (
                        any(word in _EXPLICIT_TARGET_WORDS for word in tokens)
                        and not any(word in _OUTPUT_ONLY_NOUNS for word in tokens)
                    ):
                        targets = ("*",)
                    elif _is_negated(tokens, index) and set(tokens[index + 1 :]) & _NEGATED_DURABLE_OUTPUT_MARKERS:
                        targets = ("*",)
                    else:
                        continue
            else:
                scoped_targets = tuple(
                    item.value
                    for item in path_roles
                    if item.effect_index == index
                    and item.role is PathRole.MUTATION_TARGET
                )
                if scoped_targets:
                    targets = scoped_targets
                elif path_roles:
                    # A path mention that the bounded classifier cannot bind
                    # to this operation is UNKNOWN and must not become write
                    # authority through a legacy all-path fallback.
                    targets = ()
                else:
                    targets = paths or ("*",)
                if not targets:
                    continue
            for target in targets:
                path_role = next(
                    (
                        item.role
                        for item in path_roles
                        if item.value == target and item.effect_index == index
                    ),
                    PathRole.MUTATION_TARGET
                    if target != "*" and condition is not None
                    else PathRole.UNKNOWN,
                )
                intents.append(
                    EffectIntent(
                        effect=effect,
                        target=target,
                        polarity="prohibited" if _is_negated(tokens, index) else "requested",
                        condition=condition,
                        predicate_id=predicate.predicate_id if predicate is not None else None,
                        predicate_expected=predicate.expected if predicate is not None else None,
                        candidate_role=path_role.value,
                        positive_syntax=_positive_candidate_syntax(
                            clause,
                            tokens,
                            index,
                            target,
                            path_role,
                            condition,
                            path_roles,
                        ),
                    )
                )
        for index in memory_indices:
            intents.append(
                EffectIntent(
                    effect="memory_write",
                    target="memory",
                    polarity="prohibited" if _is_negated(tokens, index) else "requested",
                    condition=condition,
                    predicate_id=predicate.predicate_id if predicate is not None else None,
                    predicate_expected=predicate.expected if predicate is not None else None,
                    candidate_role=PathRole.MEMORY.value,
                    positive_syntax=_positive_candidate_syntax(
                        clause,
                        tokens,
                        index,
                        "memory",
                        PathRole.MEMORY,
                        condition,
                        path_roles,
                    ),
                )
            )
    return tuple(intents)


def _near_memory_context(tokens: Sequence[str], index: int) -> bool:
    start = max(0, index - 2)
    end = min(len(tokens), index + 5)
    return bool(set(tokens[start:end]) & _MEMORY_CONTEXT_WORDS)


def _memory_effect_indices(tokens: Sequence[str]) -> tuple[int, ...]:
    return tuple(
        index
        for index, token in enumerate(tokens)
        if token in _MEMORY_DIRECT_TERMS
        or (token in _MEMORY_CONTEXT_TERMS and _near_memory_context(tokens, index))
    )


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


__all__ = [
    "PathRole",
    "_READ_VERBS",
    "_file_targets",
    "_is_negated",
    "_normalize_text",
    "_repair_mojibake",
    "_search_spec",
    "_tokens",
    "_clause_intents",
    "_intent_clauses",
    "_memory_effect_indices",
]
