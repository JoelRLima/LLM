"""Acumulador/sumarizador incremental de resultados parciais.

Usado durante a execução hierárquica para acumular os resumos de cada
sub-objetivo executado sem deixar o conteúdo total crescer sem limites, o
que poderia estourar a janela de contexto do modelo ao gerar a resposta
final consolidada. Este módulo não depende do `ContextManager` nem de
nenhum outro componente concreto do agente: a função de sumarização é
injetada via `summarize_fn`.
"""
from typing import Callable, List, Optional

from logger import logger


class IncrementalSummarizer:
    """Acumula itens de texto e os condensa periodicamente em resumos.

    Itens recentes são mantidos como texto bruto até que seu número
    atinja `max_items_before_summary`; nesse ponto, todos são combinados e
    passados para `summarize_fn`, e o resultado (truncado a
    `max_summary_chars`, se necessário) é armazenado como um resumo
    permanente, liberando a lista de itens recentes.
    """

    def __init__(
        self,
        summarize_fn: Callable[[str], str],
        max_items_before_summary: int = 5,
        max_summary_chars: int = 2000,
    ) -> None:
        self.summarize_fn = summarize_fn
        self.max_items_before_summary = max_items_before_summary
        self.max_summary_chars = max_summary_chars
        self._recent_items: List[str] = []
        self._summaries: List[str] = []

    def add(self, item: str) -> None:
        """Adiciona um novo item de texto ao acumulador.

        Se o número de itens recentes atingir `max_items_before_summary`,
        dispara automaticamente a condensação (`_flush`) para um resumo.
        """
        if not item:
            return
        self._recent_items.append(item)
        if len(self._recent_items) >= self.max_items_before_summary:
            self._flush()

    def force_flush(self) -> None:
        """Força a condensação de quaisquer itens recentes pendentes.

        Deve ser chamado antes de `get_accumulated_content` ao final da
        execução, garantindo que nenhum item fique de fora por não ter
        atingido o limite de `max_items_before_summary`.
        """
        if self._recent_items:
            self._flush()

    def _flush(self) -> None:
        combined = "\n\n".join(self._recent_items)
        summary = combined
        try:
            result = self.summarize_fn(combined)
            if result:
                summary = result
        except Exception as e:
            logger.warning(f"IncrementalSummarizer: falha ao sumarizar, mantendo texto bruto: {e}")

        if summary and len(summary) > self.max_summary_chars:
            summary = summary[: self.max_summary_chars] + "... (truncado)"

        if summary:
            self._summaries.append(summary)
        self._recent_items = []

    def get_accumulated_content(self) -> str:
        """Retorna todo o conteúdo acumulado até o momento.

        Combina os resumos já condensados com quaisquer itens recentes
        ainda não sumarizados (chame `force_flush` antes, se desejar que
        tudo esteja condensado).
        """
        parts: List[str] = []
        if self._summaries:
            parts.append("### Resumos acumulados\n" + "\n\n".join(self._summaries))
        if self._recent_items:
            parts.append("### Itens recentes\n" + "\n\n".join(self._recent_items))
        return "\n\n".join(parts)
