"""Bounded, independent verification for code outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    ContextFitError,
    ContextSourceRecord,
    fit_contextual_request,
    fixed_untrusted_data_policy,
    render_untrusted_context_envelope,
)
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from agent.llm.structured_output import StructuredOutputError, parse_structured_response
from agent.runtime.model_call import ModelCallService
from agent.runtime.outcome_contracts import MAX_VERIFIER_EVIDENCE_IDS

from .outcome_contracts import CodeOutcomeSeal, CodeOutcomeVerdict, CodeOutcomeVerification
from .outcome_evidence import (
    MAX_EVIDENCE_RECORDS,
    MAX_FILE_CONTENT_RECORDS,
    MAX_PARENT_DEPTH,
    MAX_PARENT_IDS,
    MAX_RECORD_TEXT_CHARS,
    MAX_TOTAL_TEXT_CHARS,
    CodeEvidenceManifest,
    CodeEvidenceRecord,
)
from .outcome_evidence_binding import bind_selected_file_evidence, prepared_change_evidence
from .outcome_proposal import (
    PROPOSAL_DECISION_SCHEMA,
    CodeProposalDecision,
    CodeProposalKind,
    CodeProposalReasonCode,
    parse_code_proposal,
)
from .outcome_verifier_support import context_limit, project_verdict

VERIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict", "reason", "evidence_ids"],
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in CodeOutcomeVerdict],
        },
        "reason": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}
class CodeOutcomeVerifier:
    # The verifier owns the request boundary while support owns pure projection.
    def __init__(self, context: Any, root: str | Path) -> None:
        self.context = context
        self.root = Path(root).expanduser().resolve()

    def verify(
        self,
        objective: str,
        decision_kind: CodeProposalKind,
        manifest: CodeEvidenceManifest,
        *,
        target_paths: Sequence[str] = (),
        reason_code: CodeProposalReasonCode | None = None,
        prepared_records: Sequence[ContextSourceRecord] = (),
        complete: Callable[[ModelRequest], Any] | None = None,
    ) -> CodeOutcomeVerification:
        if getattr(getattr(self.context, "cancellation", None), "cancelled", False):
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                "verification cancelled before call",
                failure_code="CANCELLED",
            )
        if not manifest.validate():
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                "evidence manifest is invalid or outside limits",
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )
        if not manifest.revalidate_files(self.root):
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                "file evidence became stale before verification",
                failure_code="CODE_EVIDENCE_STALE",
                stale=True,
            )
        records = [*manifest.context_records, *prepared_records]
        envelope = render_untrusted_context_envelope(records, category=REQUIRED_EVIDENCE)
        profile = getattr(self.context, "model_profile", None)
        model = getattr(profile, "model", None)
        if not isinstance(model, str) or not model:
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                "resolved model profile is missing",
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )
        output_tokens = int(
            getattr(getattr(self.context, "limits", None), "max_output_tokens", 0) or 1
        )
        system = (
            "You are an independent engineering outcome verifier. "
            "Do not execute tools, mutate files, approve effects, or invent evidence.\n"
            + fixed_untrusted_data_policy()
        )

        def build_request(
            required_message: str | None,
            optional_message: str | None,
        ) -> ModelRequest:
            del optional_message
            return ModelRequest(
                messages=(
                    ModelMessage("system", system),
                    *((ModelMessage("user", required_message),) if required_message else ()),
                    ModelMessage(
                        "user",
                        (
                            f"Exact objective: {objective}\n"
                            f"Decision to verify: {decision_kind.value}\n"
                            "Return only JSON matching the verifier schema. "
                            "Cite only evidence_ids present in the envelope."
                        ),
                    ),
                ),
                model=model,
                temperature=0.0,
                max_output_tokens=output_tokens,
                structured_output=StructuredOutputRequest(
                    mode=StructuredOutputMode.JSON_PROMPT,
                    schema=VERIFIER_SCHEMA,
                    instruction="Return only one closed JSON object.",
                ),
                context_limit=context_limit(self.context),
            )

        try:
            fit = fit_contextual_request(
                mandatory_request=build_request(envelope, None),
                required_records=records,
                context_limit=context_limit(self.context),
                gateway=getattr(self.context, "model_gateway", None),
                build_request=build_request,
            )
        except ContextFitError as exc:
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                str(exc),
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )
        if fit.mandatory_overflow:
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                "mandatory evidence does not fit context limit",
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )
        try:
            response = (
                complete(fit.request)
                if complete is not None
                else ModelCallService.for_context(self.context)
                .complete(fit.request, operation="code_outcome_verifier")
                .response
            )
            content = response.content if isinstance(response, ModelResponse) else str(response)
            parsed = parse_structured_response(content, VERIFIER_SCHEMA)
            return self._project_verdict(
                parsed,
                manifest,
                decision_kind,
                target_paths=target_paths,
                reason_code=reason_code,
                additional_evidence_ids=tuple(
                    record.source_id
                    for record in prepared_records
                    if isinstance(record.source_id, str) and record.source_id.strip()
                ),
            )
        except StructuredOutputError as exc:
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                str(exc),
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )
        except Exception as exc:
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.INSUFFICIENT,
                f"verification unavailable: {type(exc).__name__}",
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
            )

    @staticmethod
    def _project_verdict(
        parsed: Any,
        manifest: CodeEvidenceManifest,
        decision_kind: CodeProposalKind,
        *,
        target_paths: Sequence[str],
        reason_code: CodeProposalReasonCode | None,
        additional_evidence_ids: Sequence[str],
    ) -> CodeOutcomeVerification:
        return project_verdict(
            parsed,
            manifest,
            decision_kind,
            target_paths=target_paths,
            reason_code=reason_code,
            additional_evidence_ids=additional_evidence_ids,
        )


_context_limit = context_limit

__all__ = [
    "CodeEvidenceManifest",
    "CodeEvidenceRecord",
    "CodeOutcomeSeal",
    "CodeOutcomeVerification",
    "CodeOutcomeVerifier",
    "CodeOutcomeVerdict",
    "CodeProposalDecision",
    "CodeProposalKind",
    "CodeProposalReasonCode",
    "MAX_EVIDENCE_RECORDS",
    "MAX_FILE_CONTENT_RECORDS",
    "MAX_PARENT_DEPTH",
    "MAX_PARENT_IDS",
    "MAX_RECORD_TEXT_CHARS",
    "MAX_TOTAL_TEXT_CHARS",
    "MAX_VERIFIER_EVIDENCE_IDS",
    "PROPOSAL_DECISION_SCHEMA",
    "VERIFIER_SCHEMA",
    "bind_selected_file_evidence",
    "parse_code_proposal",
    "prepared_change_evidence",
]
