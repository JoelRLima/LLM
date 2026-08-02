"""Avaliação determinística de risco e confiança de ChangeSets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol, Sequence

from agent.approval import ApprovalDecision
from agent.code.changes import ChangeKind, ChangePreview, ChangeSet, FileChange
from agent.code.path_safety import (
    resolve_workspace_path,
    workspace_relative_path,
)


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
    ) -> bool | ApprovalDecision:
        ...


@dataclass(frozen=True)
class ChangeApprovalPolicy:
    auto_apply_min_confidence: float = 0.85
    max_auto_files: int = 2
    require_target_alignment: bool = True

    @staticmethod
    def _normalized_targets(
        base: Path,
        requested_targets: Sequence[str],
    ) -> tuple[set[str], bool]:
        targets: set[str] = set()
        invalid = False
        for target in requested_targets:
            if not target.strip():
                continue
            try:
                targets.add(workspace_relative_path(base, target))
            except (OSError, ValueError):
                invalid = True
        return targets, invalid

    @staticmethod
    def _change_path_facts(
        base: Path,
        path: str,
    ) -> tuple[str, bool, bool]:
        try:
            resolved = resolve_workspace_path(base, path)
        except (OSError, ValueError):
            return Path(path).as_posix(), False, False
        return (
            resolved.relative_to(base).as_posix(),
            resolved.is_file(),
            True,
        )

    @staticmethod
    def _is_aligned(
        normalized: str,
        targets: set[str],
        *,
        safe_path: bool,
        invalid_targets: bool,
    ) -> bool:
        return safe_path and not invalid_targets and (
            not targets
            or any(
                target == "."
                or normalized == target
                or normalized.startswith(target.rstrip("/") + "/")
                or target.startswith(normalized.rstrip("/") + "/")
                for target in targets
            )
        )

    @staticmethod
    def _destination_is_safe(base: Path, destination: str | None) -> bool:
        if destination is None:
            return True
        try:
            workspace_relative_path(base, destination)
        except (OSError, ValueError):
            return False
        return True

    def _assess_change(
        self,
        base: Path,
        change: FileChange,
        targets: set[str],
        *,
        invalid_targets: bool,
    ) -> tuple[float, list[str]]:
        penalty = 0.0
        reasons: list[str] = []
        normalized, existing, safe_path = self._change_path_facts(
            base,
            change.path,
        )
        if not safe_path:
            penalty += 0.5
            reasons.append(f"'{normalized}' está fora do workspace.")
        aligned = self._is_aligned(
            normalized,
            targets,
            safe_path=safe_path,
            invalid_targets=invalid_targets,
        )
        if self.require_target_alignment and not aligned:
            penalty += 0.25
            reasons.append(f"'{normalized}' não foi declarado em targets.")
        if not self._destination_is_safe(base, change.destination_path):
            penalty += 0.5
            reasons.append(f"'{change.destination_path}' está fora do workspace.")
        if existing and change.kind in {
            ChangeKind.MODIFY,
            ChangeKind.EDIT,
            ChangeKind.DELETE,
            ChangeKind.MOVE,
        } and not change.base_hash:
            penalty += 0.15
            reasons.append(f"'{normalized}' não possui base_hash.")
        if change.kind == ChangeKind.MODIFY:
            penalty += 0.1
            reasons.append(
                f"'{normalized}' será regenerado por inteiro; prefira edit."
            )
        if change.kind == ChangeKind.EDIT and any(
            edit.expected_text is None for edit in change.edits
        ):
            penalty += 0.05
            reasons.append(
                f"Edit de '{normalized}' não usa expected_text em todas as faixas."
            )
        if change.kind in {ChangeKind.DELETE, ChangeKind.MOVE}:
            penalty += 0.1
            reasons.append(
                f"'{normalized}' usa operação destrutiva {change.kind.value}."
            )
        if change.content is not None and len(change.content) > 40_000:
            penalty += 0.15
            reasons.append(f"Conteúdo integral de '{normalized}' é muito grande.")
        return penalty, reasons

    def assess(
        self,
        root: str | Path,
        change_set: ChangeSet,
        requested_targets: Sequence[str] = (),
    ) -> ProposalAssessment:
        base = Path(root).resolve()
        targets, invalid_targets = self._normalized_targets(
            base,
            requested_targets,
        )
        confidence = 1.0
        reasons: list[str] = []
        if invalid_targets:
            confidence -= 0.4
            reasons.append("Um ou mais targets estão fora do workspace.")

        if len(change_set.changes) > self.max_auto_files:
            confidence -= min(0.3, 0.08 * (len(change_set.changes) - self.max_auto_files))
            reasons.append(
                f"ChangeSet altera {len(change_set.changes)} arquivos; auto apply limita "
                f"a {self.max_auto_files}."
            )

        for change in change_set.changes:
            penalty, change_reasons = self._assess_change(
                base,
                change,
                targets,
                invalid_targets=invalid_targets,
            )
            confidence -= penalty
            reasons.extend(change_reasons)

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
