"""Pure representation of a persisted extension manifest path.

The model receives a path that was already made absolute by an administrative
boundary.  It validates the persisted lexical form but never consults the
filesystem, the process cwd, the user's home or environment variables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from types import NotImplementedType
from typing import Literal, cast

PathFlavor = Literal["windows", "posix"]
SUPPORTED_PATH_FLAVORS = frozenset(("windows", "posix"))
_WINDOWS_DRIVE = re.compile(r"[A-Za-z]:/")


def _validate_segments(segments: list[str], *, context: str) -> None:
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise ValueError(f"manifest_path {context} contém segmento não canônico")


def _validate_windows_path(value: str) -> None:
    if value.startswith(("//?/", "//./")):
        raise ValueError("manifest_path Windows não suporta device namespace")
    if value.startswith("//"):
        if value.startswith("///"):
            raise ValueError("manifest_path Windows UNC possui separadores repetidos")
        segments = value[2:].split("/")
        _validate_segments(segments, context="Windows UNC")
        if len(segments) < 2:
            raise ValueError("manifest_path Windows UNC requer servidor e share")
        if any(":" in segment for segment in segments):
            raise ValueError("manifest_path Windows UNC contém ':' inválido")
        return

    if _WINDOWS_DRIVE.match(value) is None:
        raise ValueError("manifest_path Windows deve ser drive absoluto ou UNC")
    if len(value) == 3:
        return
    segments = value[3:].split("/")
    _validate_segments(segments, context="Windows")
    if any(":" in segment for segment in segments):
        raise ValueError("manifest_path Windows contém ':' inválido")


def _validate_posix_path(value: str) -> None:
    if value == "/":
        return
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("manifest_path POSIX deve ser absoluto com uma única raiz")
    _validate_segments(value[1:].split("/"), context="POSIX")


def _validate_path(value: str, flavor: PathFlavor) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest_path deve ser uma string não vazia")
    if "\\" in value:
        raise ValueError("manifest_path persistido deve usar apenas '/'")
    if any(ord(character) < 32 for character in value):
        raise ValueError("manifest_path contém caractere de controle")
    if flavor == "windows":
        _validate_windows_path(value)
    else:
        _validate_posix_path(value)


def _validate_flavor(flavor: object) -> PathFlavor:
    if type(flavor) is not str:
        raise ValueError("manifest_path_flavor deve ser uma string exata")
    if flavor == "windows":
        return cast(PathFlavor, flavor)
    if flavor == "posix":
        return cast(PathFlavor, flavor)
    raise ValueError("manifest_path_flavor deve ser 'windows' ou 'posix'")


@dataclass(frozen=True, eq=False)
class PersistedManifestPath:
    """Immutable, host-independent persisted path representation."""

    value: str
    flavor: PathFlavor

    def __post_init__(self) -> None:
        flavor = _validate_flavor(self.flavor)
        _validate_path(self.value, flavor)

    @property
    def persisted_value(self) -> str:
        """Return the exact canonical text supplied to the model."""

        return self.value

    def _identity(self) -> tuple[PathFlavor, str]:
        return (self.flavor, self.comparison_key)

    def __eq__(self, other: object) -> bool | NotImplementedType:
        if not isinstance(other, PersistedManifestPath):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    @property
    def comparison_key(self) -> str:
        """Return a pure lexical key using this path's platform semantics."""

        if self.flavor == "windows":
            return PureWindowsPath(self.value).as_posix().lower()
        return PurePosixPath(self.value).as_posix()

    def equivalent_to(self, other: object) -> bool:
        """Compare paths only when their flavors and lexical keys agree."""

        return self == other

    def is_compatible_with(self, platform: str) -> bool:
        """Report compatibility for an explicitly supplied host flavor."""

        return self.flavor == _validate_flavor(platform)


__all__ = ["PathFlavor", "PersistedManifestPath", "SUPPORTED_PATH_FLAVORS"]
