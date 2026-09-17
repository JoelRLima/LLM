"""Compare two W18 release output directories without rebuilding either one.

The checker is intentionally independent from the release builder.  It proves
artifact SHA-256 equality, ZIP member/metadata/content equality, and manifest
semantic identity so that two user-run builds can be audited after the build
process has finished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distribution.release_identity import RELEASE_VERSION  # noqa: E402
from distribution.release_manifest import (  # noqa: E402
    ManifestValidationError,
    canonical_json_bytes,
    validate_manifest,
)


class ComparisonError(ValueError):
    """Raised when two release directories differ or are malformed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ComparisonError(f"cannot read artifact: {path}") from exc
    return digest.hexdigest()


def _artifact_paths(root: Path) -> tuple[Path, ...]:
    bundle = root / "bundle"
    if not bundle.is_dir():
        raise ComparisonError(f"release output is missing bundle directory: {root}")
    bundle_members = tuple(
        sorted((path for path in bundle.iterdir() if path.is_file()), key=lambda path: path.name)
    )
    if not bundle_members:
        raise ComparisonError(f"release bundle is empty: {bundle}")
    top_level = (
        root / "release-manifest.json",
        root / "sha256-inventory.json",
        root / "provenance-attestation.json",
        root / f"local-llm-agent-{RELEASE_VERSION}-windows-x64.zip",
    )
    missing = [path for path in top_level if not path.is_file()]
    if missing:
        raise ComparisonError(f"release output is missing artifact(s): {missing}")
    return (*bundle_members, *top_level)


def _zip_info_signature(info: zipfile.ZipInfo) -> tuple[Any, ...]:
    return (
        info.filename,
        info.date_time,
        info.compress_type,
        info.compress_size,
        info.file_size,
        info.CRC,
        info.create_system,
        info.create_version,
        info.extract_version,
        info.flag_bits,
        info.volume,
        info.internal_attr,
        info.external_attr,
        info.extra,
        info.comment,
        info.header_offset,
    )


def _compare_zip(left: Path, right: Path) -> None:
    try:
        with zipfile.ZipFile(left, "r") as left_archive, zipfile.ZipFile(right, "r") as right_archive:
            if left_archive.comment != right_archive.comment:
                raise ComparisonError(f"ZIP archive comments differ: {left.name}")
            left_infos = left_archive.infolist()
            right_infos = right_archive.infolist()
            if len({info.filename for info in left_infos}) != len(left_infos) or len({info.filename for info in right_infos}) != len(right_infos):
                raise ComparisonError(f"ZIP contains duplicate members: {left.name}")
            if tuple(info.filename for info in left_infos) != tuple(info.filename for info in right_infos):
                raise ComparisonError(f"ZIP member lists differ: {left.name}")
            for left_info, right_info in zip(left_infos, right_infos, strict=True):
                if _zip_info_signature(left_info) != _zip_info_signature(right_info):
                    raise ComparisonError(f"ZIP metadata differs for {left.name}:{left_info.filename}")
                if left_archive.read(left_info.filename) != right_archive.read(right_info.filename):
                    raise ComparisonError(f"ZIP member bytes differ for {left.name}:{left_info.filename}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ComparisonError(f"cannot inspect ZIP artifacts: {left}, {right}") from exc


def _manifest_semantic_identity(path: Path) -> bytes:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ManifestValidationError) as exc:
        raise ComparisonError(f"invalid release manifest: {path}") from exc
    source = dict(document["source"])
    source.pop("status", None)
    source.pop("commit", None)
    identity = dict(document)
    identity["source"] = source
    return cast(bytes, canonical_json_bytes(identity))


def compare_release_directories(left: Path, right: Path) -> dict[str, Any]:
    """Compare two completed release outputs and return a deterministic summary."""

    left = left.resolve()
    right = right.resolve()
    left_paths = _artifact_paths(left)
    right_paths = _artifact_paths(right)
    left_relative = tuple(path.relative_to(left).as_posix() for path in left_paths)
    right_relative = tuple(path.relative_to(right).as_posix() for path in right_paths)
    if left_relative != right_relative:
        raise ComparisonError(f"artifact member lists differ: {left_relative} != {right_relative}")

    hashes: dict[str, str] = {}
    for left_path, right_path, relative in zip(left_paths, right_paths, left_relative, strict=True):
        left_hash = _sha256(left_path)
        right_hash = _sha256(right_path)
        if left_hash != right_hash:
            raise ComparisonError(f"artifact SHA-256 differs for {relative}: {left_hash} != {right_hash}")
        hashes[relative] = left_hash

    for relative in left_relative:
        if relative.endswith(".whl") or relative.endswith(".zip"):
            _compare_zip(left / Path(*relative.split("/")), right / Path(*relative.split("/")))

    for relative in ("bundle/release-manifest.json", "release-manifest.json"):
        left_identity = _manifest_semantic_identity(left / Path(*relative.split("/")))
        right_identity = _manifest_semantic_identity(right / Path(*relative.split("/")))
        if left_identity != right_identity:
            raise ComparisonError(f"manifest semantic identity differs for {relative}")

    return {
        "status": "passed",
        "left": str(left),
        "right": str(right),
        "artifacts": hashes,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True, help="first completed release output directory")
    parser.add_argument("--right", type=Path, required=True, help="second completed release output directory")
    args = parser.parse_args(argv)
    try:
        result = compare_release_directories(args.left, args.right)
    except ValueError as exc:
        print(f"W18 reproducibility check failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
