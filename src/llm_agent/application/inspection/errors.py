"""Use-case errors for auxiliary inspection operations."""


class InspectionUnavailableError(RuntimeError):
    """The selected retained inspection data is unavailable."""


class InspectionCorruptDataError(RuntimeError):
    """Persisted inspection data failed its integrity checks."""
