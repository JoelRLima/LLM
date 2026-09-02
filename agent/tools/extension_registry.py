"""Simple local registry for external tool extensions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agent.memory.json_persistence import write_json_atomic


@dataclass(frozen=True)
class ExtensionState:
    id: str
    manifest_path: Path
    enabled: bool


class ExtensionRegistry:
    """Persists enabled/disabled state for extensions in a JSON file."""

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path).expanduser().resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def add(self, *, id: str, manifest_path: str | Path, enabled: bool = True) -> ExtensionState:
        if not id or id.strip() != id:
            raise ValueError("ID de extensão inválido")
        state = ExtensionState(id=id, manifest_path=Path(manifest_path).expanduser().resolve(), enabled=enabled)
        if state.manifest_path.exists():
            from agent.tools.stdio_adapter import load_strict_extension_manifest

            manifest = load_strict_extension_manifest(state.manifest_path)
            if manifest.id != id:
                raise ValueError("manifest.id não corresponde ao ID registrado")
        self._data[state.id] = {
            "manifest_path": str(state.manifest_path),
            "enabled": state.enabled,
        }
        self._save()
        return state

    def get(self, id: str) -> Optional[ExtensionState]:
        item = self._data.get(id)
        if item is None:
            return None
        return ExtensionState(
            id=id,
            manifest_path=Path(item["manifest_path"]),
            enabled=bool(item.get("enabled", True)),
        )

    def set_enabled(self, id: str, enabled: bool) -> ExtensionState:
        if id not in self._data:
            raise KeyError(f"Extensão não registrada: {id}")
        self._data[id]["enabled"] = enabled
        self._save()
        state = self.get(id)
        if state is None:
            raise RuntimeError("Registro de extensão inconsistente")
        return state

    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.id for item in self._iter_states() if item.enabled))

    def list(self) -> tuple[ExtensionState, ...]:
        return tuple(self._iter_states())

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Registro de extensões inválido")
        self._data = {}
        for key, value in data.items():
            if not isinstance(value, dict) or not value.get("manifest_path"):
                raise ValueError("Registro de extensões contém entrada inválida")
            self._data[str(key)] = {
                "manifest_path": str(value["manifest_path"]),
                "enabled": bool(value.get("enabled", True)),
            }

    def _save(self) -> None:
        write_json_atomic(self.state_path, self._data)

    def _iter_states(self) -> tuple[ExtensionState, ...]:
        return tuple(
            ExtensionState(
                id=key,
                manifest_path=Path(value.get("manifest_path", "")),
                enabled=bool(value.get("enabled", True)),
            )
            for key, value in sorted(self._data.items())
        )
