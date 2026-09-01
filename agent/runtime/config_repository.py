"""Versioned configuration repository for standalone application startup."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.runtime.config_effective import apply_selected_profile_overrides
from agent.runtime.config_environment import environment_config
from agent.runtime.config_errors import ConfigError, ConfigNotFound, ConfigVersionError
from agent.runtime.config_schema import SCHEMA_VERSION, validate_config_document
from agent.runtime.paths import AppPaths

if TYPE_CHECKING:
    from agent.llm.model_profile import ResolvedModelProfile


def packaged_config_defaults(
    resource_package: str = "agent.resources",
    resource_name: str = "default_config.json",
) -> dict[str, Any]:
    """Load the authored, validated packaged configuration defaults once."""

    try:
        resource = resources.files(resource_package).joinpath(resource_name)
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError) as exc:
        raise ConfigError("Recurso de configuração default indisponível.") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Recurso de configuração default deve ser um objeto.")
    defaults = deepcopy(raw)
    validate_config_document(
        defaults,
        require_version=True,
        require_complete=True,
    )
    return defaults


@dataclass(frozen=True)
class ResolvedConfig:
    """Validated configuration, still carrying its explicit schema version."""

    _values: dict[str, Any]

    @property
    def schema_version(self) -> int:
        return int(self._values["schema_version"])

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._values)

    @property
    def model_profile(self) -> "ResolvedModelProfile":
        """Resolve the effective model profile through the canonical owner."""

        from agent.llm.model_profile import resolve_model_profile

        return resolve_model_profile(self._values)


class ConfigRepository:
    """Loads, initializes and migrates one application configuration file."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        *,
        config_path: str | Path | None = None,
        resource_package: str = "agent.resources",
        resource_name: str = "default_config.json",
    ) -> None:
        self.paths = paths or AppPaths.discover()
        self._config_path = (
            Path(config_path).expanduser().resolve()
            if config_path is not None
            else None
        )
        self.resource_package = resource_package
        self.resource_name = resource_name

    @property
    def path(self) -> Path:
        return self._config_path or Path(self.paths.config_file)

    def load(
        self,
        *,
        overrides: Mapping[str, Any] | None = None,
        environment: Mapping[str, str] | None = None,
        allow_missing: bool = False,
    ) -> ResolvedConfig:
        defaults = self._defaults()
        if self.path.is_file():
            file_values = self._read_document(self.path)
            validate_config_document(
                file_values,
                require_version=True,
                require_complete=False,
            )
        elif allow_missing:
            file_values = {}
        else:
            raise ConfigNotFound(f"Configuração não encontrada: {self.path}")

        environment_values = environment_config(
            os.environ if environment is None else environment
        )
        override_values = deepcopy(dict(overrides or {}))
        if "schema_version" in override_values:
            raise ConfigVersionError("CLI não pode sobrescrever 'schema_version'.")
        validate_config_document(
            environment_values,
            require_version=False,
            require_complete=False,
        )
        validate_config_document(
            override_values,
            require_version=False,
            require_complete=False,
        )
        resolved = self._merge(defaults, file_values)
        resolved = self._merge(resolved, environment_values)
        resolved = self._merge(resolved, override_values)
        apply_selected_profile_overrides(
            resolved,
            environment_values,
            override_values,
        )
        resolved["schema_version"] = SCHEMA_VERSION
        validate_config_document(
            resolved,
            require_version=True,
            require_complete=True,
        )
        return ResolvedConfig(resolved)

    def initialize(self) -> Path:
        """Create the packaged default once, without relying on the checkout."""

        if self.path.exists():
            self.load(environment={})
            return self.path
        self._write_atomic(self.path, self._defaults())
        return self.path

    def migrate(self, legacy_path: str | Path) -> Path:
        """Copy one explicit legacy file; the source is never changed or removed."""

        source = Path(legacy_path).expanduser().resolve()
        if source == self.path.resolve():
            raise ConfigError("Origem legada e destino de configuração são iguais.")
        legacy = self._read_document(source)
        self._remove_legacy_state_paths(legacy)
        version = legacy.get("schema_version")
        if version is None or version == 0:
            legacy["schema_version"] = SCHEMA_VERSION
        validate_config_document(
            legacy,
            require_version=True,
            require_complete=False,
        )
        normalized_legacy = self._normalize(legacy)
        if self.path.exists():
            existing = self._read_document(self.path)
            validate_config_document(
                existing,
                require_version=True,
                require_complete=False,
            )
            if self._normalize(existing) == normalized_legacy:
                return self.path
            raise ConfigError(
                f"Destino já possui configuração diferente: {self.path}"
            )
        self._write_atomic(self.path, legacy)
        return self.path

    @staticmethod
    def _remove_legacy_state_paths(document: dict[str, Any]) -> None:
        """Discard paths now derived from the selected workspace."""

        document.pop("checkpoint_file", None)
        task_report = document.get("task_report")
        if isinstance(task_report, dict):
            task_report.pop("output_dir", None)

    def _defaults(self) -> dict[str, Any]:
        return packaged_config_defaults(self.resource_package, self.resource_name)

    @classmethod
    def _merge(
        cls,
        base: Mapping[str, Any],
        overlay: Mapping[str, Any],
    ) -> dict[str, Any]:
        merged = deepcopy(dict(base))
        for key, value in overlay.items():
            current = merged.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                merged[key] = cls._merge(current, value)
            else:
                merged[key] = deepcopy(value)
        return merged

    def _normalize(self, document: Mapping[str, Any]) -> dict[str, Any]:
        merged = self._merge(self._defaults(), document)
        merged["schema_version"] = SCHEMA_VERSION
        validate_config_document(
            merged,
            require_version=True,
            require_complete=True,
        )
        return merged

    @staticmethod
    def _read_document(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ConfigNotFound(f"Configuração não encontrada: {path}")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ConfigError(f"Chave JSON duplicada: {key}")
                result[key] = value
            return result

        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=unique_object,
            )
        except json.JSONDecodeError as exc:
            raise ConfigError(f"JSON inválido em '{path}': {exc.msg}.") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"Configuração em '{path}' deve ser um objeto.")
        return value

    @staticmethod
    def _write_atomic(path: Path, document: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    document,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = [
    "ConfigError",
    "ConfigNotFound",
    "ConfigRepository",
    "ConfigVersionError",
    "ResolvedConfig",
    "SCHEMA_VERSION",
    "packaged_config_defaults",
]
