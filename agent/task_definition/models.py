"""Public compatibility surface for immutable task-definition models."""

from agent.task_definition.binding_model import (
    TaskDefinitionBinding,
    TaskDefinitionRecord,
    TaskDefinitionRef,
)
from agent.task_definition.contract_model import TaskContract
from agent.task_definition.model_validation import (
    CONTRACT_VERSION,
    DEFINITION_STATES,
    DIGEST_PATTERN,
    MAX_COLLECTION_ITEMS,
    MAX_PHASE_ID_LENGTH,
    MAX_PHASES,
    MAX_STRING_LENGTH,
    MAX_TASK_ID_LENGTH,
    MAX_VERSION,
    PHASE_ID_PATTERN,
    SCHEMA_VERSION,
    SPEC_VERSION,
    TASK_ID_PATTERN,
)
from agent.task_definition.spec_model import TaskSpec, TaskSpecPhase

__all__ = [
    "CONTRACT_VERSION",
    "DEFINITION_STATES",
    "DIGEST_PATTERN",
    "MAX_COLLECTION_ITEMS",
    "MAX_PHASES",
    "MAX_PHASE_ID_LENGTH",
    "MAX_STRING_LENGTH",
    "MAX_TASK_ID_LENGTH",
    "MAX_VERSION",
    "PHASE_ID_PATTERN",
    "SCHEMA_VERSION",
    "SPEC_VERSION",
    "TASK_ID_PATTERN",
    "TaskContract",
    "TaskDefinitionBinding",
    "TaskDefinitionRecord",
    "TaskDefinitionRef",
    "TaskSpec",
    "TaskSpecPhase",
]
