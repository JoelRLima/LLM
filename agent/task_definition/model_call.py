"""Context-manager model-call seam for Contract and Spec compilation."""

from __future__ import annotations

import inspect
from typing import Any

from agent.llm.decision_contract import ModelRequestContract
from agent.runtime.budget import BudgetExhausted
from agent.task_definition.errors import TaskDefinitionCompilationError, TaskDefinitionError


def ask_task_definition_model(
    compiler: Any,
    prompt: str,
    *,
    request_contract: ModelRequestContract,
    step_type: str,
) -> Any:
    manager = compiler.context_manager
    if manager is None and compiler.context_manager_provider is not None:
        manager = compiler.context_manager_provider()
    if manager is None:
        raise TaskDefinitionCompilationError(
            "ContextManager indisponivel para compilar task definition",
            code="TASK_DEFINITION_COMPILER_UNAVAILABLE",
        )
    typed = getattr(manager, "ask_model_typed", None)
    if callable(typed):
        return _call_model(typed, prompt, request_contract, step_type, typed=True)
    ask = getattr(manager, "ask_model", None)
    if not callable(ask):
        raise TaskDefinitionCompilationError(
            "ContextManager nao expoe ask_model_typed/ask_model",
            code="TASK_DEFINITION_COMPILER_UNAVAILABLE",
        )
    return _call_model(ask, prompt, request_contract, step_type, typed=False)


def _call_model(
    callable_value: Any,
    prompt: str,
    request_contract: ModelRequestContract,
    step_type: str,
    *,
    typed: bool,
) -> Any:
    kwargs: dict[str, Any] = {
        "request_contract": request_contract,
        "step_type": step_type,
        "include_task_definition": False,
    }
    if not typed:
        kwargs["typed"] = True
    if not _accepts_keyword(callable_value, "include_task_definition"):
        kwargs.pop("include_task_definition")
    try:
        return callable_value(prompt, **kwargs)
    except BudgetExhausted:
        raise
    except TaskDefinitionError:
        raise
    except Exception as exc:
        raise TaskDefinitionCompilationError(
            f"falha no model call de {request_contract.value}",
            code="TASK_DEFINITION_MODEL_CALL_FAILED",
            cause=exc,
        ) from exc


def _accepts_keyword(callable_value: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(callable_value).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
