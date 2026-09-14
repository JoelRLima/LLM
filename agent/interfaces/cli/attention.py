"""Broker-owned interactive approval attention for the interactive worker."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Condition, Lock
from types import MappingProxyType
from typing import Any, Callable, Mapping

from agent.approval import ApprovalDecision, ApprovalRequest, ApprovalWaitCancelled

MAX_REVIEW_DIFF_CHARS = 24_000
_REVIEW_IDENTITY_KEYS = (
    "task_id",
    "invocation_id",
    "concrete_args_sha256",
    "change_set_id",
    "proposed_diff",
    "proposed_diff_truncated",
    "proposed_diff_sha256",
)


@dataclass(frozen=True)
class AttentionIdentity:
    attention_id: int
    run_generation: int
    request_fingerprint: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class AttentionSnapshot:
    identity: AttentionIdentity
    request: ApprovalRequest


class ApprovalBroker:
    """Single unresolved attention owner; resolution is identity-addressed."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._next_attention_id = 1
        self._generation: int | None = None
        self._active: AttentionSnapshot | None = None
        self._decision: ApprovalDecision | None = None
        self._cancelled = False
        self._shutdown = False
        self._on_attention: Callable[[AttentionSnapshot], None] | None = None
        self._on_cleared: Callable[[AttentionIdentity], None] | None = None

    @staticmethod
    def fingerprint(request: ApprovalRequest) -> str:
        material = json.dumps(
            {
                "action": request.action,
                "resource": request.resource,
                "prompt": request.prompt,
                "metadata": dict(request.metadata),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def bind_generation(
        self,
        generation: int,
        *,
        on_attention: Callable[[AttentionSnapshot], None] | None = None,
        on_cleared: Callable[[AttentionIdentity], None] | None = None,
    ) -> None:
        with self._condition:
            if self._active is not None and self._generation != generation:
                self._cancelled = True
                self._condition.notify_all()
            self._generation = generation
            self._on_attention = on_attention
            self._on_cleared = on_cleared

    def current(self) -> AttentionSnapshot | None:
        with self._condition:
            return self._active

    def _prepare_request_locked(
        self,
        request: ApprovalRequest,
    ) -> tuple[AttentionSnapshot, Callable[[AttentionSnapshot], None] | None]:
        if self._shutdown:
            raise ApprovalWaitCancelled("approval broker is shut down")
        if self._generation is None:
            raise RuntimeError("approval broker generation is not bound")
        if self._active is not None:
            raise RuntimeError("second unresolved approval is forbidden")
        identity = AttentionIdentity(
            attention_id=self._next_attention_id,
            run_generation=self._generation,
            request_fingerprint=self.fingerprint(request),
            metadata=MappingProxyType({
                key: request.metadata[key]
                for key in _REVIEW_IDENTITY_KEYS
                if key in request.metadata
            }),
        )
        self._next_attention_id += 1
        snapshot = AttentionSnapshot(identity, request)
        self._active = snapshot
        self._decision = None
        self._cancelled = False
        return snapshot, self._on_attention

    def _await_decision_locked(self) -> ApprovalDecision:
        while self._decision is None and not self._cancelled and not self._shutdown:
            self._condition.wait()
        snapshot = self._active
        decision = self._decision
        cancelled = self._cancelled or self._shutdown
        clear_callback = self._on_cleared
        self._active = None
        self._decision = None
        self._cancelled = False
        if snapshot is not None and clear_callback is not None:
            try:
                clear_callback(snapshot.identity)
            except Exception:
                pass
        if cancelled:
            raise ApprovalWaitCancelled("approval wait cancelled")
        if decision is None:
            raise ApprovalWaitCancelled("approval wait closed without decision")
        return decision

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        with self._condition:
            snapshot, callback = self._prepare_request_locked(request)
        if callback is not None:
            try:
                callback(snapshot)
            except Exception:
                # Attention publication is an observer; the waiter remains
                # owned by this broker and cannot silently approve.
                pass
        with self._condition:
            return self._await_decision_locked()

    def resolve(
        self,
        attention_id: int,
        decision: ApprovalDecision | str,
        *,
        run_generation: int | None = None,
        request_fingerprint: str | None = None,
    ) -> str:
        try:
            selected = decision if isinstance(decision, ApprovalDecision) else ApprovalDecision(str(decision))
        except (TypeError, ValueError):
            return "REJECTED_INVALID_DECISION"
        with self._condition:
            active = self._active
            if active is None or active.identity.attention_id != attention_id:
                return "IGNORED_STALE"
            if active.identity.run_generation != self._generation or self._cancelled or self._shutdown:
                return "IGNORED_STALE"
            if run_generation is not None and active.identity.run_generation != run_generation:
                return "IGNORED_STALE"
            if request_fingerprint is not None and active.identity.request_fingerprint != request_fingerprint:
                return "IGNORED_STALE"
            if self._decision is not None:
                return "IGNORED_DUPLICATE"
            self._decision = selected
            self._condition.notify_all()
            return "RESOLVED"

    def invalidate(self, *, attention_id: int | None = None, generation: int | None = None) -> bool:
        with self._condition:
            active = self._active
            if active is None:
                return False
            if attention_id is not None and active.identity.attention_id != attention_id:
                return False
            if generation is not None and active.identity.run_generation != generation:
                return False
            if self._decision is not None:
                # Approval won the lock race; cancellation cannot rewrite it.
                return False
            self._cancelled = True
            self._condition.notify_all()
            return True

    def finish_generation(self, generation: int) -> None:
        self.invalidate(generation=generation)
        with self._condition:
            if self._generation == generation:
                self._generation = None
                self._on_attention = None
                self._on_cleared = None

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            self._cancelled = True
            self._condition.notify_all()

    def approve_change(self, preview: Any, assessment: Any) -> bool:
        affected = tuple(str(item) for item in getattr(preview, "affected_files", ()) or ())
        proposed_diff = str(getattr(preview, "diff", "") or "")
        proposed_diff_truncated = len(proposed_diff) > MAX_REVIEW_DIFF_CHARS
        bounded_diff = proposed_diff[:MAX_REVIEW_DIFF_CHARS]
        review_metadata = MappingProxyType(
            {
                "change_set_id": str(getattr(preview, "change_set_id", "")),
                "affected_files": affected[:16],
                "confidence": getattr(assessment, "confidence", None),
                "reasons": tuple(getattr(assessment, "reasons", ()) or ())[:16],
                "proposed_diff": bounded_diff,
                "proposed_diff_truncated": proposed_diff_truncated,
                "proposed_diff_sha256": hashlib.sha256(proposed_diff.encode("utf-8")).hexdigest(),
            }
        )
        request = ApprovalRequest(
            action="code_changeset",
            resource=", ".join(affected[:16]) or "workspace",
            prompt=(
                f"Aplicar ChangeSet {getattr(preview, 'change_set_id', '')}? "
                f"arquivos={len(affected)}; confiança={getattr(assessment, 'confidence', 0):.0%}"
            ),
            metadata=review_metadata,
        )
        return self.request(request) is ApprovalDecision.APPROVED


__all__ = ["ApprovalBroker", "AttentionIdentity", "AttentionSnapshot", "MAX_REVIEW_DIFF_CHARS"]
