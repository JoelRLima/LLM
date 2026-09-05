from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Sequence

from agent.code.changes import ChangeSet, ChangeSetError, changeset_from_dict
from agent.code.context_selection import SelectedFile
from agent.code.outcome_verifier import (
    PROPOSAL_DECISION_SCHEMA,
    CodeEvidenceManifest,
    CodeProposalDecision,
    bind_selected_file_evidence,
    parse_code_proposal,
)
from agent.code.proposal_preconditions import bind_observed_preconditions
from agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    ContextFitError,
    ContextSourceRecord,
    discover_project_guidance,
    fit_contextual_request,
    fixed_untrusted_data_policy,
    render_untrusted_context_envelope,
)
from agent.llm.contracts import ModelMessage, ModelRequest
from agent.llm.structured_output import (
    StructuredOutputError,
    StructuredOutputStrategy,
    parse_structured_response,
)

from .workflow_proposal_support import build_decision_prompt, build_prompt

CHANGESET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["changes"],
    "properties": {
        "objective": {"type": "string"},
        "rationale": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "kind"],
                "properties": {
                    "path": {"type": "string"},
                    "kind": {"type": "string", "enum": ["create", "modify", "edit", "delete", "move"]},
                    "content": {"type": "string"},
                    "base_hash": {"type": "string"},
                    "destination_path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["operation", "start_line"],
                            "properties": {
                                "operation": {"type": "string", "enum": ["replace", "insert_before", "insert_after", "delete"]},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _prompt(objective: str, targets: Sequence[str], context: str, instruction: str | None) -> str:
    return build_prompt(objective, targets, context, instruction, CHANGESET_SCHEMA)


def _decision_prompt(objective: str, targets: Sequence[str], instruction: str | None) -> str:
    return build_decision_prompt(objective, targets, instruction)


def propose_changes(service: Any, objective: str, target_files: Sequence[str]) -> ChangeSet:
    strategy = StructuredOutputStrategy(service.context.model_gateway.capabilities)
    structured = strategy.select(schema=CHANGESET_SCHEMA)
    selected = service.context_selector.select(
        objective, target_files,
        max_chars=max(2000, service.context.limits.max_output_tokens * 6),
    )
    service.context.emit("code_context_selected", {
        "files": [item.path for item in selected.files], "truncated": selected.truncated,
    })
    evidence_manifest = bind_selected_file_evidence(service.root, selected.files)
    service._last_code_evidence_manifest = evidence_manifest
    required_records = evidence_manifest.context_records
    guidance = discover_project_guidance(service.root, target_files)
    optional_records = guidance.records
    prompt = _prompt(objective, target_files, selected.text, structured.instruction)
    response, call_number = _complete(
        service,
        _proposal_request(
            service,
            prompt,
            structured,
            required_records=required_records,
            optional_records=optional_records,
        ),
    )
    try:
        return _decode_proposal(response.content, objective, selected.files)
    except (StructuredOutputError, ChangeSetError) as first_error:
        repair_prompt = (
            f"{prompt}\n\nA resposta anterior foi rejeitada: {first_error}. "
            "Retorne agora um único objeto JSON completo com a chave changes, contendo "
            "as mudanças necessárias para o objetivo. Não retorne {} nem texto livre."
        )
        response, call_number = _complete(
            service,
            _proposal_request(
                service,
                repair_prompt,
                structured,
                required_records=required_records,
                optional_records=optional_records,
            ),
        )
        return _decode_proposal(response.content, objective, selected.files)


def propose_code_decision(
    service: Any,
    objective: str,
    target_files: Sequence[str],
) -> tuple[CodeProposalDecision, CodeEvidenceManifest]:
    """Build the W13 tri-state decision for grounded atomic code actions."""

    strategy = StructuredOutputStrategy(service.context.model_gateway.capabilities)
    structured = strategy.select(schema=PROPOSAL_DECISION_SCHEMA)
    selected = service.context_selector.select(
        objective,
        target_files,
        max_chars=max(2_000, service.context.limits.max_output_tokens * 6),
    )
    service.context.emit(
        "code_context_selected",
        {"files": [item.path for item in selected.files], "truncated": selected.truncated},
    )
    manifest = bind_selected_file_evidence(
        service.root,
        selected.files,
        user_objective=objective,
    )
    guidance = discover_project_guidance(service.root, target_files)
    prompt = _decision_prompt(objective, target_files, structured.instruction)
    response, _call_number = _complete(
        service,
        _proposal_request(
            service,
            prompt,
            structured,
            required_records=manifest.context_records,
            optional_records=guidance.records,
        ),
    )
    try:
        value = parse_structured_response(response.content)
        return parse_code_proposal(value, objective), manifest
    except (StructuredOutputError, ChangeSetError) as first_error:
        repair_prompt = (
            f"{prompt}\n\nA resposta anterior foi rejeitada: {first_error}. "
            "Retorne somente o envelope fechado decision/rationale/reason_code/question/changes."
        )
        response, _call_number = _complete(
            service,
            _proposal_request(
                service,
                repair_prompt,
                structured,
                required_records=manifest.context_records,
                optional_records=guidance.records,
            ),
        )
        return parse_code_proposal(parse_structured_response(response.content), objective), manifest


def _code_evidence_records(
    root: str | Path,
    observed_files: Sequence[SelectedFile],
) -> tuple[ContextSourceRecord, ...]:
    return bind_selected_file_evidence(root, observed_files).context_records


def _decode_proposal(
    content: str,
    objective: str,
    observed_files: Sequence[SelectedFile],
) -> ChangeSet:
    parsed = parse_structured_response(content, CHANGESET_SCHEMA)
    proposed = changeset_from_dict(parsed, objective=objective)
    return bind_observed_preconditions(proposed, observed_files)


def _proposal_request(
    service: Any,
    prompt: str,
    structured: Any,
    *,
    required_records: Sequence[ContextSourceRecord] = (),
    optional_records: Sequence[ContextSourceRecord] = (),
) -> ModelRequest:
    profile = getattr(service.context, "model_profile", None)
    if profile is None or not isinstance(getattr(profile, "model", None), str):
        raise RuntimeError("workflow proposal requires the resolved model profile")
    system = (
        "Você propõe mudanças revisáveis. Não escreva no filesystem.\n"
        + fixed_untrusted_data_policy()
    )
    output_tokens = int(service.context.limits.max_output_tokens)
    required_message = (
        render_untrusted_context_envelope(required_records, category=REQUIRED_EVIDENCE)
        if required_records
        else None
    )

    def build_request(required: str | None, optional: str | None) -> ModelRequest:
        data_messages = tuple(
            ModelMessage("user", content)
            for content in (required, optional)
            if content
        )
        return ModelRequest(
            messages=(ModelMessage("system", system),) + data_messages + (ModelMessage("user", prompt),),
            model=profile.model,
            temperature=0.1,
            max_output_tokens=output_tokens,
            structured_output=structured,
            context_limit=_context_limit(service),
        )

    mandatory_request = build_request(required_message, None)
    fit = fit_contextual_request(
        mandatory_request=mandatory_request,
        required_records=required_records,
        optional_records=optional_records,
        context_limit=_context_limit(service),
        gateway=service.context.model_gateway,
        build_request=build_request,
    )
    if fit.mandatory_overflow:
        raise ContextFitError(
            "A evidência obrigatória do código excede o limite de contexto; proposta não enviada."
        )
    record_metric = getattr(service.context, "record_metric", None)
    if callable(record_metric):
        record_metric(
            "context_projection",
            {
                "context_projection_total_estimated_tokens": fit.projection.estimated_tokens,
                "context_source_count": len(fit.projection.source_records),
                "context_fit_proven": fit.projection.fit_proven,
                "context_optional_truncated": fit.projection.optional_truncated,
            },
        )
    if not isinstance(fit.request, ModelRequest):
        raise TypeError("context fitting returned an invalid model request")
    return fit.request


def _context_limit(service: Any) -> int | None:
    hardware = getattr(service.context, "hardware_profile", None)
    value = getattr(hardware, "context_limit", None)
    if isinstance(value, int) and value > 0:
        return value
    metadata = getattr(service.context, "metadata", {})
    value = metadata.get("context_limit") if isinstance(metadata, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def _complete(service: Any, request: ModelRequest) -> tuple[Any, int]:
    """Delegate the coding provider attempt to the canonical lifecycle owner."""

    from agent.runtime.model_call import ModelCallService

    outcome = ModelCallService.for_context(service.context).complete(
        request,
        operation="propose_changes",
    )
    return outcome.response, outcome.call_number
