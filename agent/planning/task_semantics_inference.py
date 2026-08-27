"""Objective-only effect and obligation inference for task semantics."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from agent.planning.task_semantics_authority import admit_effect_authority
from agent.planning.task_semantics_effect_inference import (
    _READ_VERBS,
    _RESPONSE_VERBS,
    PathRole,
    _classify_path_roles,
    _clause_intents,
    _file_targets,
    _intent_clauses,
    _is_negated,
    _repair_mojibake,
    _search_spec,
    _tokens,
)
from agent.planning.task_semantics_proposal import is_proposal_only_objective
from agent.planning.task_semantics_types import (
    EffectSemantics,
    PredicateEvidence,
    PredicateResolutionState,
    TaskObligation,
    TaskSemanticsError,
    _eligible_evidence_ref,
    _normalize_text,
)


def infer_effect_semantics(objective: str) -> EffectSemantics:
    """Infer requested and prohibited effects from user intent only."""
    if not isinstance(objective, str):
        raise TaskSemanticsError("objetivo deve ser textual")
    intents = list(_clause_intents(objective))
    requested: list[str] = []
    prohibited: list[str] = []
    for item in intents:
        target = prohibited if item.polarity == "prohibited" else requested
        if item.effect not in target:
            target.append(item.effect)
    return EffectSemantics(
        tuple(requested),
        tuple(prohibited),
        tuple(intents),
        proposal_only=is_proposal_only_objective(objective),
    )


def infer_requested_effects(objective: str) -> tuple[str, ...]:
    return admit_effect_authority(objective).requested_effects


def infer_prohibited_effects(objective: str) -> tuple[str, ...]:
    authority = admit_effect_authority(objective)
    return tuple(dict.fromkeys(item.effect for item in authority.constraint_intents))


def inferred_obligations(objective: str, effects: EffectSemantics) -> list[TaskObligation]:
    admitted_effects = admit_effect_authority(objective).requested_effects
    tokens = _tokens(objective)
    paths = _file_targets(objective)
    obligations: list[TaskObligation] = []
    # A direct content question is already a user-authored read request. It
    # must not be downgraded to an implicit safety obligation that requires a
    # separate runtime admission token before the normal read plan can run.
    normalized_objective = _normalize_text(_repair_mojibake(objective))
    has_content_question = bool(
        paths
        and "?" in objective
        and re.search(
            r"\b(?:qual|what)\b[^?]*(?:conteudo|content)\b",
            normalized_objective,
        )
    )
    has_explicit_read = any(
        token in _READ_VERBS and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    ) or has_content_question
    has_compare_reads = any(
        token == "compare" and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    ) and len(paths) >= 2
    has_explicit_analyze = any(
        token in {"analyze", "analise", "analisar"} and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )
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

    implicit_paths, implicit_query = _implicit_workspace_evidence(objective, tokens, paths)
    if not has_explicit_read and not has_compare_reads:
        obligations.extend(
            TaskObligation(
                id=f"workspace:read:{index + 1}",
                kind="read",
                target=path,
                description=f"Obter evidência fresca do workspace para {path}.",
            )
            for index, path in enumerate(implicit_paths)
        )
        if implicit_query is not None:
            obligations.append(
                TaskObligation(
                    id="workspace:search",
                    kind="search",
                    query=implicit_query,
                    description="Buscar evidência fresca no workspace para responder à pergunta.",
                )
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
    if has_explicit_analyze:
        obligations.extend(
            TaskObligation(
                id=f"analyze:{index + 1}",
                kind="analyze",
                target=path,
                description=f"Analisar o arquivo {path} conforme o objetivo original.",
            )
            for index, path in enumerate(paths)
        )
    obligations.extend(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in admitted_effects
    )
    return obligations


def predicate_resolutions_from_observations(
    objective: str,
    observations: Sequence[Mapping[str, object]] | None,
) -> dict[str, PredicateEvidence]:
    """Derive only deterministic predicate facts from trusted observations.

    Model text and the condition strings carried by ``EffectIntent`` are not
    consulted as evidence.  A bounded predicate is resolved only when a
    completed observation names the predicate's concrete target and returns
    textual data.
    """

    intents = infer_effect_semantics(objective).intents
    predicate_ids = tuple(
        dict.fromkeys(
            item.predicate_id
            for item in intents
            if item.predicate_id is not None
        )
    )
    resolved: dict[str, PredicateEvidence] = {}
    for index, observation in enumerate(observations or (), start=1):
        for predicate_id in predicate_ids:
            if predicate_id is None:
                continue
            evidence = predicate_evidence_from_observation(
                predicate_id, observation, evidence_ref=index
            )
            if evidence is not None:
                resolved[predicate_id] = evidence
    return resolved


def predicate_evidence_from_observation(
    predicate_id: str,
    observation: Mapping[str, object],
    *,
    evidence_ref: int | str,
) -> PredicateEvidence | None:
    """Admit and evaluate one predicate from canonical observation evidence."""

    try:
        ref = _eligible_evidence_ref(evidence_ref)
    except (TaskSemanticsError, TypeError, ValueError):
        return None
    if not isinstance(observation, Mapping):
        return None
    result = observation.get("result")
    args = observation.get("args")
    if not isinstance(result, Mapping) or not isinstance(args, Mapping):
        return None
    if result.get("executed") is not True or str(result.get("status") or "").casefold() not in {
        "succeeded",
        "success",
    }:
        return None
    data = result.get("data")
    if not isinstance(data, str) or not _has_complete_observation(result):
        return None
    target = next(
        (
            value
            for key in ("file_path", "path", "target", "file")
            for value in (args.get(key),)
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    parts = predicate_id.split("|", 2)
    if target is None or len(parts) != 3:
        return None
    target_identity = _normalize_text(target).replace("\\", "/").strip("/")
    if parts[0] != target_identity:
        return None
    _target, operator, literal = parts
    if operator == "contains":
        value = literal in data.casefold()
    elif operator == "equals":
        value = data.casefold() == literal.casefold()
    else:
        return None
    return PredicateEvidence(
        predicate_id,
        PredicateResolutionState.TRUE if value else PredicateResolutionState.FALSE,
        ref,
        "workspace_observation",
    )


def _has_complete_observation(result: Mapping[str, object]) -> bool:
    """Require an exact text artifact when a producer exposes completeness."""

    artifacts = result.get("artifacts")
    if artifacts is None:
        # Small deterministic/unit observations may omit artifact metadata;
        # their explicit textual result remains admissible for compatibility.
        return True
    if not isinstance(artifacts, (list, tuple)):
        return False
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("metadata"), Mapping)
        and item["metadata"].get("complete") is True
        for item in artifacts
    )


def _implicit_workspace_evidence(
    objective: str, tokens: Sequence[str], paths: tuple[str, ...]
) -> tuple[tuple[str, ...], str | None]:
    """Detect content-dependent path roles without a property-word catalog.

    The request frame (question/interrogative or a bounded response verb) and
    the path's structural role decide whether a read is needed.  The nouns
    being requested are intentionally opaque, so an unseen field name cannot
    silently avoid grounding.  Conceptual questions remain excluded.
    """

    normalized = _normalize_text(_repair_mojibake(objective))
    conceptual = bool(
        re.search(
            r"\b(?:o que e(?:\s+(?:um|uma|o|a|the))?\s+[\w./-]+\.[a-z0-9]{1,16}|"
            r"what is\s+[\w./-]+\.[a-z0-9]{1,16}(?:\s+for)?|para que serve|"
            r"explique o conceito de|explain the concept of|conceito de|concept of)\b",
            normalized,
        )
    )
    token_set = set(tokens)
    request_verbs = {
        "diga", "dizer", "informe", "informar", "list", "liste", "mostrar",
        "mostre", "report", "relate", "show", "tell",
    }
    interrogatives = {
        "como", "how", "onde", "where", "qual", "quais", "quanto", "quantos",
        "quantas", "what", "which",
    }
    source_transform = bool(token_set & _RESPONSE_VERBS)
    has_request_frame = bool(token_set & request_verbs)
    has_interrogative_frame = bool(token_set & interrogatives) and (
        objective.rstrip().endswith("?") or has_request_frame or "o que" in normalized
    )
    has_what_frame = "o que" in normalized and objective.rstrip().endswith("?")
    file_report = bool(
        re.search(r"\b(?:o que diz|what does|diz|says)\b", normalized)
    )
    question = bool(
        source_transform
        or has_request_frame
        or has_interrogative_frame
        or has_what_frame
        or (paths and objective.rstrip().endswith("?"))
        or file_report
        or re.search(r"\b(?:onde|where|quantos testes|how many tests)\b", normalized)
    )
    if not question:
        return (), None
    destination_paths = {
        item.value.casefold()
        for clause in _intent_clauses(objective)
        for item in _classify_path_roles(clause, _tokens(clause))
        if item.role in {PathRole.DESTINATION, PathRole.MUTATION_TARGET}
    }
    implicit_paths: tuple[str, ...] = ()
    if paths and not conceptual:
        implicit_paths = tuple(
            path for path in paths if path.casefold() not in destination_paths
        )

    query: str | None = None
    where_match = re.search(
        r"\b(?:onde|where)\s+([a-z_][a-z0-9_.-]*)\s+(?:e|é|is)\s+(?:definid[oa]|defined)",
        normalized,
    )
    if where_match:
        query = where_match.group(1)
    else:
        count_match = re.search(
            r"\bquantos\s+testes\s+existem\s+para\s+([a-z_][a-z0-9_.-]*)",
            normalized,
        )
        if count_match:
            query = count_match.group(1)
    return implicit_paths, query


def stable_obligation_id(kind: str, description: str, effect: str | None = None) -> str:
    material = f"{kind}|{effect or ''}|{_normalize_text(description)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"requirement:{kind}:{digest}"


__all__ = (
    "infer_effect_semantics",
    "infer_prohibited_effects",
    "infer_requested_effects",
    "inferred_obligations",
    "predicate_resolutions_from_observations",
    "predicate_evidence_from_observation",
)
