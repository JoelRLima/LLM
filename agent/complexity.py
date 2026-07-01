"""Detecção de complexidade de objetivos.

Decide se um objetivo deve ser tratado via planejamento hierárquico
(decomposição em sub-objetivos / MacroPlan) ou pelo fluxo linear padrão do
Orchestrator. A decisão é puramente heurística (palavras-chave, estrutura
do texto e comprimento) e não depende de nenhum outro componente do
agente, podendo ser testada isoladamente.
"""
import re
from typing import List

# Limiar de pontuação a partir do qual um objetivo é considerado complexo o
# suficiente para justificar planejamento hierárquico. Constante
# configurável: ajuste este valor para tornar a detecção mais ou menos
# sensível sem precisar alterar a lógica de pontuação.
HIERARCHICAL_SCORE_THRESHOLD: float = 3.0

# Palavras/expressões que sugerem múltiplos componentes, escopo amplo ou
# necessidade de análise abrangente.
_COMPLEXITY_KEYWORDS: List[str] = [
    "todos os", "toda a", "todo o", "cada um", "vários", "diversos",
    "analise", "segurança", "vulnerabilidades", "todos os arquivos",
    "múltiplos", "análise completa", "analise completa", "refatore",
    "refatoração", "refatorar", "arquitetura", "sistema inteiro",
    "projeto inteiro", "base de código", "codebase", "vários arquivos",
    "múltiplos arquivos", "revisão geral", "auditoria",
    "levantamento completo", "documentação completa", "migração",
    "migrar", "reescrever", "reescreva", "todos os módulos",
    "de ponta a ponta", "abrangente", "detalhado e completo",
]

# Padrão que detecta listas explícitas (itens numerados ou com marcadores),
# fortes indicadores de que o objetivo já está subdividido em partes.
_LIST_SEPARATOR_PATTERN = re.compile(r"(?:^|\n)\s*(?:\d+[\).]|[-*])\s+")

# Conjunções que, quando repetidas, sugerem uma sequência de várias etapas
# distintas dentro do mesmo objetivo.
_MULTI_STEP_CONJUNCTION_PATTERN = re.compile(
    r"\b(e depois|depois disso|em seguida|então|além disso|adicionalmente)\b",
    re.IGNORECASE,
)

# Limiares de comprimento (em caracteres) usados na pontuação por tamanho.
_LENGTH_SOFT_LIMIT: int = 220
_LENGTH_HARD_LIMIT: int = 400


def _keyword_score(text_lower: str) -> float:
    """Pontua a presença de palavras-chave de complexidade no texto."""
    score = 0.0
    for keyword in _COMPLEXITY_KEYWORDS:
        if keyword in text_lower:
            score += 1.5
    return score


def _structure_score(text: str) -> float:
    """Pontua indícios estruturais de múltiplas etapas (listas, conjunções)."""
    score = 0.0
    if _LIST_SEPARATOR_PATTERN.search(text):
        score += 2.0
    conjunctions_found = _MULTI_STEP_CONJUNCTION_PATTERN.findall(text)
    if len(conjunctions_found) >= 2:
        score += 1.5
    return score


def _length_score(text: str) -> float:
    """Pontua o objetivo com base no seu comprimento em caracteres."""
    length = len(text)
    if length >= _LENGTH_HARD_LIMIT:
        return 2.5
    if length >= _LENGTH_SOFT_LIMIT:
        return 1.0
    return 0.0


def compute_complexity_score(objective: str) -> float:
    """Calcula a pontuação de complexidade heurística de `objective`.

    Combina três sinais independentes: presença de palavras-chave
    associadas a escopo amplo, estrutura do texto (listas/conjunções que
    sugerem múltiplas etapas) e comprimento do texto. A soma dessas
    pontuações é comparada ao limiar em `is_hierarchical`.
    """
    if not objective or not isinstance(objective, str):
        return 0.0
    text_lower = objective.lower()
    return (
        _keyword_score(text_lower)
        + _structure_score(objective)
        + _length_score(objective)
    )


def is_hierarchical(objective: str) -> bool:
    """Decide se `objective` deve usar planejamento hierárquico.

    Retorna `True` quando a pontuação de complexidade (ver
    `compute_complexity_score`) atinge ou ultrapassa
    `HIERARCHICAL_SCORE_THRESHOLD`, indicando que o objetivo provavelmente
    envolve múltiplos componentes ou uma análise ampla o suficiente para
    justificar a decomposição em sub-objetivos independentes.
    """
    return compute_complexity_score(objective) >= HIERARCHICAL_SCORE_THRESHOLD
