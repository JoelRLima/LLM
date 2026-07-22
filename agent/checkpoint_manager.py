"""
agent/checkpoint_manager.py

CheckpointManager — fonte única de responsabilidade para persistência de
checkpoint em disco (achado C.10 / linha 1 e 2 da tabela de dívida
técnica).

Antes deste PR, a responsabilidade de checkpoint estava partida entre dois
arquivos:
    - `orchestrator.py`: `_save_checkpoint` / `_load_checkpoint` /
      `_delete_checkpoint` (I/O em disco).
    - `state.py`: `to_checkpoint_dict` / `from_checkpoint_dict`
      (serialização/desserialização do estado).

Este componente concentra a parte de I/O (leitura/escrita atômica em
disco), continuando a delegar a serialização em si ao próprio
`AgentState` (via `to_checkpoint_dict`/`from_checkpoint_dict`) — ou seja,
não duplica a lógica de serialização, apenas para de estar espalhada
entre dois arquivos por responsabilidades diferentes (I/O vs. dados).
"""
import json
import os
from typing import Any, Dict, Optional

from agent.runtime.logging import logger

CHECKPOINT_SCHEMA_VERSION = 2


class CheckpointManager:
    """Salva, carrega e remove o checkpoint de uma tarefa em disco."""

    def __init__(self, checkpoint_file: str):
        self.checkpoint_file = checkpoint_file

    def save(self, agent_state: Any) -> None:
        """Salva o estado atual da tarefa em disco para possibilitar
        retomada após uma interrupção (Ctrl+C, queda de energia, etc.).

        A escrita é feita em um arquivo temporário e depois renomeada
        atomicamente (`os.replace`), evitando checkpoints corrompidos em
        caso de interrupção durante a própria gravação. Falhas de
        gravação não devem interromper a execução do agente.
        """
        try:
            checkpoint_data = agent_state.to_checkpoint_dict()
            checkpoint_data["schema_version"] = CHECKPOINT_SCHEMA_VERSION
            tmp_path = f"{self.checkpoint_file}.tmp"
            os.makedirs(os.path.dirname(self.checkpoint_file) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_path, self.checkpoint_file)
        except Exception as e:
            logger.warning(f"Falha ao salvar checkpoint: {e}")

    def load(self) -> Optional[Dict[str, Any]]:
        """Carrega o checkpoint salvo em disco, se existir e for válido.

        Retorna `None` silenciosamente se o arquivo não existir ou
        estiver corrompido/ilegível, garantindo que uma nova tarefa possa
        iniciar normalmente sem que o checkpoint quebre a execução.
        """
        if not os.path.exists(self.checkpoint_file):
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            version = data.get("schema_version")
            if version != CHECKPOINT_SCHEMA_VERSION:
                logger.warning(
                    "Checkpoint com versão incompatível (%s); esperado %s.",
                    version,
                    CHECKPOINT_SCHEMA_VERSION,
                )
                return None
            if not isinstance(data.get("objective"), str):
                logger.warning("Checkpoint sem objetivo textual válido; ignorando.")
                return None
            plan = data.get("plan")
            if not isinstance(plan, list) or any(not isinstance(step, dict) for step in plan):
                logger.warning("Checkpoint com plano estruturalmente inválido; ignorando.")
                return None
            records = data.get("step_records")
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                logger.warning("Checkpoint sem registros de execução válidos; ignorando.")
                return None
            return data
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as e:
            logger.warning(f"Checkpoint corrompido ou ilegível, ignorando: {e}")
            return None

    def delete(self) -> None:
        """Remove o arquivo de checkpoint ao final da tarefa (sucesso ou falha)."""
        try:
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
        except OSError as e:
            logger.warning(f"Falha ao remover checkpoint: {e}")
