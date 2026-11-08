"""Automatic grammar-selection sentinel used by model-call interfaces."""

from __future__ import annotations


class AutoGrammar:
    """Sentinela que indica que a gramática deve ser escolhida
    automaticamente com base no `step_type` do passo atual."""

    pass


AUTO_GRAMMAR = AutoGrammar()
