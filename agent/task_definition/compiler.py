"""Single owner for typed Contract to Spec task-definition compilation."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from agent.llm.admitted_decisions import (
    TaskContractBlockedDecision,
    TaskContractDecision,
    TaskContractNeedsInputDecision,
    TaskSpecBlockedDecision,
    TaskSpecDecision,
    admit_typed_model_decision,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.runtime.budget import BudgetExhausted
from agent.task_definition.errors import (
    TaskDefinitionBlocked,
    TaskDefinitionCompilationError,
    TaskDefinitionError,
    TaskDefinitionMismatchError,
    TaskDefinitionNeedsInput,
)
from agent.task_definition.model_call import ask_task_definition_model
from agent.task_definition.models import TaskContract, TaskDefinitionRef, TaskSpec
from agent.task_definition.repository import TaskDefinitionRepository
from agent.task_definition.serialization import serialize_contract, serialize_spec

Provider = Callable[..., Any]


class TaskDefinitionCompiler:
    """Compile and durably publish exactly one immutable definition sequence."""

    def __init__(
        self,
        repository: TaskDefinitionRepository,
        context_manager: Any | None = None,
        *,
        context_manager_provider: Callable[[], Any] | None = None,
        contract_provider: Provider | None = None,
        spec_provider: Provider | None = None,
    ) -> None:
        self.repository = repository
        self.context_manager = context_manager
        self.context_manager_provider = context_manager_provider
        self.contract_provider = contract_provider
        self.spec_provider = spec_provider
        self.last_ref: TaskDefinitionRef | None = None

    def compile(self, task_id: str, objective: str) -> TaskDefinitionRef:
        task_id = self.repository.validate_task_id(task_id)
        if not isinstance(objective, str) or not objective.strip():
            raise TaskDefinitionCompilationError(
                "objective nao pode ser vazio", code="TASK_DEFINITION_INVALID"
            )
        existing = self.repository.inspect(task_id)
        if existing is not None:
            if existing.contract.objective != objective:
                raise TaskDefinitionMismatchError(
                    "task_id ja esta ligado a um Contract de objetivo diferente"
                )
            if existing.reference.is_complete:
                self.last_ref = existing.reference
                return existing.reference
            return self._expand_spec(existing.contract)
        contract = self._request_contract(task_id, objective)
        if contract.task_id != task_id:
            raise TaskDefinitionMismatchError("Contract retornou task_id diferente do root_task_id")
        if contract.objective != objective:
            raise TaskDefinitionMismatchError("Contract nao preservou o objective exato")
        self.last_ref = self.repository.save_contract(contract)
        return self._expand_spec(contract)

    def resume(self, task_id: str, reference: TaskDefinitionRef) -> TaskDefinitionRef:
        task_id = self.repository.validate_task_id(task_id)
        if not isinstance(reference, TaskDefinitionRef):
            raise TaskDefinitionMismatchError("resume exige TaskDefinitionRef tipada")
        if reference.task_id != task_id:
            raise TaskDefinitionMismatchError("TaskDefinitionRef nao corresponde ao root_task_id")
        record = self.repository.resolve(reference)
        self.last_ref = reference
        if record.reference.is_complete:
            return reference
        return self._expand_spec(record.contract)

    def _expand_spec(self, contract: TaskContract) -> TaskDefinitionRef:
        spec = self._request_spec(contract)
        try:
            spec.validate_against(contract)
            reference = self.repository.save_spec(spec)
        except TaskDefinitionError:
            raise
        except Exception as exc:
            raise TaskDefinitionCompilationError(
                "Spec nao pode ser validada/persistida",
                code="TASK_SPEC_PERSISTENCE_FAILED",
                cause=exc,
            ) from exc
        self.last_ref = reference
        return reference

    def _request_contract(self, task_id: str, objective: str) -> TaskContract:
        prompt = _contract_prompt(task_id, objective)
        if self.contract_provider is not None:
            raw = _call_provider(
                self.contract_provider,
                task_id,
                objective,
                contract=ModelRequestContract.TASK_CONTRACT,
            )
        else:
            raw = self._ask_model(
                prompt,
                request_contract=ModelRequestContract.TASK_CONTRACT,
                step_type="task_contract",
            )
        admitted = self._admit(raw, ModelRequestContract.TASK_CONTRACT, "Contract")
        if isinstance(admitted, TaskContractNeedsInputDecision):
            raise TaskDefinitionNeedsInput(admitted.reason, admitted.question)
        if isinstance(admitted, TaskContractBlockedDecision):
            raise TaskDefinitionBlocked(admitted.reason)
        if not isinstance(admitted, TaskContractDecision):
            raise TaskDefinitionCompilationError(
                "resposta de Contract nao foi admitida pelo contrato fechado",
                code="TASK_CONTRACT_ADMISSION_FAILED",
            )
        return admitted.contract

    def _request_spec(self, contract: TaskContract) -> TaskSpec:
        prompt = _spec_prompt(contract)
        if self.spec_provider is not None:
            raw = _call_provider(
                self.spec_provider,
                contract,
                contract=ModelRequestContract.TASK_SPEC,
            )
        else:
            raw = self._ask_model(
                prompt,
                request_contract=ModelRequestContract.TASK_SPEC,
                step_type="task_spec",
            )
        admitted = self._admit(raw, ModelRequestContract.TASK_SPEC, "Spec")
        if isinstance(admitted, TaskSpecBlockedDecision):
            raise TaskDefinitionBlocked(admitted.reason)
        if not isinstance(admitted, TaskSpecDecision):
            raise TaskDefinitionCompilationError(
                "resposta de Spec nao foi admitida pelo contrato fechado",
                code="TASK_SPEC_ADMISSION_FAILED",
            )
        return admitted.spec

    def _ask_model(
        self,
        prompt: str,
        *,
        request_contract: ModelRequestContract,
        step_type: str,
    ) -> Any:
        return ask_task_definition_model(
            self,
            prompt,
            request_contract=request_contract,
            step_type=step_type,
        )

    @staticmethod
    def _admit(raw: Any, contract: ModelRequestContract, label: str) -> Any:
        if isinstance(raw, TaskContractDecision):
            _validate_admitted_authority(raw.contract, "Contract")
            return raw
        if isinstance(raw, TaskSpecDecision):
            _validate_admitted_authority(raw.spec, "Spec")
            return raw
        if isinstance(
            raw,
            (
                TaskContractDecision,
                TaskContractNeedsInputDecision,
                TaskContractBlockedDecision,
                TaskSpecDecision,
                TaskSpecBlockedDecision,
            ),
        ):
            return raw
        if isinstance(raw, TaskContract):
            _validate_admitted_authority(raw, "Contract")
            return TaskContractDecision(contract=raw)
        if isinstance(raw, TaskSpec):
            _validate_admitted_authority(raw, "Spec")
            return TaskSpecDecision(spec=raw)
        admitted = admit_typed_model_decision(
            raw, request_contract=contract, step_type=contract.value
        )
        if admitted is None:
            raise TaskDefinitionCompilationError(
                f"{label} retornado fora da forma fechada",
                code=f"{label.upper()}_ADMISSION_FAILED",
            )
        return admitted


def _validate_admitted_authority(value: TaskContract | TaskSpec, label: str) -> None:
    try:
        if isinstance(value, TaskContract):
            serialize_contract(value)
        else:
            serialize_spec(value)
    except TaskDefinitionError as exc:
        raise TaskDefinitionCompilationError(
            f"{label} retornado fora dos limites canonicos",
            code=f"{label.upper()}_ADMISSION_FAILED",
            cause=exc,
        ) from exc


def _invoke_provider(provider: Provider, *args: Any) -> Any:
    try:
        parameters = tuple(inspect.signature(provider).parameters.values())
    except (TypeError, ValueError):
        return provider(*args)
    if any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        return provider(*args)
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    return provider(*args[: len(positional)])


def _call_provider(
    provider: Provider,
    *args: Any,
    contract: ModelRequestContract,
) -> Any:
    try:
        return _invoke_provider(provider, *args)
    except BudgetExhausted:
        raise
    except TaskDefinitionError:
        raise
    except Exception as exc:
        raise TaskDefinitionCompilationError(
            f"falha no provider de {contract.value}",
            code="TASK_DEFINITION_PROVIDER_FAILED",
            cause=exc,
        ) from exc


def _contract_prompt(task_id: str, objective: str) -> str:
    return (
        "Compile uma TaskContract normativa para a tarefa abaixo. Responda exclusivamente "
        "com o contrato fechado TASK_CONTRACT, sem markdown, prose, ferramentas, grants ou plano executavel.\n"
        f"task_id exato: {task_id}\nobjective exato: {objective}\n"
        'Forma permitida: {"action":"define_contract","contract":{...}} ou '
        '{"action":"needs_input","reason":"...","question":"..."}. '
        "O Contract deve conter apenas intent, requirements, constraints, invariants, "
        "success_criteria, out_of_scope e stop_conditions, dentro dos limites admitidos."
    )


def _spec_prompt(contract: TaskContract) -> str:
    return (
        "Expand the admitted Contract into a bounded TaskSpec. Respond only with the closed "
        "TASK_SPEC decision; do not emit prose, tool calls, capability grants, approvals, or an executable Plan.\n"
        f"Canonical Contract: {contract.to_dict()}\n"
        f"Contract version: {contract.version}; Contract digest: {contract.digest()}\n"
        'Allowed success form: {"action":"define_spec","spec":{...}}; blocked form: '
        '{"action":"blocked","reason":"..."}. The Spec must reproduce the exact task_id, '
        "contract_version and contract_digest and contain descriptive phases with requirements, "
        "invariants, acceptance_criteria, evidence_requirements and depends_on."
    )


__all__ = ["TaskDefinitionCompiler"]
