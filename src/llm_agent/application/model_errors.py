"""Application identities for the two model transport failures observed by CLI."""

from llm_agent.agent.llm.errors import ModelConnectionError as AgentModelConnectionError
from llm_agent.agent.llm.errors import ModelTimeoutError as AgentModelTimeoutError


class ModelConnectionError(RuntimeError, ConnectionError):
    pass


class ModelTimeoutError(RuntimeError, TimeoutError):
    pass


def _translate_model_error(exc: Exception) -> Exception:
    if isinstance(exc, AgentModelTimeoutError):
        return ModelTimeoutError(*exc.args)
    if isinstance(exc, AgentModelConnectionError):
        return ModelConnectionError(*exc.args)
    return exc


__all__ = ["ModelConnectionError", "ModelTimeoutError"]
