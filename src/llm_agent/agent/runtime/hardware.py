"""Perfis conservadores de hardware; nenhum deles exige CUDA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    context_limit: int
    default_output_tokens: int
    max_model_concurrency: int
    max_io_concurrency: int
    max_process_concurrency: int
    max_repair_attempts: int
    semantic_memory_default: bool


LOW_VRAM_8GB = HardwareProfile(
    name="low_vram_8gb",
    context_limit=8192,
    default_output_tokens=2048,
    max_model_concurrency=1,
    max_io_concurrency=2,
    max_process_concurrency=1,
    max_repair_attempts=2,
    semantic_memory_default=False,
)

BALANCED = HardwareProfile(
    name="balanced",
    context_limit=16384,
    default_output_tokens=4096,
    max_model_concurrency=1,
    max_io_concurrency=4,
    max_process_concurrency=2,
    max_repair_attempts=3,
    semantic_memory_default=True,
)

HARDWARE_PROFILES = {profile.name: profile for profile in (LOW_VRAM_8GB, BALANCED)}


def resolve_hardware_profile(config: Dict[str, Any]) -> HardwareProfile:
    name = str(config.get("hardware_profile", LOW_VRAM_8GB.name))
    return HARDWARE_PROFILES.get(name, LOW_VRAM_8GB)
