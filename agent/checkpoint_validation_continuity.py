"""Validation of the optional schema-1 continuity checkpoint object."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from agent.checkpoint_types import CheckpointLoadError

CONTINUITY_SCHEMA_VERSION = 1
MAX_CONTINUITY_TEXT = 512
MAX_CONTINUITY_TIMESTAMP = 128
CONTINUITY_FIELDS = frozenset(
    {
        "schema_version",
        "resume_generation",
        "last_run_id",
        "resumed_from_run_id",
        "interrupted",
        "interruption_reason",
        "interrupted_at",
    }
)


def validate_continuity(path: Path, data: dict[str, Any]) -> None:
    """Validate additive continuity metadata without changing checkpoint v2."""

    raw = data.get("continuity")
    if raw is None:
        return
    if not isinstance(raw, dict):
        _invalid(path, "metadata de continuidade invalida")
    if any(key not in CONTINUITY_FIELDS for key in raw):
        _invalid(path, "metadata de continuidade contem campos desconhecidos")
    required = ("schema_version", "resume_generation", "last_run_id", "interrupted")
    if any(key not in raw for key in required):
        _invalid(path, "metadata de continuidade incompleta")
    version = raw["schema_version"]
    if isinstance(version, bool) or version != CONTINUITY_SCHEMA_VERSION:
        _invalid(
            path,
            f"versao de continuidade incompativel ({version!r})",
            reason_code="CHECKPOINT_INCOMPATIBLE_CONTINUITY_SCHEMA",
        )
    generation = raw["resume_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        _invalid(path, "geracao de retomada invalida")
    if not isinstance(raw["interrupted"], bool):
        _invalid(path, "marcador de interrupcao invalido")
    for name in ("last_run_id", "resumed_from_run_id", "interruption_reason"):
        _validate_optional_text(path, raw.get(name), name)
    _validate_optional_timestamp(path, raw.get("interrupted_at"))
    if not raw["interrupted"] and (
        raw.get("interruption_reason") is not None or raw.get("interrupted_at") is not None
    ):
        _invalid(path, "continuidade nao interrompida contem detalhes de interrupcao")


def _validate_optional_text(path: Path, value: Any, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CONTINUITY_TEXT:
        _invalid(path, f"{name} invalido")


def _validate_optional_timestamp(path: Path, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CONTINUITY_TIMESTAMP:
        _invalid(path, "interrupted_at invalido")
    try:
        datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        _invalid(path, "interrupted_at nao e um timestamp valido")


def _invalid(
    path: Path,
    detail: str,
    *,
    reason_code: str = "CHECKPOINT_INVALID_CONTINUITY",
) -> NoReturn:
    raise CheckpointLoadError(path, detail, reason_code=reason_code)


__all__ = [
    "CONTINUITY_FIELDS",
    "CONTINUITY_SCHEMA_VERSION",
    "MAX_CONTINUITY_TEXT",
    "MAX_CONTINUITY_TIMESTAMP",
    "validate_continuity",
]
