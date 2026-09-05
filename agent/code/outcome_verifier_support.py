"""Pure verdict projection helpers for the bounded outcome verifier."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.code.outcome_contracts import CodeOutcomeVerdict, CodeOutcomeVerification
from agent.code.outcome_evidence import CodeEvidenceManifest
from agent.code.outcome_proposal import CodeProposalKind, CodeProposalReasonCode
from agent.resources.contracts import normalize_resource_id
from agent.runtime.outcome_contracts import MAX_VERIFIER_EVIDENCE_IDS


def _insufficient(
    message: str,
    evidence_ids: Sequence[str] = (),
) -> CodeOutcomeVerification:
    return CodeOutcomeVerification(
        CodeOutcomeVerdict.INSUFFICIENT,
        message,
        evidence_ids=tuple(evidence_ids),
        failure_code="CODE_VERIFICATION_INSUFFICIENT",
    )


def _parse_verdict_envelope(
    parsed: Any,
) -> tuple[CodeOutcomeVerdict, str, list[str]] | CodeOutcomeVerification:
    if not isinstance(parsed, Mapping):
        return _insufficient("verdict invÃ¡lido")
    try:
        verdict = CodeOutcomeVerdict(parsed.get("verdict"))
    except (TypeError, ValueError):
        return _insufficient("verdict fora do enum")
    reason = parsed.get("reason")
    ids = parsed.get("evidence_ids")
    if not isinstance(reason, str) or len(reason) > 1_500:
        return _insufficient("reason invÃ¡lido")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return _insufficient("evidence_ids invÃ¡lidos")
    if len(ids) > MAX_VERIFIER_EVIDENCE_IDS or len(set(ids)) != len(ids):
        return _insufficient("evidence_ids excedem o limite")
    if verdict is not CodeOutcomeVerdict.INSUFFICIENT and not (
        1 <= len(ids) <= MAX_VERIFIER_EVIDENCE_IDS
    ):
        return _insufficient("verdict exige evidÃªncia citada")
    return verdict, reason, ids


def _validate_evidence_ids(
    verdict: CodeOutcomeVerdict,
    ids: Sequence[str],
    manifest: CodeEvidenceManifest,
    additional_evidence_ids: Sequence[str],
) -> CodeOutcomeVerification | None:
    known_ids = set(manifest.by_id) | set(additional_evidence_ids)
    if any(item not in known_ids for item in ids):
        return _insufficient("evidence_id nÃ£o pertence ao manifesto")
    return None


def _validate_runtime_bound_evidence(
    verdict: CodeOutcomeVerdict,
    decision_kind: CodeProposalKind,
    manifest: CodeEvidenceManifest,
    ids: Sequence[str],
    target_paths: Sequence[str],
    reason_code: CodeProposalReasonCode | None,
) -> CodeOutcomeVerification | None:
    if verdict is not CodeOutcomeVerdict.SUPPORTED or decision_kind not in {
        CodeProposalKind.NO_CHANGE,
        CodeProposalKind.NEEDS_INPUT,
    }:
        return None
    if any(item not in manifest.by_id for item in ids):
        return _insufficient("NO_CHANGE so pode citar evidencia de arquivo runtime-bound", ids)
    cited_records = tuple(manifest.by_id[item] for item in ids)
    if any(
        record.kind == "FILE_CONTENT"
        and (not record.complete or record.truncated or not record.text_lossless)
        for record in cited_records
    ):
        return _insufficient("evidencia FILE_CONTENT citada nao e completa/lossless", ids)
    analysis_ids = tuple(
        record.evidence_id
        for record in cited_records
        if record.kind == "ANALYSIS_FACT"
    )
    if analysis_ids and not manifest.required_file_content_ids(analysis_ids):
        return _insufficient("ANALYSIS_FACT citado sem cadeia FILE_CONTENT completa", ids)
    if not manifest.supports_no_change(ids, target_paths=target_paths):
        return _insufficient("evidÃªncia decisiva incompleta ou fora do target", ids)
    if decision_kind is CodeProposalKind.NEEDS_INPUT and not any(
        manifest.by_id[item].kind == "USER_LITERAL" for item in ids
    ):
        return _insufficient("clarificaÃ§Ã£o sem USER_LITERAL runtime-bound", ids)
    return None


def _validate_target_choice(
    verdict: CodeOutcomeVerdict,
    decision_kind: CodeProposalKind,
    reason_code: CodeProposalReasonCode | None,
    manifest: CodeEvidenceManifest,
    ids: Sequence[str],
    target_paths: Sequence[str],
) -> CodeOutcomeVerification | None:
    if (
        verdict is not CodeOutcomeVerdict.SUPPORTED
        or decision_kind is not CodeProposalKind.NEEDS_INPUT
        or reason_code is not CodeProposalReasonCode.TARGET_CHOICE_REQUIRED
    ):
        return None
    grounding_ids = tuple(
        record.evidence_id
        for record in manifest.by_id.values()
        if record.evidence_id in ids
        and record.kind in {"FILE_CONTENT", "ANALYSIS_FACT"}
    )
    leaves = manifest.required_file_content_ids(grounding_ids)
    grounded_paths = {
        normalize_resource_id(manifest.by_id[item].path)
        for item in leaves
        if manifest.by_id[item].path
        and normalize_resource_id(manifest.by_id[item].path) != "*"
    }
    requested_paths = {
        normalize_resource_id(item)
        for item in target_paths
        if normalize_resource_id(item) != "*"
    }
    if requested_paths:
        grounded_paths &= requested_paths
    if len(grounded_paths) < 2:
        return _insufficient("TARGET_CHOICE_REQUIRED exige dois alvos runtime-grounded distintos", ids)
    return None


def project_verdict(
    parsed: Any,
    manifest: CodeEvidenceManifest,
    decision_kind: CodeProposalKind,
    *,
    target_paths: Sequence[str],
    reason_code: CodeProposalReasonCode | None,
    additional_evidence_ids: Sequence[str],
) -> CodeOutcomeVerification:
    envelope = _parse_verdict_envelope(parsed)
    if isinstance(envelope, CodeOutcomeVerification):
        return envelope
    verdict, reason, ids = envelope
    invalid_ids = _validate_evidence_ids(
        verdict,
        ids,
        manifest,
        additional_evidence_ids,
    )
    if invalid_ids is not None:
        return invalid_ids
    invalid_evidence = _validate_runtime_bound_evidence(
        verdict,
        decision_kind,
        manifest,
        ids,
        target_paths,
        reason_code,
    )
    if invalid_evidence is not None:
        return invalid_evidence
    invalid_target_choice = _validate_target_choice(
        verdict,
        decision_kind,
        reason_code,
        manifest,
        ids,
        target_paths,
    )
    if invalid_target_choice is not None:
        return invalid_target_choice
    return CodeOutcomeVerification(verdict, reason, tuple(ids))

def context_limit(context: Any) -> int | None:
    metadata = getattr(context, "metadata", {})
    value = metadata.get("context_limit") if isinstance(metadata, Mapping) else None
    if isinstance(value, int) and value > 0:
        return value
    hardware = getattr(context, "hardware_profile", None)
    value = getattr(hardware, "context_limit", None)
    return value if isinstance(value, int) and value > 0 else None


__all__ = ["context_limit", "project_verdict"]
