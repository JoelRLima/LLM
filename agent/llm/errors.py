"""Erros públicos normalizados na fronteira de modelo/provider."""

from typing import Any


class ModelGatewayError(RuntimeError):
    """Erro normalizado na fronteira de provider."""


class ModelProviderError(ModelGatewayError):
    """Falha causal segura para uma tentativa de provider/modelo."""

    code = "MODEL_PROVIDER_ERROR"
    layer = "provider"

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        del message
        self.public_message = "Model provider request failed."
        super().__init__(self.public_message)
        if cause is not None:
            self.__cause__ = cause


class ModelTimeoutError(ModelGatewayError, TimeoutError):
    pass


class ModelConnectionError(ModelGatewayError, ConnectionError):
    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


class ModelResponseError(ModelGatewayError, ValueError):
    def __init__(self, message: str, *, partial_content: str = "") -> None:
        super().__init__(message)
        self.partial_content = partial_content


class UnsupportedModelCapability(ModelGatewayError):
    pass


__all__ = [
    "ModelConnectionError",
    "ModelGatewayError",
    "ModelProviderError",
    "ModelResponseError",
    "ModelTimeoutError",
    "UnsupportedModelCapability",
]
