"""Primitivas de persistência JSON durável e atômica."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.memory.path_safety import LinkLikePathError, reject_link_like
from agent.runtime.logging import logger


class AtomicJsonWriteError(RuntimeError):
    """Indica que o destino não foi substituído pela nova versão JSON."""

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"Falha ao persistir JSON em {path}: {cause}")


class JsonObjectReadError(RuntimeError):
    """Indica que um arquivo esperado como objeto JSON não pôde ser lido."""

    def __init__(self, path: Path, cause: Exception | str) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"Falha ao carregar objeto JSON de {path}: {cause}")


def read_json_object(
    path: str | Path,
    *,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    """Lê um objeto JSON inteiro sem confundir ausência com corrupção."""

    source = Path(path)
    try:
        reject_link_like(source)
        with source.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except (
        LinkLikePathError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise JsonObjectReadError(source, exc) from exc
    if not isinstance(value, dict):
        raise JsonObjectReadError(source, "a raiz deve ser um objeto")
    return value


def write_json_atomic(path: str | Path, payload: Any) -> None:
    """Grava JSON em arquivo temporário e substitui o destino atomicamente."""

    destination = Path(path)
    temporary_path: Path | None = None
    try:
        reject_link_like(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        reject_link_like(destination)
        os.replace(temporary_path, destination)
        temporary_path = None
    except Exception as exc:
        raise AtomicJsonWriteError(destination, exc) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "Falha ao remover arquivo temporário de memória %s: %s",
                    temporary_path,
                    exc,
                )
