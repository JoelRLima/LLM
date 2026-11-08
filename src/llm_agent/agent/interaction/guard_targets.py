"""Identity and proof rules for operational targets."""

from __future__ import annotations

import re

from .evidence import (
    normalize_clause_for_guard,
    strip_target_surrounding_punctuation,
)
from .guard_operational import analyze_operational_clause
from .guard_types import (
    OperationalAnalysis,
    OperationalClassification,
    TargetAnchorIdentity,
    TargetProof,
)
from .lexicon import (
    DEICTIC_TARGETS,
    GENERIC_TARGET_NOUNS,
    PROCESS_OBJECT_HEADS,
)


def _anchor_candidate(value: str) -> str | None:
    raw = value.strip()
    candidate = raw if raw.startswith(("./", ".\\")) else strip_target_surrounding_punctuation(raw)
    lowered = candidate.casefold()
    for prefix in (
        "o arquivo ",
        "a arquivo ",
        "os arquivos ",
        "as arquivos ",
        "arquivo ",
        "the file ",
        "the files ",
        "file ",
        "o módulo ",
        "o modulo ",
        "the module ",
    ):
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix) :].strip()
            lowered = candidate.casefold()
            break
    return candidate or None


def normalize_target_anchor_identity(value: str) -> TargetAnchorIdentity:
    candidate = _anchor_candidate(value)
    if _is_ambiguous_anchor(candidate):
        return TargetAnchorIdentity("AMBIGUOUS", None, True)
    assert candidate is not None
    lowered = candidate.casefold()
    if lowered.startswith(("http://", "https://")):
        return TargetAnchorIdentity("URL", lowered, False)
    if "@" in candidate and " " not in candidate:
        return TargetAnchorIdentity("EMAIL", lowered, False)
    if any(char in candidate for char in (".", "/", "\\", ":")):
        return _local_path_identity(lowered)
    if "_" in candidate or "-" in candidate:
        return TargetAnchorIdentity("IDENTIFIER", lowered, False)
    return TargetAnchorIdentity("AMBIGUOUS", None, True)


def _is_ambiguous_anchor(candidate: str | None) -> bool:
    return (
        candidate is None
        or " " in candidate
        or candidate.casefold() in GENERIC_TARGET_NOUNS
        or candidate.casefold() in DEICTIC_TARGETS
    )


def _local_path_identity(lowered: str) -> TargetAnchorIdentity:
    absolute = lowered.startswith("/")
    result: list[str] = []
    unresolved = False
    for piece in lowered.replace("\\", "/").split("/"):
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


def _target_fragment(analysis: OperationalAnalysis) -> str | None:
    raw_complement = analysis.complement.strip()
    complement = (
        raw_complement
        if raw_complement.startswith(("./", ".\\"))
        else strip_target_surrounding_punctuation(raw_complement)
    )
    family = analysis.family
    predicate = analysis.predicate or ""
    if family == "WRITE" and predicate in {"aplique", "apply"}:
        exact = {
            "o patch",
            "patch",
            "as alterações",
            "as alteracoes",
            "as mudanças",
            "as mudancas",
            "the patch",
            "the changes",
            "changes",
        }
        if complement in exact:
            return None
        for prefix in (
            "o patch em ",
            "patch em ",
            "o patch no ",
            "patch no ",
            "o patch na ",
            "patch na ",
            "the patch to ",
            "patch to ",
        ):
            if complement.startswith(prefix):
                return complement[len(prefix) :]
        return "__unproven__"
    if family == "WRITE" and predicate in {"commite", "faça commit", "faca commit", "commit"}:
        if complement in {
            "as alterações",
            "as alteracoes",
            "as mudanças",
            "as mudancas",
            "o commit",
            "commit",
            "the changes",
            "changes",
            "the commit",
        }:
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
    complement = normalize_clause_for_guard(analysis.complement)
    if not complement:
        return TargetProof.UNPROVEN
    fragment = _target_fragment(analysis)
    return _prove_target_fragment(analysis.family, analysis.predicate or "", fragment)


def _prove_target_fragment(family: str | None, predicate: str, fragment: str | None) -> TargetProof:
    if fragment is None:
        return _proof_without_fragment(family, predicate)
    if fragment == "__unproven__":
        return TargetProof.UNPROVEN
    if family == "WRITE":
        return _prove_write_target(predicate, fragment)
    if family == "PROCESS":
        return _prove_process_target(fragment)
    if family == "NETWORK":
        return _prove_network_target(predicate, fragment)
    if family == "MEMORY":
        return TargetProof.PROVEN
    if family == "PACKAGE_INSTALL":
        return _prove_package_target(fragment)
    return TargetProof.UNPROVEN


def _proof_without_fragment(family: str | None, predicate: str) -> TargetProof:
    if family == "NETWORK" and predicate in _WEB_SEARCH_PREDICATES:
        return TargetProof.PROVEN
    if family == "WRITE" and predicate in _TARGETLESS_WRITE_PREDICATES:
        return TargetProof.PROVEN
    return TargetProof.UNPROVEN


def _prove_write_target(predicate: str, fragment: str) -> TargetProof:
    identity = normalize_target_anchor_identity(fragment)
    if predicate in {"aplique", "apply"} and identity.identity_unresolved:
        return TargetProof.UNPROVEN
    return _proof_from_identity(identity)


def _prove_process_target(fragment: str) -> TargetProof:
    if _begins_process_object_head(fragment):
        return TargetProof.PROVEN
    return _proof_from_identity(normalize_target_anchor_identity(fragment))


def _prove_network_target(predicate: str, fragment: str) -> TargetProof:
    if predicate in _WEB_SEARCH_PREDICATES:
        return TargetProof.PROVEN
    if " " not in fragment and ("@" in fragment or fragment.startswith(("http://", "https://"))):
        return TargetProof.PROVEN
    return _proof_from_identity(normalize_target_anchor_identity(fragment))


def _proof_from_identity(identity: TargetAnchorIdentity) -> TargetProof:
    return (
        TargetProof.PROVEN
        if identity.canonical is not None and not identity.identity_unresolved
        else TargetProof.UNPROVEN
    )


def _prove_package_target(fragment: str) -> TargetProof:
    excluded = {
        "a",
        "an",
        "the",
        "um",
        "uma",
        "o",
        "os",
        "as",
        "package",
        "pacote",
        "dependency",
        "depend????ncia",
        "dependencia",
    }
    valid = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", fragment)
    return (
        TargetProof.PROVEN
        if valid and fragment.casefold() not in GENERIC_TARGET_NOUNS and fragment.casefold() not in excluded
        else TargetProof.UNPROVEN
    )


_WEB_SEARCH_PREDICATES = frozenset({"pesquise na web", "busque na web", "search the web", "browse the web"})
_TARGETLESS_WRITE_PREDICATES = frozenset({"aplique", "apply", "commite", "fa??a commit", "faca commit", "commit"})


class DirectOperationalTargetGuard:
    @staticmethod
    def classify(analysis_or_clause: OperationalAnalysis | str) -> TargetProof:
        return direct_operational_target_proof(analysis_or_clause)
