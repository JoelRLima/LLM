"""Closed deterministic W12 speech-act, target, and conflict guards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .evidence import (
    SpanKind,
    evidence_is_plain_exact,
    normalize_clause_for_guard,
    plain_occurrences,
    scan_clause_spans,
    scan_spans,
    strip_target_surrounding_punctuation,
)
from .lexicon import (
    ALL_EFFECT_LEXEMES,
    BROAD_TARGET_QUANTIFIERS,
    CONDITIONAL_PREFIXES,
    DEICTIC_TARGETS,
    EFFECT_LEXICON,
    GENERIC_TARGET_NOUNS,
    GLOBAL_FAMILY_CORES,
    GLOBAL_RESTRICTIONS,
    LOCAL_CONFLICT_SEPARATORS,
    MIXED_INTENT_MARKERS,
    NEGATION_PREFIXES,
    NEGATIVE_INFINITIVE_FORMS,
    PLAN_LEXEMES,
    PROCESS_OBJECT_HEADS,
    READ_LEXEMES,
    REQUEST_PREFIXES,
)


class OperationalClassification(str, Enum):
    DIRECT = "DIRECT"
    NEGATED = "NEGATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    QUOTED = "QUOTED"
    META = "META"
    CONTEXTUAL = "CONTEXTUAL"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class TargetProof(str, Enum):
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"


class ReadClassification(str, Enum):
    DIRECT_READ = "DIRECT_READ"
    CONTEXTUAL = "CONTEXTUAL"
    OPERATIONAL = "OPERATIONAL"
    PROPOSAL = "PROPOSAL"
    META = "META"
    UNKNOWN = "UNKNOWN"


class PlanClassification(str, Enum):
    DIRECT_PLAN = "DIRECT_PLAN"
    CONTEXTUAL = "CONTEXTUAL"
    OPERATIONAL = "OPERATIONAL"
    META = "META"
    UNKNOWN = "UNKNOWN"


class LocalConflictClassification(str, Enum):
    CLEAR = "CLEAR"
    CONFLICT = "CONFLICT"


class MixedIntentClassification(str, Enum):
    CLEAR = "CLEAR"
    MIXED_EFFECT = "MIXED_EFFECT"


class CrossClauseRelation(str, Enum):
    CLEAR = "CLEAR"
    INDEPENDENT = "INDEPENDENT"
    FAMILY_CONFLICT = "FAMILY_CONFLICT"
    SAME_TARGET_CONFLICT = "SAME_TARGET_CONFLICT"
    GLOBAL_CONFLICT = "GLOBAL_CONFLICT"
    UNKNOWN_RELATION_CONFLICT = "UNKNOWN_RELATION_CONFLICT"


@dataclass(frozen=True, slots=True)
class TargetAnchorIdentity:
    identity_class: str
    canonical: str | None
    identity_unresolved: bool = False

    @property
    def kind(self) -> str:
        return self.identity_class

    @property
    def identity(self) -> str | None:
        return self.canonical

    @property
    def normalized(self) -> str | None:
        return self.canonical


@dataclass(frozen=True, slots=True)
class OperationalAnalysis:
    classification: OperationalClassification
    family: str | None = None
    predicate: str | None = None
    complement: str = ""
    predicate_start: int = -1


@dataclass(frozen=True, slots=True)
class NegativeRestriction:
    family: str
    scope: str
    complement: str


_CONTEXTUAL_OPERATION_FORMS = frozenset(
    {
        "faz isso",
        "faça isso",
        "faca isso",
        "do that",
        "do this",
    }
)
_CONTEXTUAL_COMPLEMENT_PREFIXES = (
    "isso",
    "isto",
    "esse",
    "essa",
    "este",
    "esta",
    "aquilo",
    "this",
    "that",
    "it",
    "the previous one",
    "esse arquivo",
    "essa pasta",
    "esse mÃ³dulo",
    "esse modulo",
    "essa mÃ³dulo",
    "essa modulo",
    "this file",
    "that file",
    "this module",
    "that module",
    "this repository",
    "that repository",
    "this project",
    "that project",
)


def strip_one_request_prefix(clause: str) -> tuple[str, str | None]:
    value = normalize_clause_for_guard(clause)
    for prefix in REQUEST_PREFIXES:
        boundary = prefix + " "
        if value.startswith(boundary):
            return value[len(boundary) :], prefix
    return value, None


def _starts_lexeme(value: str, lexemes: tuple[str, ...]) -> tuple[str, str] | None:
    for lexeme in sorted(lexemes, key=len, reverse=True):
        if value.startswith(lexeme + " "):
            return lexeme, value[len(lexeme) + 1 :]
    return None


def _positive_effect(value: str, *, include_cross_only: bool = False) -> tuple[str, str, str] | None:
    for family, lexemes in EFFECT_LEXICON.items():
        available = lexemes
        if include_cross_only and family == "WRITE":
            available = tuple(
                sorted(
                    set(lexemes) | {"alter", "mexa", "toque", "touch"},
                    key=len,
                    reverse=True,
                )
            )
        found = _starts_lexeme(value, available)
        if found is not None:
            predicate, complement = found
            if complement:
                return family, predicate, complement
    return None


def _positive_effect_signal(value: str, *, include_cross_only: bool = False) -> tuple[str, str] | None:
    for family, lexemes in EFFECT_LEXICON.items():
        available = lexemes
        if include_cross_only and family == "WRITE":
            available = tuple(
                sorted(
                    set(lexemes) | {"alter", "mexa", "toque", "touch"},
                    key=len,
                    reverse=True,
                )
            )
        found = _starts_lexeme(value + " ", available)
        if found is not None:
            return family, found[0]
    return None


def _all_marker_occurrences(value: str, markers: tuple[str, ...]) -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int]] = []
    for marker in set(markers):
        needle = marker + " "
        cursor = 0
        while (index := value.find(needle, cursor)) >= 0:
            if marker and marker[0].isalnum() and index > 0 and not value[index - 1].isspace():
                cursor = index + 1
                continue
            occurrences.append((index, index + len(needle)))
            cursor = index + 1
    return sorted(occurrences, key=lambda item: (item[0], -(item[1] - item[0])))


def _negative_effect(value: str) -> tuple[str, str, str] | None:
    for marker in sorted(NEGATION_PREFIXES, key=len, reverse=True):
        prefix = marker + " "
        if value.startswith(prefix):
            positive = _positive_effect(value[len(prefix) :], include_cross_only=True)
            if positive is not None:
                family, predicate, complement = positive
                return family, predicate, complement
    for family, scopes in NEGATIVE_INFINITIVE_FORMS.items():
        for scope, forms in scopes.items():
            for form in sorted(forms, key=len, reverse=True):
                suffix = value.removeprefix(form)
                if (scope == "FAMILY_ALL" and not suffix) or (suffix.startswith(" ") and suffix[1:]):
                    return family, scope, suffix.strip()
    return None


def _conditional_prefix(value: str) -> bool:
    for prefix in sorted(CONDITIONAL_PREFIXES, key=len, reverse=True):
        if value == prefix or value.startswith(prefix + " "):
            return True
    return False


def _plain_effect_occurrence(value: str) -> bool:
    for lexeme in ALL_EFFECT_LEXEMES:
        if plain_occurrences(value, lexeme, limit=1):
            return True
    return False


def _plain_char_positions(value: str) -> set[int]:
    positions: set[int] = set()
    for span in scan_spans(value):
        if span.kind is SpanKind.PLAIN:
            positions.update(range(span.start, span.end))
    return positions


def _standalone_plain_conditional(value: str, after: int) -> bool:
    positions = _plain_char_positions(value)
    for token in ("se", "caso", "if"):
        cursor = after
        while True:
            found = value.find(token, cursor)
            if found < 0:
                break
            before_ok = found == 0 or value[found - 1].isspace() or value[found - 1] in ",;.!?"
            end = found + len(token)
            after_ok = end == len(value) or value[end].isspace() or value[end] in ",;.!?"
            if before_ok and after_ok and all(index in positions for index in range(found, end)):
                return True
            cursor = found + 1
    return False


def analyze_operational_clause(clause: str) -> OperationalAnalysis:
    normalized = normalize_clause_for_guard(clause)
    if not normalized:
        return OperationalAnalysis(OperationalClassification.UNKNOWN)
    stripped, _ = strip_one_request_prefix(normalized)
    prefix_offset = len(normalized) - len(stripped)

    if stripped in _CONTEXTUAL_OPERATION_FORMS:
        return OperationalAnalysis(OperationalClassification.CONTEXTUAL)
    if _conditional_prefix(stripped) and _plain_effect_occurrence(stripped):
        return OperationalAnalysis(OperationalClassification.HYPOTHETICAL)

    negative = _negative_effect(stripped)
    if negative is not None:
        family, predicate, complement = negative
        return OperationalAnalysis(OperationalClassification.NEGATED, family, predicate, complement, prefix_offset)

    positive = _positive_effect(stripped)
    if positive is not None:
        family, predicate, complement = positive
        predicate_end = prefix_offset + len(predicate)
        if _standalone_plain_conditional(normalized, predicate_end):
            return OperationalAnalysis(OperationalClassification.HYPOTHETICAL, family, predicate, complement, predicate_end)
        if _is_contextual_complement(complement):
            return OperationalAnalysis(OperationalClassification.CONTEXTUAL, family, predicate, complement, prefix_offset)
        return OperationalAnalysis(OperationalClassification.DIRECT, family, predicate, complement, prefix_offset)

    if _plain_effect_occurrence(normalized):
        meta_markers = (
            "instrução", "instrucao", "instruction", "phrase", "frase", "appears", "aparece",
            "example", "exemplo", "explicação", "explicacao", "explanation", "mentioned", "mencionada",
        )
        if any(marker in normalized for marker in meta_markers):
            return OperationalAnalysis(OperationalClassification.META)
    for span in scan_spans(normalized):
        if span.kind is not SpanKind.PLAIN and _plain_effect_occurrence(span.text):
            return OperationalAnalysis(OperationalClassification.QUOTED)
    return OperationalAnalysis(OperationalClassification.UNKNOWN)


def classify_operational_request(clause: str) -> OperationalClassification:
    return analyze_operational_clause(clause).classification


class DirectOperationalRequestGuard:
    @staticmethod
    def classify(clause: str) -> OperationalClassification:
        return classify_operational_request(clause)

    @staticmethod
    def analyze(clause: str) -> OperationalAnalysis:
        return analyze_operational_clause(clause)


def _is_contextual_complement(complement: str) -> bool:
    value = complement.strip()
    return any(
        value == prefix or value.startswith(prefix + " ")
        for prefix in _CONTEXTUAL_COMPLEMENT_PREFIXES
    )


def _anchor_candidate(value: str) -> str | None:
    raw = value.strip()
    candidate = raw if raw.startswith(("./", ".\\")) else strip_target_surrounding_punctuation(raw)
    lowered = candidate.casefold()
    for prefix in (
        "o arquivo ", "a arquivo ", "os arquivos ", "as arquivos ", "arquivo ", "the file ",
        "the files ", "file ", "o módulo ", "o modulo ", "the module ",
    ):
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix) :].strip()
            lowered = candidate.casefold()
            break
    return candidate or None


def normalize_target_anchor_identity(value: str) -> TargetAnchorIdentity:
    candidate = _anchor_candidate(value)
    if candidate is None or " " in candidate or candidate.casefold() in GENERIC_TARGET_NOUNS or candidate.casefold() in DEICTIC_TARGETS:
        return TargetAnchorIdentity("AMBIGUOUS", None, True)
    lowered = candidate.casefold()
    if lowered.startswith(("http://", "https://")):
        return TargetAnchorIdentity("URL", lowered, False)
    if "@" in candidate and " " not in candidate:
        return TargetAnchorIdentity("EMAIL", lowered, False)
    if any(char in candidate for char in (".", "/", "\\", ":")):
        absolute = lowered.startswith("/")
        pieces = lowered.replace("\\", "/").split("/")
        result: list[str] = []
        unresolved = False
        for piece in pieces:
            if piece in ("", "."):
                continue
            if piece == "..":
                if result and result[-1] != "..":
                    result.pop()
                else:
                    result.append(piece)
                    unresolved = True
            else:
                result.append(piece)
        canonical = "/".join(result)
        if absolute:
            canonical = "/" + canonical
        return TargetAnchorIdentity("LOCAL_PATH", canonical, unresolved)
    if "_" in candidate or "-" in candidate:
        return TargetAnchorIdentity("IDENTIFIER", lowered, False)
    return TargetAnchorIdentity("AMBIGUOUS", None, True)


def _target_fragment(analysis: OperationalAnalysis) -> str | None:
    raw_complement = analysis.complement.strip()
    complement = raw_complement if raw_complement.startswith(("./", ".\\")) else strip_target_surrounding_punctuation(raw_complement)
    family = analysis.family
    predicate = analysis.predicate or ""
    if family == "WRITE" and predicate in {"aplique", "apply"}:
        exact = {
            "o patch", "patch", "as alterações", "as alteracoes", "as mudanças", "as mudancas",
            "the patch", "the changes", "changes",
        }
        if complement in exact:
            return None
        for prefix in ("o patch em ", "patch em ", "o patch no ", "patch no ", "o patch na ", "patch na ", "the patch to ", "patch to "):
            if complement.startswith(prefix):
                return complement[len(prefix) :]
        return "__unproven__"
    if family == "WRITE" and predicate in {"commite", "faça commit", "faca commit", "commit"}:
        if complement in {"as alterações", "as alteracoes", "as mudanças", "as mudancas", "o commit", "commit", "the changes", "changes", "the commit"}:
            return None
        return "__unproven__"
    if family == "PROCESS":
        return complement
    if family == "NETWORK" and predicate in {"pesquise na web", "busque na web", "search the web", "browse the web"}:
        return None
    return complement


def _begins_process_object_head(complement: str) -> bool:
    return any(complement == head or complement.startswith(head + " ") for head in PROCESS_OBJECT_HEADS)


def direct_operational_target_proof(analysis_or_clause: OperationalAnalysis | str) -> TargetProof:
    analysis = (
        analysis_or_clause
        if isinstance(analysis_or_clause, OperationalAnalysis)
        else analyze_operational_clause(analysis_or_clause)
    )
    if analysis.classification is not OperationalClassification.DIRECT:
        return TargetProof.UNPROVEN
    family = analysis.family
    predicate = analysis.predicate or ""
    complement = normalize_clause_for_guard(analysis.complement)
    if not complement:
        return TargetProof.UNPROVEN
    fragment = _target_fragment(analysis)
    if fragment is None:
        if family == "NETWORK" and predicate in {"pesquise na web", "busque na web", "search the web", "browse the web"}:
            return TargetProof.PROVEN
        if family == "WRITE" and predicate in {"aplique", "apply", "commite", "faça commit", "faca commit", "commit"}:
            return TargetProof.PROVEN
        return TargetProof.UNPROVEN
    if fragment == "__unproven__":
        return TargetProof.UNPROVEN
    if family == "WRITE":
        if predicate in {"aplique", "apply"}:
            if normalize_target_anchor_identity(fragment).identity_unresolved:
                return TargetProof.UNPROVEN
            return TargetProof.PROVEN
        identity = normalize_target_anchor_identity(fragment)
        return TargetProof.PROVEN if identity.canonical is not None and not identity.identity_unresolved else TargetProof.UNPROVEN
    if family == "PROCESS":
        if _begins_process_object_head(fragment):
            return TargetProof.PROVEN
        if normalize_target_anchor_identity(fragment).canonical is not None:
            return TargetProof.PROVEN
        return TargetProof.UNPROVEN
    if family == "NETWORK":
        if predicate in {"pesquise na web", "busque na web", "search the web", "browse the web"}:
            return TargetProof.PROVEN
        token = fragment
        if " " not in token and ("@" in token or token.startswith(("http://", "https://"))):
            return TargetProof.PROVEN
        identity = normalize_target_anchor_identity(token)
        return TargetProof.PROVEN if identity.canonical is not None and not identity.identity_unresolved else TargetProof.UNPROVEN
    if family == "MEMORY":
        return TargetProof.PROVEN
    if family == "PACKAGE_INSTALL":
        excluded = {
            "a", "an", "the", "um", "uma", "o", "os", "as",
            "package", "pacote", "dependency", "dependÃªncia", "dependencia",
        }
        return (
            TargetProof.PROVEN
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", fragment)
            and fragment.casefold() not in GENERIC_TARGET_NOUNS
            and fragment.casefold() not in excluded
            else TargetProof.UNPROVEN
        )
    return TargetProof.UNPROVEN


class DirectOperationalTargetGuard:
    @staticmethod
    def classify(analysis_or_clause: OperationalAnalysis | str) -> TargetProof:
        return direct_operational_target_proof(analysis_or_clause)


def _start_read(value: str) -> tuple[str, str] | None:
    return _starts_lexeme(value, tuple(sorted(READ_LEXEMES, key=len, reverse=True)))


def classify_read_request(clause: str) -> ReadClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    operational = analyze_operational_clause(normalized)
    if operational.classification in {OperationalClassification.DIRECT, OperationalClassification.NEGATED, OperationalClassification.HYPOTHETICAL, OperationalClassification.CONTEXTUAL}:
        return ReadClassification.OPERATIONAL
    if _start_plan(stripped) is not None:
        return ReadClassification.PROPOSAL
    read = _start_read(stripped)
    if read is None:
        if _plain_effect_occurrence(normalized):
            return ReadClassification.META
        return ReadClassification.UNKNOWN
    _, complement = read
    if _is_contextual_complement(complement):
        return ReadClassification.CONTEXTUAL
    return ReadClassification.DIRECT_READ if complement.strip() else ReadClassification.UNKNOWN


class DirectReadRequestGuard:
    @staticmethod
    def classify(clause: str) -> ReadClassification:
        return classify_read_request(clause)


def _start_plan(value: str) -> tuple[str, str] | None:
    return _starts_lexeme(value, tuple(sorted(PLAN_LEXEMES, key=len, reverse=True)))


def classify_plan_request(clause: str) -> PlanClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    operational = analyze_operational_clause(normalized)
    if operational.classification in {OperationalClassification.DIRECT, OperationalClassification.NEGATED, OperationalClassification.HYPOTHETICAL, OperationalClassification.CONTEXTUAL}:
        return PlanClassification.OPERATIONAL
    found = _start_plan(stripped)
    if found is None:
        if _plain_effect_occurrence(normalized):
            return PlanClassification.META
        return PlanClassification.UNKNOWN
    _, complement = found
    if not complement.strip() or _is_contextual_complement(complement):
        return PlanClassification.CONTEXTUAL
    return PlanClassification.DIRECT_PLAN


class DirectPlanRequestGuard:
    @staticmethod
    def classify(clause: str) -> PlanClassification:
        return classify_plan_request(clause)


def local_effect_conflict(clause: str) -> LocalConflictClassification:
    """Scan every closed separator tail through the negative parser.

    A later same-family restriction remains a veto after unrelated families.
    The scan is exhausted only after all candidate tails are examined.
    """
    normalized = normalize_clause_for_guard(clause)
    primary = analyze_operational_clause(normalized)
    if primary.classification is not OperationalClassification.DIRECT or primary.family is None:
        return LocalConflictClassification.CLEAR
    remainder_start = normalized.find(primary.predicate or "") + len(primary.predicate or "")
    remainder = normalized[remainder_start:]
    for _separator_start, separator_end in _all_marker_occurrences(remainder, LOCAL_CONFLICT_SEPARATORS):
        restriction = parse_negative_restriction(remainder[separator_end:].strip())
        if restriction is not None and restriction.family in {"ALL", primary.family}:
            return LocalConflictClassification.CONFLICT
    return LocalConflictClassification.CLEAR


class LocalEffectConflictGuard:
    @staticmethod
    def classify(clause: str) -> LocalConflictClassification:
        return local_effect_conflict(clause)


def mixed_intent_tail(clause: str) -> MixedIntentClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    read = classify_read_request(normalized)
    plan = classify_plan_request(normalized)
    if read is not ReadClassification.DIRECT_READ and plan is not PlanClassification.DIRECT_PLAN:
        return MixedIntentClassification.CLEAR
    found = _start_read(stripped) or _start_plan(stripped)
    if found is None:
        return MixedIntentClassification.CLEAR
    remainder = found[1]
    for _marker_start, marker_end in _all_marker_occurrences(remainder, MIXED_INTENT_MARKERS):
        tail = remainder[marker_end:]
        if _positive_effect(tail) is not None:
            return MixedIntentClassification.MIXED_EFFECT
    return MixedIntentClassification.CLEAR


class MixedIntentTailGuard:
    @staticmethod
    def classify(clause: str) -> MixedIntentClassification:
        return mixed_intent_tail(clause)


def _global_restriction(value: str) -> bool:
    if value in GLOBAL_RESTRICTIONS:
        return True
    for _family, cores in GLOBAL_FAMILY_CORES.items():
        for core in cores:
            if value == core or value.startswith(core + " "):
                return True
    return False


def parse_negative_restriction(clause: str) -> NegativeRestriction | None:
    normalized, _ = strip_one_request_prefix(clause)
    if _global_restriction(normalized):
        for family, cores in GLOBAL_FAMILY_CORES.items():
            for core in cores:
                if normalized == core or normalized.startswith(core + " "):
                    return NegativeRestriction(family, "FAMILY_ALL", normalized[len(core) :].strip())
        return NegativeRestriction("ALL", "FAMILY_ALL", normalized)
    negative = _negative_effect(normalized)
    if negative is None:
        return None
    family, predicate_or_scope, complement = negative
    if predicate_or_scope in {"FAMILY_ALL", "TARGET_SCOPED"}:
        return NegativeRestriction(family, predicate_or_scope, complement)
    return NegativeRestriction(family, "TARGET_SCOPED", complement)


def _target_for_relation(analysis: OperationalAnalysis | NegativeRestriction) -> TargetAnchorIdentity:
    value: str | None
    if isinstance(analysis, NegativeRestriction):
        value = analysis.complement
    else:
        value = _target_fragment(analysis)
        if value is None:
            return TargetAnchorIdentity("AMBIGUOUS", None, True)
    return normalize_target_anchor_identity(value)


def cross_clause_effect_conflict(subject: str) -> CrossClauseRelation:
    clauses = scan_clause_spans(subject)
    positives: list[OperationalAnalysis] = []
    negatives: list[NegativeRestriction] = []
    for clause in clauses:
        analysis = analyze_operational_clause(clause.text)
        if analysis.classification is OperationalClassification.DIRECT and analysis.family is not None:
            positives.append(analysis)
        restriction = parse_negative_restriction(clause.text)
        if restriction is not None:
            negatives.append(restriction)
    if not positives or not negatives:
        return CrossClauseRelation.CLEAR
    if any(
        restriction.family == "ALL" and restriction.scope == "FAMILY_ALL"
        for restriction in negatives
    ):
        return CrossClauseRelation.GLOBAL_CONFLICT

    relevant = [
        restriction
        for restriction in negatives
        if restriction.family == "ALL"
        or any(positive.family == restriction.family for positive in positives)
    ]
    if not relevant:
        return CrossClauseRelation.CLEAR

    saw_independent = False
    for restriction in relevant:
        matching_positives = [
            positive
            for positive in positives
            if restriction.family == "ALL" or positive.family == restriction.family
        ]
        for positive in matching_positives:
            if restriction.scope == "FAMILY_ALL":
                return CrossClauseRelation.FAMILY_CONFLICT

            negative_identity = _target_for_relation(restriction)
            positive_identity = _target_for_relation(positive)
            if (
                negative_identity.canonical is not None
                and negative_identity.canonical == positive_identity.canonical
                and not negative_identity.identity_unresolved
                and not positive_identity.identity_unresolved
            ):
                return CrossClauseRelation.SAME_TARGET_CONFLICT
            if negative_identity.identity_unresolved or positive_identity.identity_unresolved:
                return CrossClauseRelation.UNKNOWN_RELATION_CONFLICT
            if negative_identity.identity_class in {"URL", "EMAIL"} or positive_identity.identity_class in {"URL", "EMAIL"}:
                return CrossClauseRelation.UNKNOWN_RELATION_CONFLICT

            broad = {
                strip_target_surrounding_punctuation(token)
                for token in restriction.complement.split()
            } & BROAD_TARGET_QUANTIFIERS
            if (
                not broad
                and negative_identity.canonical
                and positive_identity.canonical
                and negative_identity.identity_class in {"LOCAL_PATH", "IDENTIFIER"}
                and positive_identity.identity_class in {"LOCAL_PATH", "IDENTIFIER"}
            ):
                saw_independent = True
                continue
            return CrossClauseRelation.UNKNOWN_RELATION_CONFLICT
    return CrossClauseRelation.INDEPENDENT if saw_independent else CrossClauseRelation.CLEAR


class CrossClauseEffectConflictGuard:
    @staticmethod
    def classify(subject: str) -> CrossClauseRelation:
        return cross_clause_effect_conflict(subject)


def evidence_is_current_plain(subject: str, evidence: str) -> bool:
    return evidence_is_plain_exact(subject, evidence)


def evidence_is_within_one_clause(subject: str, evidence: str) -> bool:
    for start, end in plain_occurrences(subject, evidence, limit=32):
        for clause in scan_clause_spans(subject):
            if clause.start <= start and end <= clause.end:
                return True
    return False


DirectOperationalResult = OperationalClassification
DirectReadResult = ReadClassification
DirectPlanResult = PlanClassification
TargetGuardResult = TargetProof

__all__ = [
    "CrossClauseEffectConflictGuard",
    "CrossClauseRelation",
    "DirectOperationalRequestGuard",
    "DirectOperationalResult",
    "DirectOperationalTargetGuard",
    "DirectPlanRequestGuard",
    "DirectPlanResult",
    "DirectReadRequestGuard",
    "DirectReadResult",
    "LocalConflictClassification",
    "LocalEffectConflictGuard",
    "MixedIntentClassification",
    "MixedIntentTailGuard",
    "OperationalAnalysis",
    "OperationalClassification",
    "PlanClassification",
    "ReadClassification",
    "TargetAnchorIdentity",
    "TargetGuardResult",
    "TargetProof",
    "analyze_operational_clause",
    "classify_operational_request",
    "classify_plan_request",
    "classify_read_request",
    "cross_clause_effect_conflict",
    "direct_operational_target_proof",
    "evidence_is_current_plain",
    "evidence_is_within_one_clause",
    "local_effect_conflict",
    "mixed_intent_tail",
    "normalize_target_anchor_identity",
    "parse_negative_restriction",
    "strip_one_request_prefix",
]
