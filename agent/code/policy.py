"""Avaliação determinística de risco e confiança de ChangeSets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol, Sequence

from agent.code.changes import ChangeKind, ChangePreview, ChangeSet


@dataclass(frozen=True)
class ProposalAssessment:
    confidence: float
    requires_confirmation: bool
    reasons: tuple[str, ...] = ()


class ChangeApprover(Protocol):
    def approve(
        self,
        preview: ChangePreview,
        assessment: ProposalAssessment,
    ) -> bool:
        ...


@dataclass(frozen=True)
class ChangeApprovalPolicy:
    auto_apply_min_confidence: float = 0.85
    max_auto_files: int = 2
    require_target_alignment: bool = True

    def assess(
        self,
        root: str | Path,
        change_set: ChangeSet,
        requested_targets: Sequence[str] = (),
    ) -> ProposalAssessment:
        base = Path(root).resolve()
        targets = {
            Path(target).as_posix().strip("/")
            for target in requested_targets
            if target.strip()
        }
        confidence = 1.0
        reasons: list[str] = []

        if len(change_set.changes) > self.max_auto_files:
            confidence -= min(0.3, 0.08 * (len(change_set.changes) - self.max_auto_files))
            reasons.append(
                f"ChangeSet altera {len(change_set.changes)} arquivos; auto apply limita "
                f"a {self.max_auto_files}."
            )

        for change in change_set.changes:
            normalized = Path(change.path).as_posix().strip("/")
            existing = (base / normalized).is_file()
            aligned = not targets or any(
                target == "."
                or normalized == target
                or normalized.startswith(target.rstrip("/") + "/")
                or target.startswith(normalized.rstrip("/") + "/")
                for target in targets
            )
            if self.require_target_alignment and not aligned:
                confidence -= 0.25
                reasons.append(f"'{normalized}' não foi declarado em targets.")
            if existing and change.kind in {
                ChangeKind.MODIFY,
                ChangeKind.EDIT,
                ChangeKind.DELETE,
                ChangeKind.MOVE,
            } and not change.base_hash:
                confidence -= 0.15
                reasons.append(f"'{normalized}' não possui base_hash.")
            if change.kind == ChangeKind.MODIFY:
                confidence -= 0.1
                reasons.append(f"'{normalized}' será regenerado por inteiro; prefira edit.")
            if change.kind == ChangeKind.EDIT and any(
                edit.expected_text is None for edit in change.edits
            ):
                confidence -= 0.05
                reasons.append(f"Edit de '{normalized}' não usa expected_text em todas as faixas.")
            if change.kind in {ChangeKind.DELETE, ChangeKind.MOVE}:
                confidence -= 0.1
                reasons.append(f"'{normalized}' usa operação destrutiva {change.kind.value}.")
            if change.content is not None and len(change.content) > 40_000:
                confidence -= 0.15
                reasons.append(f"Conteúdo integral de '{normalized}' é muito grande.")

        confidence = round(max(0.0, min(1.0, confidence)), 3)
        requires_confirmation = (
            confidence < self.auto_apply_min_confidence
            or len(change_set.changes) > self.max_auto_files
        )
        return ProposalAssessment(confidence, requires_confirmation, tuple(dict.fromkeys(reasons)))


def change_policy_from_config(config: Dict[str, Any]) -> ChangeApprovalPolicy:
    raw = config.get("code_policy")
    if not isinstance(raw, dict):
        return ChangeApprovalPolicy()
    confidence = raw.get("auto_apply_min_confidence", 0.85)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        confidence = 0.85
    max_files = raw.get("max_auto_files", 2)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1:
        max_files = 2
    alignment = raw.get("require_target_alignment", True)
    if not isinstance(alignment, bool):
        alignment = True
    return ChangeApprovalPolicy(
        auto_apply_min_confidence=float(confidence),
        max_auto_files=max_files,
        require_target_alignment=alignment,
    )
