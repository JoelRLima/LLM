"""
agent/semantic_memory.py

Camada de busca semântica sobre a memória de longo prazo do agente
(`AgentMemory.state['file_summaries']`).
    Isso permitiria que o agente encontre arquivos relevantes mesmo quando
    o usuário não menciona o nome exato do arquivo no objetivo (busca por
    significado, não por substring).
"""

from __future__ import annotations

import json
import logging
import pickle
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    # Import apenas para type checking, evita import circular em runtime.
    from agent.memory import AgentMemory

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"


class SemanticMemory:
    """
    Índice de busca semântica construído sobre os resumos de arquivos
    armazenados em `AgentMemory.state['file_summaries']`.

    A classe segue carregamento e indexação totalmente "lazy": nem o
    modelo de embeddings nem o índice vetorial são criados no
    `__init__`, apenas quando um método que efetivamente precisa deles
    é chamado pela primeira vez. Isso evita custo de import/download do
    modelo em fluxos do agente que nunca usam busca semântica.

    Atributos:
        memory: Referência à instância de `AgentMemory` cujos resumos
            serão indexados.
        model_name: Nome do modelo `sentence-transformers` a ser usado.
    """

    def __init__(self, memory: "AgentMemory", model_name: str = DEFAULT_MODEL_NAME) -> None:
        """
        Inicializa a camada de memória semântica.

        Args:
            memory: Instância de `AgentMemory` já populada (ou não) com
                `file_summaries`. Apenas a chave `state['file_summaries']`
                é lida; nenhum método privado de `AgentMemory` é usado.
            model_name: Nome do modelo do `sentence-transformers` usado
                para gerar os embeddings. Padrão: 'all-MiniLM-L6-v2'
                (~80 MB, bom equilíbrio entre qualidade e custo local).
        """
        self.memory = memory
        self.model_name = model_name

        # Carregados/preenchidos sob demanda (lazy).
        self._model: Optional[Any] = None
        self._paths: List[str] = []
        self._embeddings: Optional[Any] = None  # np.ndarray, shape (N, D)
        self._index_built: bool = False

    # ------------------------------------------------------------------
    # Carregamento lazy do modelo
    # ------------------------------------------------------------------
    def _get_model(self) -> Any:
        """
        Carrega (uma única vez) e retorna o modelo `SentenceTransformer`.

        O modelo é baixado automaticamente pela biblioteca na primeira
        execução (cache local do HuggingFace). Chamadas subsequentes
        reutilizam a instância já carregada em memória.

        Raises:
            ImportError: se `sentence-transformers` não estiver instalado.
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "A biblioteca 'sentence-transformers' não está instalada. "
                    "Instale com: pip install sentence-transformers"
                ) from exc
            logger.info("Carregando modelo de embeddings '%s'...", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    # ------------------------------------------------------------------
    # Construção do índice
    # ------------------------------------------------------------------
    def build_index(self) -> None:
        """
        Constrói (ou reconstrói) o índice vetorial a partir de todos os
        resumos presentes em `self.memory.state['file_summaries']`.

        Se não houver nenhum resumo disponível, o índice fica vazio e
        `find_similar_files` simplesmente retornará uma lista vazia,
        sem lançar exceção.
        """
        summaries = self.memory.state.get("file_summaries", {}) or {}

        self._paths = []
        self._embeddings = None
        self._index_built = True  # marca como "já tentamos construir"

        if not summaries:
            logger.info("Nenhum resumo em 'file_summaries'; índice semântico vazio.")
            return

        paths = list(summaries.keys())
        texts = [summaries[p] for p in paths]

        try:
            import numpy as np

            model = self._get_model()
            vectors = model.encode(
                texts,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True,  # simplifica a similaridade de cosseno
            )
            self._paths = paths
            self._embeddings = np.asarray(vectors, dtype="float32")
            logger.info("Índice semântico construído com %d arquivos.", len(paths))
        except ImportError:
            # Sem sentence-transformers/numpy disponível: falha de forma
            # controlada, deixando o índice vazio em vez de derrubar o agente.
            logger.warning(
                "Dependências ausentes para busca semântica; índice permanecerá vazio."
            )
            self._paths = []
            self._embeddings = None
        except Exception as exc:  # pragma: no cover - proteção defensiva
            logger.warning("Falha ao construir índice semântico: %s", exc)
            self._paths = []
            self._embeddings = None

    # ------------------------------------------------------------------
    # Busca
    # ------------------------------------------------------------------
    def find_similar_files(self, query: str, top_k: int = 5) -> List[str]:
        """
        Retorna os caminhos de arquivo cujos resumos são mais similares
        semanticamente à consulta fornecida.

        Constrói o índice automaticamente (via `build_index`) caso ele
        ainda não tenha sido criado nesta instância.

        Args:
            query: Texto de consulta (ex.: o objetivo do usuário).
            top_k: Número máximo de caminhos a retornar.

        Returns:
            Lista de caminhos de arquivo (strings), ordenados do mais
            para o menos similar. Lista vazia se o índice estiver vazio,
            se a consulta for vazia/inválida, ou se as dependências de
            embeddings não estiverem disponíveis.
        """
        if not query or not query.strip():
            return []

        if not self._index_built:
            self.build_index()

        if self._embeddings is None or not self._paths:
            return []

        try:
            import numpy as np

            model = self._get_model()
            query_vec = model.encode(
                [query],
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True,
            )[0].astype("float32")

            # Embeddings já normalizados -> produto escalar == similaridade de cosseno.
            scores = self._embeddings @ query_vec

            top_k = max(1, min(top_k, len(self._paths)))
            top_indices = np.argsort(-scores)[:top_k]

            return [self._paths[i] for i in top_indices]
        except ImportError:
            logger.warning("Dependências ausentes; busca semântica indisponível.")
            return []
        except Exception as exc:  # pragma: no cover - proteção defensiva
            logger.warning("Falha ao executar busca semântica: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Persistência opcional do índice
    # ------------------------------------------------------------------
    def save_index(self, path: str) -> None:
        """
        Persiste o índice atual (caminhos + embeddings) em disco, para
        evitar recalcular os embeddings em execuções futuras.

        Usa `pickle` para serializar `paths` e o array numpy de
        embeddings em um único arquivo. Se o índice ainda não tiver
        sido construído, ele é construído antes de salvar.

        Args:
            path: Caminho do arquivo de destino (ex.: 'semantic_index.pkl').
        """
        if not self._index_built:
            self.build_index()

        payload = {
            "model_name": self.model_name,
            "paths": self._paths,
            "embeddings": self._embeddings,  # pode ser None se índice vazio
        }
        try:
            with open(path, "wb") as f:
                pickle.dump(payload, f)
            logger.info("Índice semântico salvo em '%s'.", path)
        except Exception as exc:
            logger.warning("Não foi possível salvar o índice semântico: %s", exc)

    def load_index(self, path: str) -> bool:
        """
        Carrega um índice previamente salvo com `save_index`.

        Args:
            path: Caminho do arquivo salvo anteriormente.

        Returns:
            True se o índice foi carregado com sucesso, False caso
            contrário (arquivo inexistente, corrompido, ou modelo
            incompatível). Uma falha aqui nunca lança exceção — o
            chamador pode simplesmente cair de volta para
            `build_index()` caso `load_index` retorne False.
        """
        try:
            with open(path, "rb") as f:
                payload = pickle.load(f)

            if payload.get("model_name") != self.model_name:
                logger.warning(
                    "Índice salvo foi gerado com modelo diferente ('%s' != '%s'); "
                    "ignorando cache.",
                    payload.get("model_name"),
                    self.model_name,
                )
                return False

            self._paths = payload.get("paths", [])
            self._embeddings = payload.get("embeddings")
            self._index_built = True
            logger.info("Índice semântico carregado de '%s' (%d arquivos).", path, len(self._paths))
            return True
        except FileNotFoundError:
            logger.info("Arquivo de índice '%s' não encontrado.", path)
            return False
        except Exception as exc:
            logger.warning("Falha ao carregar índice semântico de '%s': %s", path, exc)
            return False


__all__ = ["SemanticMemory", "DEFAULT_MODEL_NAME"]