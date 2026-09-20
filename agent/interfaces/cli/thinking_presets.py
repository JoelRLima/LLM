"""Frozen interactive thinking-budget presentation policy."""

THINKING_PRESET_BY_KEY: dict[str, int] = {
    "B": 512,
    "M": 1024,
    "A": 2048,
}

THINKING_LABEL_BY_BUDGET: dict[int, str] = {
    512: "BAIXO",
    1024: "MÉDIO",
    2048: "ALTO",
}

DEFAULT_THINKING_BUDGET = 1024

__all__ = [
    "DEFAULT_THINKING_BUDGET",
    "THINKING_LABEL_BY_BUDGET",
    "THINKING_PRESET_BY_KEY",
]
