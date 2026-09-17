"""Release-build reproducibility primitives.

This module is the single owner of the W18 release epoch and of the byte-level
normalization used by the release builder.  It deliberately contains no
application-runtime behavior: the functions here operate only on build
artifacts and the isolated runtime assembled by the release boundary.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import re
import stat
import struct
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence


class ReproducibilityError(ValueError):
    """Raised when an artifact cannot be proved safe to canonicalize."""


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PE_SIGNATURE = b"PE\x00\x00"
_MZ_SIGNATURE = b"MZ"
_LOCAL_FILE_HEADER = b"PK\x03\x04"
_CENTRAL_FILE_HEADER = b"PK\x01\x02"
_END_OF_CENTRAL_DIRECTORY = b"PK\x05\x06"
_ZIP_FILE_HEADER_STRUCT = struct.Struct("<4s5H3L2H")
_ZIP_CENTRAL_DIRECTORY_STRUCT = struct.Struct("<4s6H3L5H2L")
_ZIP_END_OF_CENTRAL_DIRECTORY_STRUCT = struct.Struct("<4s4H2LH")

_ZIP_MINIMUM_YEAR = 1980
_ZIP_MINIMUM_EPOCH = 315532800  # 1980-01-01T00:00:00Z
_ZIP_MAXIMUM_EPOCH = 4354819198  # 2107-12-31T23:59:58Z
_ZIP_FILE_MODE = 0o100644
_ZIP_VERSION = 20
_ZIP_COMPRESSION = zipfile.ZIP_DEFLATED
_ZIP_COMPRESSION_LEVEL = 9


@dataclass(frozen=True)
class CanonicalReleaseEpoch:
    """The build epoch derived from the frozen source base commit."""

    source_base_commit: str
    source_date_epoch: int
    zip_datetime: tuple[int, int, int, int, int, int]

    @property
    def utc_iso(self) -> str:
        """Return the original commit timestamp rendered in UTC."""

        return datetime.fromtimestamp(self.source_date_epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_commit(value: str) -> str:
    if not isinstance(value, str) or not _HEX40.fullmatch(value):
        raise ReproducibilityError("source.base_commit must be a 40-character lowercase Git object id")
    return value


def _zip_datetime_for_epoch(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int):
        raise ReproducibilityError("source date epoch must be an integer Unix timestamp")
    bounded = min(max(source_date_epoch, _ZIP_MINIMUM_EPOCH), _ZIP_MAXIMUM_EPOCH)
    # ZIP/DOS time has two-second precision.  The integer arithmetic is
    # timezone-independent and also behaves deterministically for pre-1980
    # timestamps after the lower bound has been applied.
    bounded -= bounded % 2
    value = datetime.fromtimestamp(bounded, timezone.utc)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)


def make_release_epoch(source_base_commit: str, source_date_epoch: int) -> CanonicalReleaseEpoch:
    """Create the canonical epoch value after validating its source identity."""

    return CanonicalReleaseEpoch(
        source_base_commit=_validate_commit(source_base_commit),
        source_date_epoch=source_date_epoch,
        zip_datetime=_zip_datetime_for_epoch(source_date_epoch),
    )


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in ("GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(variable, None)
    return environment


def derive_release_epoch(repository_root: Path, source_base_commit: str) -> CanonicalReleaseEpoch:
    """Derive the stable epoch from the base commit's UTC committer timestamp."""

    source_base_commit = _validate_commit(source_base_commit)
    try:
        completed = subprocess.run(
            ["git", "show", "-s", "--format=%ct", "--no-patch", source_base_commit],
            cwd=repository_root,
            env=_git_environment(),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise ReproducibilityError(f"cannot read source base commit timestamp: {source_base_commit}\n{detail[-2000:]}") from exc
    raw_timestamp = completed.stdout.strip()
    try:
        timestamp = int(raw_timestamp, 10)
    except ValueError as exc:
        raise ReproducibilityError(
            f"Git returned a non-integer timestamp for source base commit {source_base_commit}: {raw_timestamp!r}"
        ) from exc
    return make_release_epoch(source_base_commit, timestamp)


def reproducible_build_environment(
    epoch: CanonicalReleaseEpoch,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a subprocess environment with volatile build inputs normalized."""

    environment = dict(base) if base is not None else os.environ.copy()
    for variable in (
        "SOURCE_DATE_EPOCH",
        "PYTHONHASHSEED",
        "PYTHONUTF8",
        "PYTHONNOUSERSITE",
        "PYTHONUSERBASE",
        "TZ",
        "LC_ALL",
        "LANG",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "SOURCE_DATE_EPOCH": str(epoch.source_date_epoch),
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "TZ": "UTC",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _lstat_source_snapshot_path(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ReproducibilityError(f"cannot inspect source snapshot path: {path}") from exc


def _reject_source_snapshot_linklike(path: Path, metadata: os.stat_result) -> None:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & reparse_attribute):
        raise ReproducibilityError(f"source snapshot contains a symlink/reparse point: {path}")


def _source_snapshot_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    directories = [root]
    while directories:
        directory = directories.pop()
        directory_metadata = _lstat_source_snapshot_path(directory)
        _reject_source_snapshot_linklike(directory, directory_metadata)
        child_directories: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    metadata = _lstat_source_snapshot_path(path)
                    _reject_source_snapshot_linklike(path, metadata)
                    paths.append(path)
                    if stat.S_ISDIR(metadata.st_mode):
                        child_directories.append(path)
        except OSError as exc:
            raise ReproducibilityError(f"cannot inspect source snapshot directory: {directory}") from exc
        directories.extend(child_directories)
    return sorted(paths, key=lambda path: (len(path.parts), path.as_posix()), reverse=True)


def normalize_tree_mtimes(root: Path, epoch: CanonicalReleaseEpoch) -> None:
    """Normalize isolated source-snapshot mtimes before a build backend sees them."""

    root = root.absolute()
    root_metadata = _lstat_source_snapshot_path(root)
    _reject_source_snapshot_linklike(root, root_metadata)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ReproducibilityError(f"source snapshot is not a directory: {root}")
    paths = _source_snapshot_paths(root)
    paths.append(root)
    times = (epoch.source_date_epoch, epoch.source_date_epoch)
    supports_follow_symlinks = os.utime in os.supports_follow_symlinks
    for path in paths:
        try:
            if supports_follow_symlinks:
                os.utime(path, times, follow_symlinks=False)
            else:
                # The complete pre-scan rejects links/reparse points.  This
                # plain call is therefore safe on platforms whose utime API
                # has no follow_symlinks capability.
                os.utime(path, times)
        except OSError as exc:
            raise ReproducibilityError(f"cannot normalize source snapshot mtime: {path}") from exc


def _validate_zip_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ReproducibilityError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ReproducibilityError(f"unsafe ZIP member name: {name!r}")
    return name


def canonical_zip_info(name: str, epoch: CanonicalReleaseEpoch) -> zipfile.ZipInfo:
    """Create a regular-file ZipInfo with fully explicit metadata."""

    name = _validate_zip_member_name(name)
    info = zipfile.ZipInfo(name, date_time=epoch.zip_datetime)
    info.compress_type = _ZIP_COMPRESSION
    info.create_system = 3
    info.create_version = _ZIP_VERSION
    info.extract_version = _ZIP_VERSION
    info.flag_bits = 0
    info.volume = 0
    info.internal_attr = 0
    info.external_attr = _ZIP_FILE_MODE << 16
    info.extra = b""
    info.comment = b""
    return info


def canonical_zip_bytes(members: Iterable[tuple[str, bytes]], epoch: CanonicalReleaseEpoch) -> bytes:
    """Render sorted regular-file members into a byte-stable ZIP archive."""

    ordered = sorted(((_validate_zip_member_name(name), bytes(content)) for name, content in members), key=lambda item: item[0])
    names = [name for name, _ in ordered]
    if len(names) != len(set(names)):
        raise ReproducibilityError("ZIP contains duplicate member names")
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=_ZIP_COMPRESSION,
        compresslevel=_ZIP_COMPRESSION_LEVEL,
        strict_timestamps=True,
    ) as archive:
        archive.comment = b""
        for name, content in ordered:
            archive.writestr(canonical_zip_info(name, epoch), content)
    return output.getvalue()


def write_canonical_zip(
    destination: Path,
    members: Iterable[tuple[str, bytes]],
    epoch: CanonicalReleaseEpoch,
) -> None:
    """Write a canonical ZIP to *destination* without reading filesystem mtimes."""

    destination.write_bytes(canonical_zip_bytes(members, epoch))


def validate_canonical_zip(
    path: Path,
    epoch: CanonicalReleaseEpoch,
    expected_members: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Validate canonical ordering, timestamps, permissions, and ZIP options."""

    try:
        with zipfile.ZipFile(path, "r") as archive:
            if archive.comment != b"":
                raise ReproducibilityError(f"canonical ZIP has a comment: {path}")
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if names != tuple(sorted(names)) or len(names) != len(set(names)):
                raise ReproducibilityError(f"canonical ZIP member ordering is unstable: {path}")
            if expected_members is not None and names != tuple(sorted(expected_members)):
                raise ReproducibilityError(f"canonical ZIP member set differs from expectation: {path}")
            for info in infos:
                expected = canonical_zip_info(info.filename, epoch)
                expected_flags = 0 if info.filename.isascii() else 0x800
                if (
                    info.date_time != expected.date_time
                    or info.compress_type != expected.compress_type
                    or info.create_system != expected.create_system
                    or info.create_version != expected.create_version
                    or info.extract_version != expected.extract_version
                    or info.flag_bits != expected_flags
                    or info.volume != expected.volume
                    or info.internal_attr != expected.internal_attr
                    or info.external_attr != expected.external_attr
                    or info.extra != expected.extra
                    or info.comment != expected.comment
                ):
                    raise ReproducibilityError(f"canonical ZIP metadata differs: {path}:{info.filename}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReproducibilityError(f"cannot inspect canonical ZIP: {path}") from exc
    return names


def _sha256_base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def _safe_record_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReproducibilityError(f"unsafe RECORD path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", "."} for part in value.split("/")):
        raise ReproducibilityError(f"unsafe RECORD path: {value!r}")
    return value


def _record_base(record_path: Path, runtime_root: Path, rows: Sequence[Sequence[str]]) -> Path:
    """Find the install root from the RECORD row that names RECORD itself."""

    runtime_root = runtime_root.resolve()
    record_path = record_path.resolve()
    ancestors: list[Path] = []
    current = record_path.parent
    while current == runtime_root or runtime_root in current.parents:
        ancestors.append(current)
        if current == runtime_root:
            break
        current = current.parent
    for base in ancestors:
        relative = record_path.relative_to(base).as_posix()
        if any(row and row[0] == relative for row in rows):
            return base
    raise ReproducibilityError(f"RECORD does not contain a self-relative row: {record_path}")


def _record_target(base: Path, value: str, runtime_root: Path) -> Path:
    value = _safe_record_path(value)
    raw_candidate = base / Path(*PurePosixPath(value).parts)
    if raw_candidate.is_symlink():
        raise ReproducibilityError(f"RECORD path is a symlink: {value}")
    candidate = raw_candidate.resolve()
    root = runtime_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ReproducibilityError(f"RECORD path escapes runtime root: {value}")
    return candidate


def _read_record(path: Path) -> list[list[str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ReproducibilityError(f"cannot read RECORD: {path}") from exc
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if any(len(row) != 3 for row in rows):
        raise ReproducibilityError(f"RECORD row shape is invalid: {path}")
    if not rows:
        raise ReproducibilityError(f"RECORD is empty: {path}")
    return rows


def _record_rows_for_files(
    record_path: Path,
    runtime_root: Path,
    rows: Sequence[Sequence[str]],
    removed_paths: set[Path],
) -> list[list[str]]:
    base = _record_base(record_path, runtime_root, rows)
    normalized: list[list[str]] = []
    seen: set[str] = set()
    for row in rows:
        relative = _safe_record_path(row[0])
        if relative in seen:
            raise ReproducibilityError(f"duplicate RECORD path {relative}: {record_path}")
        seen.add(relative)
        target = _record_target(base, relative, runtime_root)
        if not target.is_file():
            if target in removed_paths:
                continue
            raise ReproducibilityError(f"RECORD references a missing file: {record_path}: {relative}")
        if target == record_path.resolve():
            normalized.append([relative, "", ""])
            continue
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ReproducibilityError(f"cannot read RECORD member: {target}") from exc
        normalized.append([relative, f"sha256={_sha256_base64(data)}", str(len(data))])
    if not any(row[0] == record_path.resolve().relative_to(base).as_posix() for row in normalized):
        raise ReproducibilityError(f"RECORD self-entry is missing: {record_path}")
    normalized.sort(key=lambda row: row[0])
    return normalized


def _render_record(rows: Sequence[Sequence[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def reconcile_dist_info_records(
    runtime_root: Path,
    removed_paths: Iterable[Path] = (),
) -> tuple[Path, ...]:
    """Rewrite every runtime RECORD with hashes for the final runtime bytes."""

    runtime_root = runtime_root.resolve()
    if not runtime_root.is_dir():
        raise ReproducibilityError(f"runtime root is not a directory: {runtime_root}")
    removed = {path.resolve() for path in removed_paths}
    records = tuple(sorted(runtime_root.rglob("RECORD"), key=lambda path: path.relative_to(runtime_root).as_posix()))
    changed: list[Path] = []
    for record_path in records:
        if not record_path.parent.name.casefold().endswith(".dist-info"):
            continue
        rows = _read_record(record_path)
        canonical = _render_record(_record_rows_for_files(record_path, runtime_root, rows, removed))
        if record_path.read_bytes() != canonical:
            record_path.write_bytes(canonical)
            changed.append(record_path)
    validate_dist_info_records(runtime_root)
    return tuple(changed)


def validate_dist_info_records(runtime_root: Path) -> None:
    """Fail closed unless every surviving runtime RECORD matches disk bytes."""

    runtime_root = runtime_root.resolve()
    if not runtime_root.is_dir():
        raise ReproducibilityError(f"runtime root is not a directory: {runtime_root}")
    for record_path in sorted(runtime_root.rglob("RECORD"), key=lambda path: path.relative_to(runtime_root).as_posix()):
        if not record_path.parent.name.casefold().endswith(".dist-info"):
            continue
        rows = _read_record(record_path)
        normalized = _record_rows_for_files(record_path, runtime_root, rows, set())
        if _render_record(normalized) != record_path.read_bytes():
            raise ReproducibilityError(f"stale or non-canonical RECORD: {record_path}")


def _parse_wheel_record(archive: zipfile.ZipFile) -> tuple[tuple[str, ...], str, list[list[str]]]:
    names = tuple(archive.namelist())
    record_names = tuple(name for name in names if name.endswith(".dist-info/RECORD"))
    if len(record_names) != 1:
        raise ReproducibilityError("wheel must contain exactly one dist-info/RECORD")
    record_name = record_names[0]
    content = archive.read(record_name).decode("utf-8")
    rows = list(csv.reader(io.StringIO(content, newline="")))
    return names, record_name, rows


def _validate_wheel_record_member(
    archive: zipfile.ZipFile,
    name: str,
    digest: str,
    size: str,
) -> None:
    try:
        content = archive.read(name)
    except KeyError as exc:
        raise ReproducibilityError(f"wheel RECORD references a missing member: {name}") from exc
    if digest != f"sha256={_sha256_base64(content)}" or size != str(len(content)):
        raise ReproducibilityError(f"wheel RECORD hash/size mismatch: {name}")


def _validate_wheel_record_row(
    archive: zipfile.ZipFile,
    expected_self: str,
    row: list[str],
    seen: set[str],
) -> None:
    if len(row) != 3:
        raise ReproducibilityError("wheel RECORD row shape is invalid")
    name, digest, size = row
    _validate_zip_member_name(name)
    if name in seen:
        raise ReproducibilityError(f"wheel RECORD contains a duplicate path: {name}")
    seen.add(name)
    if name == expected_self:
        if digest or size:
            raise ReproducibilityError("wheel RECORD self-entry must have empty hash and size")
        return
    _validate_wheel_record_member(archive, name, digest, size)


def _validate_wheel_record_completeness(names: tuple[str, ...], expected_self: str, seen: set[str]) -> None:
    if expected_self not in seen:
        raise ReproducibilityError("wheel RECORD self-entry is missing")
    if set(seen) != set(names):
        raise ReproducibilityError("wheel contains a member absent from RECORD")


def validate_wheel_records(archive: zipfile.ZipFile) -> None:
    """Validate wheel RECORD hashes against the member bytes in *archive*."""

    names, record_name, rows = _parse_wheel_record(archive)
    seen: set[str] = set()
    for row in rows:
        _validate_wheel_record_row(archive, record_name, row, seen)
    _validate_wheel_record_completeness(names, record_name, seen)


def canonicalize_wheel(path: Path, epoch: CanonicalReleaseEpoch) -> None:
    """Canonicalize wheel ZIP metadata while preserving all member bytes."""

    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = tuple(archive.namelist())
            if len(names) != len(set(names)):
                raise ReproducibilityError("wheel contains duplicate members")
            members = [(name, archive.read(name)) for name in names]
            validate_wheel_records(archive)
            record_names = tuple(name for name in names if name.endswith(".dist-info/RECORD"))
            record_name = record_names[0]
            record_rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"), newline="")))
            canonical_record = _render_record(sorted(record_rows, key=lambda row: row[0]))
            members = [
                (name, canonical_record if name == record_name else content)
                for name, content in members
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReproducibilityError(f"cannot read application wheel: {path}") from exc
    path.write_bytes(canonical_zip_bytes(members, epoch))
    try:
        with zipfile.ZipFile(path, "r") as archive:
            validate_wheel_records(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReproducibilityError(f"canonicalized wheel cannot be reopened: {path}") from exc


@dataclass(frozen=True)
class _EmbeddedZip:
    data: bytes
    archive: zipfile.ZipFile
    zip_start: int
    central_start: int
    central_size: int
    end_record_offset: int


_PE_DOS_E_LFANEW_OFFSET = 0x3C
_PE_DOS_MINIMUM_SIZE = _PE_DOS_E_LFANEW_OFFSET + 4
_PE_COFF_HEADER_SIZE = 20
_PE_OPTIONAL_HEADER_MINIMUM_SIZE = 64
_PE_SECTION_HEADER_SIZE = 40
_PE_OPTIONAL_FILE_ALIGNMENT_OFFSET = 36
_PE_OPTIONAL_SIZE_OF_HEADERS_OFFSET = 60
_PE32_MAGIC = 0x10B
_PE32_PLUS_MAGIC = 0x20B
_ZIP_LOCAL_TIME_OFFSET = 10
_ZIP_CENTRAL_TIME_OFFSET = 12
_DISTLIB_PYTHON_EXECUTABLE = re.compile(r"(?i)pythonw?[0-9.]*\.exe\Z")


def _pe_header_offsets(data: bytes) -> tuple[int, int] | None:
    if len(data) < _PE_DOS_MINIMUM_SIZE or data[:2] != _MZ_SIGNATURE:
        return None
    pe_offset = struct.unpack_from("<L", data, _PE_DOS_E_LFANEW_OFFSET)[0]
    coff_offset = pe_offset + len(_PE_SIGNATURE)
    if pe_offset < _PE_DOS_MINIMUM_SIZE or coff_offset + _PE_COFF_HEADER_SIZE > len(data):
        return None
    if data[pe_offset:coff_offset] != _PE_SIGNATURE:
        return None
    return pe_offset, coff_offset


def _pe_optional_layout(data: bytes, coff_offset: int) -> tuple[int, int, int] | None:
    _, section_count, _, _, _, optional_size, _ = struct.unpack_from("<HHLLLHH", data, coff_offset)
    optional_start = coff_offset + _PE_COFF_HEADER_SIZE
    optional_end = optional_start + optional_size
    if not section_count or optional_size < _PE_OPTIONAL_HEADER_MINIMUM_SIZE or optional_end > len(data):
        return None
    optional_magic = struct.unpack_from("<H", data, optional_start)[0]
    if optional_magic not in {_PE32_MAGIC, _PE32_PLUS_MAGIC}:
        return None
    file_alignment = struct.unpack_from("<L", data, optional_start + _PE_OPTIONAL_FILE_ALIGNMENT_OFFSET)[0]
    size_headers = struct.unpack_from("<L", data, optional_start + _PE_OPTIONAL_SIZE_OF_HEADERS_OFFSET)[0]
    if not _valid_pe_alignment(file_alignment, size_headers, len(data)):
        return None
    return optional_end, file_alignment, size_headers


def _valid_pe_alignment(file_alignment: int, size_headers: int, data_size: int) -> bool:
    return bool(
        file_alignment
        and file_alignment <= 0x10000
        and not file_alignment & (file_alignment - 1)
        and size_headers
        and not size_headers % file_alignment
        and size_headers <= data_size
    )


def _pe_section_ranges(
    data: bytes,
    section_table_start: int,
    size_headers: int,
    file_alignment: int,
    section_count: int,
) -> list[tuple[int, int]] | None:
    section_table_end = section_table_start + section_count * _PE_SECTION_HEADER_SIZE
    if section_table_end > size_headers or section_table_end > len(data):
        return None
    raw_ranges: list[tuple[int, int]] = []
    for index in range(section_count):
        section_start = section_table_start + index * _PE_SECTION_HEADER_SIZE
        raw_range = _pe_section_range(data, section_start, size_headers, file_alignment)
        if raw_range is None:
            return None
        if raw_range != (0, 0):
            raw_ranges.append(raw_range)
    return raw_ranges or None


def _pe_section_range(
    data: bytes,
    section_start: int,
    size_headers: int,
    file_alignment: int,
) -> tuple[int, int] | None:
    raw_size = struct.unpack_from("<L", data, section_start + 16)[0]
    raw_pointer = struct.unpack_from("<L", data, section_start + 20)[0]
    if not raw_size:
        return 0, 0
    if raw_pointer < size_headers or raw_pointer % file_alignment or raw_size % file_alignment:
        return None
    raw_end = raw_pointer + raw_size
    if raw_end > len(data):
        return None
    return raw_pointer, raw_end


def _pe_image_extent(raw_ranges: list[tuple[int, int]], size_headers: int) -> int | None:
    raw_ranges.sort()
    image_end = size_headers
    for raw_start, raw_end in raw_ranges:
        if raw_start < image_end:
            return None
        image_end = raw_end
    return image_end


def _pe_image_end(data: bytes) -> int | None:
    """Return the end of the PE image before any distlib overlay bytes."""

    offsets = _pe_header_offsets(data)
    if offsets is None:
        return None
    _, coff_offset = offsets
    section_count = struct.unpack_from("<H", data, coff_offset + 2)[0]
    optional = _pe_optional_layout(data, coff_offset)
    if optional is None:
        return None
    section_table_start, file_alignment, size_headers = optional
    ranges = _pe_section_ranges(data, section_table_start, size_headers, file_alignment, section_count)
    return None if ranges is None else _pe_image_extent(ranges, size_headers)


def _is_distlib_shebang(prefix: bytes) -> bool:
    if (
        not prefix.startswith(b"#!")
        or not prefix.endswith(b"\n")
        or b"\n" in prefix[:-1]
        or b"\r" in prefix
        or b"\x00" in prefix
    ):
        return False
    try:
        line = prefix[2:-1].decode("utf-8")
    except UnicodeDecodeError:
        return False

    if line.startswith('"'):
        closing_quote = line.find('"', 1)
        if closing_quote <= 1:
            return False
        executable = line[1:closing_quote]
        remainder = line[closing_quote + 1 :]
        if remainder and remainder[0] not in " \t":
            return False
    else:
        match = re.fullmatch(r"(?P<executable>[^ \t\r\n]+)(?:[ \t].*)?", line)
        if match is None:
            return False
        executable = match.group("executable")
    executable_name = re.split(r"[\\/]", executable)[-1]
    return bool(_DISTLIB_PYTHON_EXECUTABLE.fullmatch(executable_name))


def _is_distlib_main_body(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = text.splitlines(keepends=True)
    if len(lines) != 7 or lines[:3] != [
        "# -*- coding: utf-8 -*-\n",
        "import re\n",
        "import sys\n",
    ]:
        return False
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    import_match = re.fullmatch(
        rf"from (?P<module>{identifier}(?:\.{identifier})*) import (?P<import_name>{identifier})\n",
        lines[3],
    )
    if import_match is None or lines[4] != "if __name__ == '__main__':\n":
        return False
    if lines[5] != r"    sys.argv[0] = re.sub(r'(-script\.pyw|\.exe)?$', '', sys.argv[0])" + "\n":
        return False
    call_match = re.fullmatch(
        rf"    sys\.exit\((?P<function>{identifier}(?:\.{identifier})*)\(\)\)\n",
        lines[6],
    )
    return call_match is not None and call_match.group("function").split(".")[0] == import_match.group("import_name")


def _validate_local_header(
    data: bytes | bytearray,
    info: zipfile.ZipInfo,
    zip_start: int,
    central_start: int,
) -> tuple[int, int]:
    offset = info.header_offset
    if offset < zip_start or offset + _ZIP_FILE_HEADER_STRUCT.size > central_start:
        raise ReproducibilityError("distlib launcher local ZIP header is outside the embedded archive")
    header = _ZIP_FILE_HEADER_STRUCT.unpack_from(data, offset)
    (
        signature,
        version_needed,
        flags,
        compression,
        _,
        _,
        crc,
        compressed_size,
        uncompressed_size,
        filename_size,
        extra_size,
    ) = header
    if signature != _LOCAL_FILE_HEADER:
        raise ReproducibilityError("distlib launcher local ZIP header signature is invalid")
    if flags & 0x09:
        raise ReproducibilityError("distlib launcher local ZIP header uses unsupported flags")
    if version_needed != info.extract_version or flags != info.flag_bits or compression != info.compress_type:
        raise ReproducibilityError("distlib launcher local/central ZIP flags disagree")
    if crc != info.CRC or compressed_size != info.compress_size or uncompressed_size != info.file_size:
        raise ReproducibilityError("distlib launcher local/central ZIP sizes disagree")
    name_start = offset + _ZIP_FILE_HEADER_STRUCT.size
    name_end = name_start + filename_size
    data_start = name_end + extra_size
    data_end = data_start + compressed_size
    if data_end > central_start:
        raise ReproducibilityError("distlib launcher local ZIP member overlaps the central directory")
    try:
        local_name = data[name_start:name_end].decode("utf-8" if flags & 0x800 else "cp437")
    except UnicodeDecodeError as exc:
        raise ReproducibilityError("distlib launcher local ZIP filename is not decodable") from exc
    if local_name != info.filename or data[name_end:data_start] != info.extra:
        raise ReproducibilityError("distlib launcher local/central ZIP filenames disagree")
    return offset, data_end


def _validate_central_header(
    data: bytes | bytearray,
    info: zipfile.ZipInfo,
    position: int,
    zip_start: int,
    central_start: int,
    end_record_offset: int,
    local_offset: int,
) -> int:
    if position + _ZIP_CENTRAL_DIRECTORY_STRUCT.size > end_record_offset:
        raise ReproducibilityError("distlib launcher central directory is truncated")
    header = _ZIP_CENTRAL_DIRECTORY_STRUCT.unpack_from(data, position)
    (
        signature,
        _,
        version_needed,
        flags,
        compression,
        _,
        _,
        crc,
        compressed_size,
        uncompressed_size,
        filename_size,
        extra_size,
        comment_size,
        disk_start,
        internal_attr,
        external_attr,
        relative_offset,
    ) = header
    if signature != _CENTRAL_FILE_HEADER:
        raise ReproducibilityError("distlib launcher central ZIP header signature is invalid")
    if (
        version_needed != info.extract_version
        or flags != info.flag_bits
        or compression != info.compress_type
        or crc != info.CRC
        or compressed_size != info.compress_size
        or uncompressed_size != info.file_size
        or disk_start != info.volume
        or internal_attr != info.internal_attr
        or external_attr != info.external_attr
        or relative_offset != local_offset - zip_start
    ):
        raise ReproducibilityError("distlib launcher central/local ZIP metadata disagree")
    name_start = position + _ZIP_CENTRAL_DIRECTORY_STRUCT.size
    name_end = name_start + filename_size
    extra_end = name_end + extra_size
    comment_end = extra_end + comment_size
    if comment_end > end_record_offset:
        raise ReproducibilityError("distlib launcher central directory entry is truncated")
    try:
        central_name = data[name_start:name_end].decode("utf-8" if flags & 0x800 else "cp437")
    except UnicodeDecodeError as exc:
        raise ReproducibilityError("distlib launcher central ZIP filename is not decodable") from exc
    if (
        central_name != info.filename
        or data[name_end:extra_end] != info.extra
        or data[extra_end:comment_end] != info.comment
    ):
        raise ReproducibilityError("distlib launcher central ZIP filename disagrees with ZipInfo")
    if position < central_start:
        raise ReproducibilityError("distlib launcher central directory starts before the ZIP archive")
    return comment_end


def _validate_embedded_zip_layout(embedded: _EmbeddedZip) -> None:
    infos = embedded.archive.infolist()
    if not infos:
        raise ReproducibilityError("distlib launcher embedded ZIP is empty")
    position = embedded.central_start
    local_ranges: list[tuple[int, int]] = []
    for info in infos:
        local_offset, data_end = _validate_local_header(
            embedded.data,
            info,
            embedded.zip_start,
            embedded.central_start,
        )
        position = _validate_central_header(
            embedded.data,
            info,
            position,
            embedded.zip_start,
            embedded.central_start,
            embedded.end_record_offset,
            local_offset,
        )
        local_ranges.append((local_offset, data_end))
    if position != embedded.end_record_offset:
        raise ReproducibilityError("distlib launcher central directory has an unexpected trailing layout")
    if local_ranges != sorted(local_ranges) or local_ranges[0][0] != embedded.zip_start:
        raise ReproducibilityError("distlib launcher local ZIP members are not ordered")
    for (_, previous_end), (next_start, _) in zip(local_ranges, local_ranges[1:], strict=False):
        if previous_end != next_start:
            raise ReproducibilityError("distlib launcher ZIP members have unexplained padding or overlap")
    if local_ranges[-1][1] != embedded.central_start:
        raise ReproducibilityError("distlib launcher ZIP central directory is not adjacent to its members")


def _open_embedded_archive(
    data: bytes,
) -> tuple[zipfile.ZipFile, tuple[zipfile.ZipInfo, ...]] | None:
    stream = io.BytesIO(data)
    try:
        archive = zipfile.ZipFile(stream, "r")
    except NotImplementedError as exc:
        raise ReproducibilityError("distlib launcher local/central ZIP metadata disagree") from exc
    except (OSError, zipfile.BadZipFile):
        return None
    try:
        infos = tuple(archive.infolist())
    except (OSError, zipfile.BadZipFile):
        archive.close()
        return None
    return archive, infos


def _embedded_zip_end_metadata(
    data: bytes,
    archive: zipfile.ZipFile,
    infos: tuple[zipfile.ZipInfo, ...],
) -> tuple[int, int, int, int] | None:
    if len(infos) != 1 or archive.comment:
        return None
    end_record_offset = len(data) - _ZIP_END_OF_CENTRAL_DIRECTORY_STRUCT.size
    if end_record_offset < 0 or data[end_record_offset : end_record_offset + 4] != _END_OF_CENTRAL_DIRECTORY:
        return None
    end = _ZIP_END_OF_CENTRAL_DIRECTORY_STRUCT.unpack_from(data, end_record_offset)
    _, disk, central_disk, entries_disk, entries_total, central_size, central_offset, comment_size = end
    if (
        disk
        or central_disk
        or entries_disk != entries_total
        or entries_total != len(infos)
        or comment_size
        or end_record_offset + _ZIP_END_OF_CENTRAL_DIRECTORY_STRUCT.size != len(data)
    ):
        return None
    return end_record_offset, central_size, central_offset, archive.start_dir


def _embedded_zip_start(
    pe_end: int,
    infos: tuple[zipfile.ZipInfo, ...],
    end_record_offset: int,
    central_size: int,
    central_offset: int,
    central_start: int,
) -> int | None:
    if central_start < 0 or central_start + central_size != end_record_offset:
        return None
    zip_start = central_start - central_offset
    offsets = [info.header_offset for info in infos]
    if zip_start <= pe_end or min(offsets) != zip_start or len(offsets) != len(set(offsets)):
        return None
    if any(offset < zip_start for offset in offsets):
        return None
    return zip_start


def _read_distlib_main_body(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    if info.filename != "__main__.py" or info.filename.endswith("/") or info.extra or info.comment:
        return None
    try:
        return archive.read(info)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        if isinstance(exc, zipfile.BadZipFile):
            return None
        raise ReproducibilityError("cannot read distlib launcher __main__.py") from exc
    except RuntimeError as exc:
        raise ReproducibilityError("cannot read distlib launcher __main__.py") from exc


def _open_embedded_zip(data: bytes) -> _EmbeddedZip | None:
    pe_end = _pe_image_end(data)
    if pe_end is None:
        return None
    opened = _open_embedded_archive(data)
    if opened is None:
        return None
    archive, infos = opened
    metadata = _embedded_zip_end_metadata(data, archive, infos)
    if metadata is None:
        archive.close()
        return None
    end_record_offset, central_size, central_offset, central_start = metadata
    zip_start = _embedded_zip_start(
        pe_end,
        infos,
        end_record_offset,
        central_size,
        central_offset,
        central_start,
    )
    if zip_start is None or not _is_distlib_shebang(data[pe_end:zip_start]):
        archive.close()
        return None
    try:
        main_body = _read_distlib_main_body(archive, infos[0])
    except ReproducibilityError:
        archive.close()
        raise
    if main_body is None or not _is_distlib_main_body(main_body):
        archive.close()
        return None

    embedded = _EmbeddedZip(data, archive, zip_start, central_start, central_size, end_record_offset)
    try:
        _validate_embedded_zip_layout(embedded)
    except ReproducibilityError:
        archive.close()
        raise
    return embedded


def _canonicalize_embedded_zip(embedded: _EmbeddedZip, epoch: CanonicalReleaseEpoch) -> bytes:
    data = bytearray(embedded.data)
    dos_time = (epoch.zip_datetime[3] << 11) | (epoch.zip_datetime[4] << 5) | (epoch.zip_datetime[5] // 2)
    dos_date = ((epoch.zip_datetime[0] - _ZIP_MINIMUM_YEAR) << 9) | (epoch.zip_datetime[1] << 5) | epoch.zip_datetime[2]
    _validate_embedded_zip_layout(embedded)
    infos = embedded.archive.infolist()
    local_offsets: list[int] = []
    for info in infos:
        local_offset, _ = _validate_local_header(data, info, embedded.zip_start, embedded.central_start)
        local_offsets.append(local_offset)
        struct.pack_into("<HH", data, local_offset + _ZIP_LOCAL_TIME_OFFSET, dos_time, dos_date)

    position = embedded.central_start
    for index, info in enumerate(infos):
        central_header_offset = position
        position = _validate_central_header(
            data,
            info,
            position,
            embedded.zip_start,
            embedded.central_start,
            embedded.end_record_offset,
            local_offsets[index],
        )
        struct.pack_into("<HH", data, central_header_offset + _ZIP_CENTRAL_TIME_OFFSET, dos_time, dos_date)
    if position != embedded.end_record_offset:
        raise ReproducibilityError("distlib launcher central directory has an unexpected trailing layout")
    return bytes(data)


def canonicalize_distlib_launcher(path: Path, epoch: CanonicalReleaseEpoch) -> bool:
    """Canonicalize a structurally proven distlib Windows launcher in place.

    ``False`` means that the file is not a distlib launcher.  A file that has
    the expected PE/embedded-ZIP shape but fails a structural invariant raises
    ``ReproducibilityError`` instead of being modified heuristically.
    """

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReproducibilityError(f"cannot read launcher candidate: {path}") from exc
    embedded = _open_embedded_zip(data)
    if embedded is None:
        return False
    try:
        canonical = _canonicalize_embedded_zip(embedded, epoch)
    finally:
        embedded.archive.close()
    revalidated = _open_embedded_zip(canonical)
    if revalidated is None:
        raise ReproducibilityError(f"canonicalized launcher failed structural validation: {path}")
    revalidated.archive.close()
    if canonical != data:
        path.write_bytes(canonical)
    return True


def canonicalize_distlib_launchers(runtime_root: Path, epoch: CanonicalReleaseEpoch) -> tuple[Path, ...]:
    """Canonicalize every non-interpreter executable in the assembled runtime."""

    runtime_root = runtime_root.resolve()
    if not runtime_root.is_dir():
        raise ReproducibilityError(f"runtime root is not a directory: {runtime_root}")
    canonicalized: list[Path] = []
    executables = (path for path in runtime_root.rglob("*") if path.is_file() and path.suffix.casefold() == ".exe")
    for path in sorted(executables, key=lambda item: item.relative_to(runtime_root).as_posix().casefold()):
        relative = path.relative_to(runtime_root).as_posix().casefold()
        if relative in {
            "python.exe",
            "pythonw.exe",
            "python3.exe",
            "python3w.exe",
            "runtime/python.exe",
            "runtime/pythonw.exe",
            "runtime/python3.exe",
            "runtime/python3w.exe",
        }:
            continue
        if relative in {
            "lib/venv/scripts/nt/python.exe",
            "lib/venv/scripts/nt/pythonw.exe",
        }:
            try:
                interpreter_data = path.read_bytes()
            except OSError as exc:
                raise ReproducibilityError(f"cannot read stdlib venv interpreter: {relative}") from exc
            if _pe_image_end(interpreter_data) != len(interpreter_data):
                raise ReproducibilityError(f"stdlib venv interpreter is not a standalone PE image: {relative}")
            continue
        changed = canonicalize_distlib_launcher(path, epoch)
        if not changed:
            raise ReproducibilityError(f"non-interpreter executable is not a proven distlib launcher: {relative}")
        canonicalized.append(path)
    return tuple(canonicalized)


__all__ = [
    "CanonicalReleaseEpoch",
    "ReproducibilityError",
    "canonical_zip_bytes",
    "canonical_zip_info",
    "canonicalize_distlib_launcher",
    "canonicalize_distlib_launchers",
    "canonicalize_wheel",
    "derive_release_epoch",
    "make_release_epoch",
    "normalize_tree_mtimes",
    "reconcile_dist_info_records",
    "reproducible_build_environment",
    "validate_dist_info_records",
    "validate_canonical_zip",
    "validate_wheel_records",
    "write_canonical_zip",
]
