"""Application error for a failed legacy-state migration use case."""


class StateMigrationFailedError(RuntimeError):
    """A supported legacy-state migration could not be completed."""
