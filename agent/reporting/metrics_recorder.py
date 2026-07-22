"""
agent/metrics_recorder.py

MetricsRecorder — fonte única de responsabilidade para gravação e leitura
de métricas do agente em `agent_metrics.jsonl` (achado C.10 / linha 1 da
tabela de dívida técnica).

Antes deste PR, esta lógica (`_log_metric`, `_count_metrics_lines`,
`_get_metrics_for_task`) vivia dentro de `orchestrator.py`, mais um
pedaço de responsabilidade acumulada no God Object. Extraída aqui como um
componente isolado, sem nenhuma dependência do Orchestrator — recebe
apenas o caminho do arquivo de métricas e, quando necessário, a linha de
início (`start_line`) marcada no começo da tarefa atual.
"""
import json
import os
from typing import Any, Dict, List

from agent.runtime.logging import logger

DEFAULT_METRICS_FILE = "agent_metrics.jsonl"


class MetricsRecorder:
    """Grava e lê entradas de métricas do agente em um arquivo JSONL."""

    def __init__(self, metrics_file: str = DEFAULT_METRICS_FILE):
        self.metrics_file = metrics_file

    def log_metric(self, entry: Dict[str, Any]) -> None:
        """Adiciona uma entrada ao final do arquivo de métricas."""
        try:
            with open(self.metrics_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Falha ao registrar métrica: {e}")

    def count_lines(self) -> int:
        """Conta quantas linhas já existem no arquivo de métricas.

        Usado como marca d'água no início de uma tarefa para que
        `get_entries_since` só considere entradas gravadas durante a
        execução atual. Retorna 0 se o arquivo não existir ou não puder
        ser lido.
        """
        if not os.path.exists(self.metrics_file):
            return 0
        try:
            with open(self.metrics_file, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError as e:
            logger.warning(f"Falha ao contar linhas de métricas: {e}")
            return 0

    def get_entries_since(self, start_line: int) -> List[Dict[str, Any]]:
        """Lê as entradas do arquivo de métricas gravadas após `start_line`
        (tipicamente o valor retornado por `count_lines()` no início da
        tarefa), isto é, aquelas produzidas durante a execução corrente.

        Linhas malformadas (JSON inválido) são ignoradas silenciosamente,
        garantindo leitura robusta mesmo diante de gravações concorrentes
        ou truncadas. Retorna lista vazia se o arquivo não existir.
        """
        if not os.path.exists(self.metrics_file):
            return []

        entries: List[Dict[str, Any]] = []
        try:
            with open(self.metrics_file, "r", encoding="utf-8") as f:
                for index, line in enumerate(f):
                    if index < start_line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(parsed, dict):
                        entries.append(parsed)
        except OSError as e:
            logger.warning(f"Falha ao ler métricas da tarefa: {e}")
            return []

        return entries
