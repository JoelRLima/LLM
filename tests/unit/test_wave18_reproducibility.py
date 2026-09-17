"""Focused, non-network checks for W18 reproducible-build primitives.

These tests intentionally exercise synthetic artifacts.  The expensive
two-build release gate remains the user-run checker in
``scripts/check_reproducible_release.py``.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import stat
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from distribution.payload import make_inventory, render_inventory
from distribution.release_identity import APPLICATION_WHEEL, PAYLOAD_ARCHIVE, RELEASE_VERSION
from distribution.release_manifest import make_manifest, render_manifest
from distribution.reproducibility import (
    ReproducibilityError,
    canonical_zip_bytes,
    canonicalize_distlib_launcher,
    canonicalize_distlib_launchers,
    canonicalize_wheel,
    make_release_epoch,
    normalize_tree_mtimes,
    reconcile_dist_info_records,
    validate_dist_info_records,
)
from scripts.check_reproducible_release import compare_release_directories

BASE_COMMIT = "a" * 40
EPOCH = make_release_epoch(BASE_COMMIT, 1_757_950_801)


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


def _write_noncanonical_zip(path: Path, members: list[tuple[str, bytes]], stamp: tuple[int, int, int, int, int, int]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, content in members:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.create_system = 0
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)


def _wheel_members() -> list[tuple[str, bytes]]:
    files = {
        "local_llm_agent/__init__.py": b"__version__ = '0.2.0rc1'\n",
        "local_llm_agent-0.2.0rc1.dist-info/METADATA": b"Metadata-Version: 2.1\nName: local-llm-agent\nVersion: 0.2.0rc1\n",
        "local_llm_agent-0.2.0rc1.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "local_llm_agent-0.2.0rc1.dist-info/entry_points.txt": b"[console_scripts]\nllm-agent = agent.interfaces.cli.app:main\n",
    }
    rows = [
        [name, _record_hash(content), str(len(content))]
        for name, content in sorted(files.items())
    ]
    rows.append(["local_llm_agent-0.2.0rc1.dist-info/RECORD", "", ""])
    rendered = io.StringIO(newline="")
    csv.writer(rendered, lineterminator="\n").writerows(rows)
    files["local_llm_agent-0.2.0rc1.dist-info/RECORD"] = rendered.getvalue().encode("utf-8")
    return list(files.items())


_DISTLIB_SHEBANG = b"#!\"C:\\Program Files\\Python312\\python.exe\"\n"
_DISTLIB_MAIN_BODY = (
    b"# -*- coding: utf-8 -*-\n"
    b"import re\n"
    b"import sys\n"
    b"from example.module import main\n"
    b"if __name__ == '__main__':\n"
    b"    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
    b"    sys.exit(main())\n"
)


def _minimal_pe_stub() -> bytearray:
    header_size = 0x400
    section_size = 0x200
    pe_stub = bytearray(header_size + section_size)
    pe_stub[:2] = b"MZ"
    struct.pack_into("<L", pe_stub, 0x3C, 0x80)
    pe_stub[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHLLLHH", pe_stub, 0x84, 0x8664, 1, 0, 0, 0, 0xF0, 0x2022)
    optional_start = 0x98
    struct.pack_into("<H", pe_stub, optional_start, 0x20B)
    struct.pack_into("<L", pe_stub, optional_start + 36, 0x200)
    struct.pack_into("<L", pe_stub, optional_start + 56, 0x2000)
    struct.pack_into("<L", pe_stub, optional_start + 60, header_size)
    struct.pack_into("<L", pe_stub, optional_start + 108, 16)
    section_start = optional_start + 0xF0
    pe_stub[section_start : section_start + 8] = b".text\x00\x00\x00"
    struct.pack_into(
        "<IIIIIIHHI",
        pe_stub,
        section_start + 8,
        1,
        0x1000,
        section_size,
        header_size,
        0,
        0,
        0,
        0,
        0x60000020,
    )
    pe_stub[header_size] = 0xC3
    return pe_stub


def _fake_distlib_launcher(
    path: Path,
    stamp: tuple[int, int, int, int, int, int],
    *,
    shebang: bytes = _DISTLIB_SHEBANG,
    main_body: bytes = _DISTLIB_MAIN_BODY,
    trailing: bytes = b"",
) -> None:
    embedded = io.BytesIO()
    with zipfile.ZipFile(embedded, "w", compression=zipfile.ZIP_STORED) as archive:
        main = zipfile.ZipInfo("__main__.py", date_time=stamp)
        archive.writestr(main, main_body)
    path.write_bytes(bytes(_minimal_pe_stub()) + shebang + embedded.getvalue() + trailing)


def test_release_epoch_is_utc_bounded_and_dos_granular() -> None:
    before_zip = make_release_epoch(BASE_COMMIT, -1)
    odd_second = make_release_epoch(BASE_COMMIT, 1_757_950_801)

    assert before_zip.zip_datetime == (1980, 1, 1, 0, 0, 0)
    assert odd_second.zip_datetime[5] % 2 == 0


def test_normalize_tree_mtimes_sets_regular_file_to_canonical_epoch(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    path = root / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")

    normalize_tree_mtimes(root, EPOCH)

    assert path.stat().st_atime == EPOCH.source_date_epoch
    assert path.stat().st_mtime == EPOCH.source_date_epoch


def test_normalize_tree_mtimes_sets_regular_directories_to_canonical_epoch(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    nested = root / "nested"
    nested.mkdir(parents=True)

    normalize_tree_mtimes(root, EPOCH)

    for path in (nested, root):
        assert path.stat().st_atime == EPOCH.source_date_epoch
        assert path.stat().st_mtime == EPOCH.source_date_epoch


def test_normalize_tree_mtimes_uses_plain_utime_for_safe_paths_without_follow_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    path = root / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")
    real_utime = os.utime
    calls: list[tuple[Path, tuple[int, int]]] = []

    def plain_utime(target: Path, times: tuple[int, int]) -> None:
        calls.append((target, times))
        real_utime(target, times)

    monkeypatch.setattr(os, "supports_follow_symlinks", set())
    monkeypatch.setattr(os, "utime", plain_utime)

    normalize_tree_mtimes(root, EPOCH)

    assert calls == [
        (path, (EPOCH.source_date_epoch, EPOCH.source_date_epoch)),
        (root, (EPOCH.source_date_epoch, EPOCH.source_date_epoch)),
    ]
    assert path.stat().st_mtime == EPOCH.source_date_epoch
    assert root.stat().st_mtime == EPOCH.source_date_epoch


def test_normalize_tree_mtimes_rejects_symlink_before_touching_external_target(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside\n", encoding="utf-8")
    sentinel = EPOCH.source_date_epoch - 100
    os.utime(target, (sentinel, sentinel))
    link = root / "linked.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ReproducibilityError, match="symlink/reparse point"):
        normalize_tree_mtimes(root, EPOCH)

    assert target.stat().st_mtime == sentinel


def test_normalize_tree_mtimes_rejects_reparse_point_before_utime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    regular = root / "regular.txt"
    regular.write_text("regular\n", encoding="utf-8")
    sentinel = EPOCH.source_date_epoch - 100
    os.utime(regular, (sentinel, sentinel))
    reparse = root / "junction"
    reparse.mkdir()
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    original_lstat = Path.lstat

    def fake_lstat(path: Path):
        if path == reparse:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=reparse_attribute)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ReproducibilityError, match="symlink/reparse point"):
        normalize_tree_mtimes(root, EPOCH)

    assert regular.stat().st_mtime == sentinel


def test_normalize_tree_mtimes_does_not_mask_real_utime_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")

    def failing_utime(_path: Path, _times: tuple[int, int]) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "utime", failing_utime)

    with pytest.raises(ReproducibilityError, match="cannot normalize source snapshot mtime") as error:
        normalize_tree_mtimes(root, EPOCH)

    assert isinstance(error.value.__cause__, PermissionError)


def test_zip_metadata_is_canonical_and_order_independent() -> None:
    first = canonical_zip_bytes([("z.txt", b"z"), ("a.txt", b"a")], EPOCH)
    second = canonical_zip_bytes([("a.txt", b"a"), ("z.txt", b"z")], EPOCH)

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.comment == b""
        assert [info.filename for info in archive.infolist()] == ["a.txt", "z.txt"]
        assert all(info.date_time == EPOCH.zip_datetime for info in archive.infolist())
        assert all(info.create_system == 3 for info in archive.infolist())
        assert all(info.external_attr == 0o100644 << 16 for info in archive.infolist())


def test_distlib_launcher_canonicalization_updates_local_and_central_zip_times(tmp_path: Path) -> None:
    first = tmp_path / "first.exe"
    second = tmp_path / "second.exe"
    _fake_distlib_launcher(first, (2026, 9, 15, 17, 4, 56))
    _fake_distlib_launcher(second, (2026, 9, 15, 17, 44, 44))
    before = first.read_bytes()
    with zipfile.ZipFile(io.BytesIO(before)) as archive:
        info = archive.infolist()[0]
        allowed_time_offsets = {
            *range(info.header_offset + 10, info.header_offset + 14),
            *range(archive.start_dir + 12, archive.start_dir + 16),
        }

    assert canonicalize_distlib_launcher(first, EPOCH) is True
    assert canonicalize_distlib_launcher(second, EPOCH) is True
    assert first.read_bytes() == second.read_bytes()
    after = first.read_bytes()
    changed_offsets = {
        index
        for index, (before_byte, after_byte) in enumerate(zip(before, after, strict=True))
        if before_byte != after_byte
    }
    assert changed_offsets <= allowed_time_offsets
    with zipfile.ZipFile(io.BytesIO(first.read_bytes())) as archive:
        assert [info.date_time for info in archive.infolist()] == [EPOCH.zip_datetime]
        assert archive.read("__main__.py") == _DISTLIB_MAIN_BODY

    once = first.read_bytes()
    assert canonicalize_distlib_launcher(first, EPOCH) is True
    assert first.read_bytes() == once


def test_distlib_launcher_rejects_arbitrary_pe_with_pk_bytes(tmp_path: Path) -> None:
    path = tmp_path / "unknown.exe"
    arbitrary = bytearray(0x100)
    arbitrary[:2] = b"MZ"
    struct.pack_into("<L", arbitrary, 0x3C, 0x80)
    arbitrary[0x80:0x84] = b"PE\x00\x00"
    path.write_bytes(bytes(arbitrary) + b"PK")

    assert canonicalize_distlib_launcher(path, EPOCH) is False


def test_distlib_launcher_rejects_invalid_zip_after_valid_pe_and_shebang(tmp_path: Path) -> None:
    path = tmp_path / "invalid-zip.exe"
    path.write_bytes(bytes(_minimal_pe_stub()) + _DISTLIB_SHEBANG + b"PK\x03\x04")

    assert canonicalize_distlib_launcher(path, EPOCH) is False


def test_distlib_launcher_rejects_invalid_shebang(tmp_path: Path) -> None:
    path = tmp_path / "invalid-shebang.exe"
    _fake_distlib_launcher(path, (2026, 9, 15, 17, 4, 56), shebang=b"#!C:\\Windows\\cmd.exe\n")

    assert canonicalize_distlib_launcher(path, EPOCH) is False


def test_distlib_launcher_rejects_trailing_unexplained_data(tmp_path: Path) -> None:
    path = tmp_path / "trailing.exe"
    _fake_distlib_launcher(path, (2026, 9, 15, 17, 4, 56), trailing=b"unexplained")

    assert canonicalize_distlib_launcher(path, EPOCH) is False


def test_distlib_launcher_rejects_inconsistent_local_and_central_headers(tmp_path: Path) -> None:
    path = tmp_path / "inconsistent.exe"
    _fake_distlib_launcher(path, (2026, 9, 15, 17, 4, 56))
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        central_offset = archive.start_dir
    struct.pack_into("<H", data, central_offset + 6, 99)
    path.write_bytes(data)

    with pytest.raises(ReproducibilityError, match="local/central"):
        canonicalize_distlib_launcher(path, EPOCH)


def test_unknown_executable_fails_closed_in_runtime_sweep(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    unknown = runtime / "unknown.exe"
    unknown.write_bytes(b"MZ arbitrary executable")

    with pytest.raises(ReproducibilityError, match="not a proven distlib launcher"):
        canonicalize_distlib_launchers(runtime, EPOCH)


@pytest.mark.parametrize(
    "relative",
    (
        "Lib/venv/scripts/nt/python.exe",
        "Lib/venv/scripts/nt/pythonw.exe",
    ),
)
def test_runtime_sweep_preserves_stdlib_venv_interpreters(tmp_path: Path, relative: str) -> None:
    runtime = tmp_path / "runtime"
    interpreter = runtime / Path(*relative.split("/"))
    interpreter.parent.mkdir(parents=True)
    original = bytes(_minimal_pe_stub())
    interpreter.write_bytes(original)

    assert canonicalize_distlib_launchers(runtime, EPOCH) == ()
    assert interpreter.read_bytes() == original


def test_runtime_sweep_rejects_malformed_stdlib_venv_interpreter(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    interpreter = runtime / "Lib" / "venv" / "scripts" / "nt" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"MZ arbitrary executable")

    with pytest.raises(ReproducibilityError, match="not a standalone PE image"):
        canonicalize_distlib_launchers(runtime, EPOCH)


def test_wheel_canonicalization_preserves_valid_record_and_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    members = _wheel_members()
    _write_noncanonical_zip(first, list(reversed(members)), (2026, 9, 15, 17, 4, 56))
    _write_noncanonical_zip(second, list(reversed(members)), (2026, 9, 15, 17, 44, 44))

    canonicalize_wheel(first, EPOCH)
    canonicalize_wheel(second, EPOCH)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        record = archive.read("local_llm_agent-0.2.0rc1.dist-info/RECORD").decode("utf-8")
        assert record == "".join(sorted(record.splitlines(keepends=True)))


def test_record_reconciliation_updates_launcher_hash_and_drops_intentional_removal(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    package = site_packages / "sample_pkg"
    dist_info = site_packages / "sample_pkg-1.0.dist-info"
    launcher = site_packages / "bin" / "sample.exe"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"sample\n")
    launcher.write_bytes(b"launcher-before\n")
    record_path = dist_info / "RECORD"
    record_path.write_text(
        "sample_pkg/__init__.py,sha256=stale,1\n"
        "bin/sample.exe,sha256=stale,1\n"
        "sample_pkg-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    launcher.write_bytes(b"launcher-after\n")
    reconcile_dist_info_records(runtime)
    validate_dist_info_records(runtime)
    rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    by_path = {row[0]: row for row in rows}
    assert by_path["bin/sample.exe"][1:] == [_record_hash(b"launcher-after\n"), str(len(b"launcher-after\n"))]

    launcher.unlink()
    reconcile_dist_info_records(runtime, [launcher])
    validate_dist_info_records(runtime)
    assert "bin/sample.exe" not in {
        row[0] for row in csv.reader(record_path.read_text(encoding="utf-8").splitlines())
    }


def test_record_reconciliation_hashes_final_canonicalized_distlib_launcher(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    dist_info = site_packages / "sample_pkg-1.0.dist-info"
    launcher = site_packages / "bin" / "sample.exe"
    dist_info.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    _fake_distlib_launcher(launcher, (2026, 9, 15, 17, 4, 56))
    record_path = dist_info / "RECORD"
    record_path.write_text(
        "bin/sample.exe,sha256=stale,1\n"
        "sample_pkg-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    canonicalize_distlib_launcher(launcher, EPOCH)
    reconcile_dist_info_records(runtime)
    validate_dist_info_records(runtime)
    rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    by_path = {row[0]: row for row in rows}

    final_bytes = launcher.read_bytes()
    assert by_path["bin/sample.exe"][1:] == [_record_hash(final_bytes), str(len(final_bytes))]


def test_payload_inventory_is_stable_for_same_logical_files(tmp_path: Path) -> None:
    first = tmp_path / "payload-a"
    second = tmp_path / "payload-b"
    for root, order in ((first, ("b.txt", "a.txt")), (second, ("a.txt", "b.txt"))):
        for name in order:
            root.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(name.encode("ascii"))

    first_inventory = make_inventory(first)
    second_inventory = make_inventory(second)
    assert render_inventory(first_inventory) == render_inventory(second_inventory)


def test_payload_and_final_release_zip_bytes_are_stable(tmp_path: Path) -> None:
    payload_a = tmp_path / "payload-a.zip"
    payload_b = tmp_path / "payload-b.zip"
    release_a = tmp_path / "release-a.zip"
    release_b = tmp_path / "release-b.zip"
    logical_payload = [("runtime/python.exe", b"python"), ("app/launcher.py", b"launcher")]
    logical_release = [("payload-windows-x64.zip", payload_a.read_bytes() if payload_a.exists() else b"payload")]

    payload_a.write_bytes(canonical_zip_bytes(logical_payload, EPOCH))
    payload_b.write_bytes(canonical_zip_bytes(list(reversed(logical_payload)), EPOCH))
    release_a.write_bytes(canonical_zip_bytes(logical_release, EPOCH))
    release_b.write_bytes(canonical_zip_bytes(logical_release, EPOCH))

    assert payload_a.read_bytes() == payload_b.read_bytes()
    assert release_a.read_bytes() == release_b.read_bytes()


def test_two_output_checker_compares_all_artifacts_and_zip_metadata(tmp_path: Path) -> None:
    first = tmp_path / "release-a"
    second = tmp_path / "release-b"
    for root in (first, second):
        bundle = root / "bundle"
        bundle.mkdir(parents=True)
        manifest = make_manifest(
            source_base_commit=BASE_COMMIT,
            source_tree="b" * 40,
            wheel_sha256="1" * 64,
            payload_sha256="4" * 64,
            payload_inventory_sha256="5" * 64,
            runtime_lock_sha256="2" * 64,
            bootstrap_pip_lock_sha256="3" * 64,
        )
        manifest_bytes = render_manifest(manifest)
        (bundle / APPLICATION_WHEEL).write_bytes(canonical_zip_bytes([("wheel.txt", b"wheel")], EPOCH))
        (bundle / PAYLOAD_ARCHIVE).write_bytes(canonical_zip_bytes([("payload.txt", b"payload")], EPOCH))
        (bundle / "payload-files.json").write_bytes(b'{"files":[],"schema_version":"W18-PAYLOAD-FILES-V1"}\n')
        (bundle / "release-manifest.json").write_bytes(manifest_bytes)
        (root / "release-manifest.json").write_bytes(manifest_bytes)
        (root / "sha256-inventory.json").write_bytes(b"{}\n")
        (root / "provenance-attestation.json").write_bytes(b"{}\n")
        (root / f"local-llm-agent-{RELEASE_VERSION}-windows-x64.zip").write_bytes(
            canonical_zip_bytes([("release.txt", b"release")], EPOCH)
        )

    result = compare_release_directories(first, second)
    assert result["status"] == "passed"
