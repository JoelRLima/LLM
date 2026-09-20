from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.storage_contracts import StorageMigrationError
from agent.runtime.storage_migration import build_migration_plan, migrate_plan


def _legacy_home(tmp_path: Path) -> AppPaths:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "state").mkdir(parents=True)
    (home / "config" / "config.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    (home / "state" / "health_report.json").write_text("{}", encoding="utf-8")
    return AppPaths.discover(app_home=home, env={})


def test_migration_promotes_receipt_before_marker_and_preserves_source(tmp_path: Path) -> None:
    paths = _legacy_home(tmp_path)
    source = paths.home_dir / "state" / "health_report.json"
    receipt = migrate_plan(build_migration_plan(paths), paths)

    assert receipt == paths.w18_to_w19_migration_receipt_file
    assert paths.storage_layout_file.exists()
    assert paths.health_report_file.read_text(encoding="utf-8") == "{}"
    assert source.read_text(encoding="utf-8") == "{}"


def test_conflicting_target_fails_before_new_promotion(tmp_path: Path) -> None:
    paths = _legacy_home(tmp_path)
    target = paths.health_report_file
    target.parent.mkdir(parents=True)
    target.write_text('{"different":true}', encoding="utf-8")
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(build_migration_plan(paths), paths)
    assert raised.value.reason_code == "MIGRATION_TARGET_CONFLICT"
    assert not paths.storage_layout_file.exists()


def test_injected_promotion_failure_rolls_back_created_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _legacy_home(tmp_path)
    (paths.home_dir / "state" / "last_workspace.json").write_text("{}", encoding="utf-8")
    import agent.runtime.storage_migration as migration_module

    original = migration_module._copy_entry
    calls = 0

    def flaky(entry, home, record_created=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageMigrationError("MIGRATION_PROMOTION_FAILED")
        return original(entry, home, record_created)

    monkeypatch.setattr(migration_module, "_copy_entry", flaky)
    with pytest.raises(StorageMigrationError):
        migrate_plan(build_migration_plan(paths), paths)
    assert not paths.storage_layout_file.exists()
    assert not paths.health_report_file.exists()
    assert not paths.last_workspace_file.exists()


def test_fresh_bootstrap_materializes_marker_without_legacy_sources(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "fresh", env={})
    result = StorageBootstrap().prepare(paths)
    assert result.initial_status.value == "fresh"
    assert result.migrated is False
    assert paths.storage_layout_file.exists()


def _entry_for_health(paths: AppPaths):
    return next(entry for entry in build_migration_plan(paths).entries if entry.destination == paths.health_report_file)


class _FunctionCallOwners(ast.NodeVisitor):
    _PROTECTED = {
        "write_text_atomic",
        "write_migration_receipt",
        "write_layout_marker",
    }

    def __init__(self, aliases: dict[str, str]) -> None:
        self.stack: list[str] = []
        self.callers: dict[str, set[str]] = {}
        self.aliases = aliases

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        symbol: str | None = None
        if isinstance(node.func, ast.Name):
            symbol = self.aliases.get(node.func.id, node.func.id)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in self._PROTECTED:
            symbol = node.func.attr
        if symbol in self._PROTECTED:
            owner = self.stack[-1] if self.stack else "<module>"
            self.callers.setdefault(symbol, set()).add(owner)
        self.generic_visit(node)


def _module_import_aliases(tree: ast.AST) -> dict[str, str]:
    protected = _FunctionCallOwners._PROTECTED
    aliases: dict[str, str] = {}
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in protected:
                        aliases[imported.asname or imported.name] = imported.name
    return aliases


def _assert_single_metadata_writer_authority(tree: ast.AST) -> None:
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    visitor = _FunctionCallOwners(_module_import_aliases(tree))
    visitor.visit(tree)
    assert visitor.callers.get("write_text_atomic", set()) == {"_restore_metadata_if_owned"}
    assert visitor.callers.get("write_migration_receipt", set()) == {"migrate_plan"}
    assert visitor.callers.get("write_layout_marker", set()) == {"migrate_plan"}
    assert "_publish_metadata" not in functions


@pytest.mark.parametrize("failure", ["inspection", "hash_mismatch", "hash_oserror"])
def test_failure_after_replace_removes_only_owned_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    entry = _entry_for_health(paths)
    real_inspect = migration_module.inspect_final_path
    real_hash = migration_module._hash
    real_replace = os.replace
    after_replace = False

    def replace(source, destination):
        nonlocal after_replace
        real_replace(source, destination)
        after_replace = True

    def inspect(path):
        nonlocal after_replace
        if after_replace and Path(path) == entry.destination and failure == "inspection":
            after_replace = False
            raise OSError("injected inspection failure")
        return real_inspect(path)

    def digest(path):
        nonlocal after_replace
        if after_replace and Path(path) == entry.destination:
            after_replace = False
            if failure == "hash_oserror":
                raise StorageMigrationError("MIGRATION_SOURCE_INVALID", "injected read failure")
            if failure == "hash_mismatch":
                return "0" * 64
        return real_hash(path)

    monkeypatch.setattr(migration_module.os, "replace", replace)
    monkeypatch.setattr(migration_module, "inspect_final_path", inspect)
    monkeypatch.setattr(migration_module, "_hash", digest)
    with pytest.raises(StorageMigrationError):
        migration_module._copy_entry(entry, paths.home_dir)
    assert not entry.destination.exists()


def test_replacement_after_promotion_failure_is_preserved_and_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    entry = _entry_for_health(paths)
    real_inspect = migration_module.inspect_final_path
    injected = False

    def inspect(path):
        nonlocal injected
        if Path(path) == entry.destination and entry.destination.exists() and not injected:
            injected = True
            replacement = entry.destination.with_suffix(".replacement")
            replacement.write_bytes(b"replacement")
            os.replace(replacement, entry.destination)
        return real_inspect(path)

    monkeypatch.setattr(migration_module, "inspect_final_path", inspect)
    with pytest.raises(StorageMigrationError) as raised:
        migration_module._copy_entry(entry, paths.home_dir)
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert entry.destination.read_bytes() == b"replacement"


def test_same_byte_source_replacement_is_detected_and_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    source = paths.home_dir / "state" / "health_report.json"
    original = migration_module._copy_entry
    replaced = False

    def copy(entry, home, record_created=None):
        nonlocal replaced
        result = original(entry, home, record_created)
        if not replaced:
            replaced = True
            temporary = source.with_suffix(".new")
            temporary.write_bytes(source.read_bytes())
            os.replace(temporary, source)
        return result

    monkeypatch.setattr(migration_module, "_copy_entry", copy)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(plan, paths)
    assert raised.value.reason_code == "MIGRATION_SOURCE_CHANGED"
    assert not paths.health_report_file.exists()
    assert not paths.storage_layout_file.exists()


@pytest.mark.parametrize("change", ["modified", "removed", "link"])
def test_identical_target_is_revalidated_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import agent.runtime.storage_migration as migration_module
    import agent.runtime.storage_migration_revalidation as revalidation_module

    paths = _legacy_home(tmp_path)
    identical = paths.health_report_file
    identical.parent.mkdir(parents=True)
    identical.write_text("{}", encoding="utf-8")
    plan = build_migration_plan(paths)
    original = migration_module._build_plan_for_profile
    real_inspect = revalidation_module.inspect_final_path
    link_injected = False

    def rebuild(app_paths, profile):
        nonlocal link_injected
        result = original(app_paths, profile)
        identical.unlink()
        if change == "modified":
            identical.write_text('{"changed":true}', encoding="utf-8")
        elif change == "link":
            identical.write_text("{}", encoding="utf-8")
            link_injected = True
        return result

    def inspect(path):
        inspection = real_inspect(path)
        if link_injected and Path(path) == identical:
            inspection.is_link_like = True
        return inspection

    monkeypatch.setattr(migration_module, "_build_plan_for_profile", rebuild)
    monkeypatch.setattr(revalidation_module, "inspect_final_path", inspect)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(plan, paths)
    assert raised.value.reason_code == "MIGRATION_TARGET_CONFLICT"
    assert not paths.storage_layout_file.exists()


def test_migration_has_no_parallel_atomic_metadata_writer() -> None:
    import agent.runtime.storage_migration as migration_module

    source = Path(migration_module.__file__).read_text(encoding="utf-8")
    _assert_single_metadata_writer_authority(ast.parse(source))


@pytest.mark.parametrize(
    "mutation",
    [
        "def _publish_metadata(path, content):\n    return write_text_atomic(path, content)\n",
        "def _parallel_writer(path, content):\n    return write_text_atomic(path, content)\n",
        "def _new_metadata_helper(path, content):\n    return write_text_atomic(path, content)\n",
        (
            "import agent.memory.json_persistence as jp\n"
            "def _parallel_writer_qualified(path, content):\n"
            "    return jp.write_text_atomic(path, content)\n"
        ),
        (
            "from agent.memory.json_persistence import write_text_atomic as wat\n"
            "def _parallel_writer_alias(path, content):\n"
            "    return wat(path, content)\n"
        ),
        (
            "def _parallel_writer_alias_late(path, content):\n"
            "    return wat(path, content)\n"
            "from agent.memory.json_persistence import write_text_atomic as wat\n"
        ),
        (
            "import agent.runtime.storage_contracts as contracts\n"
            "def _parallel_receipt_writer(path, receipt):\n"
            "    return contracts.write_migration_receipt(path, receipt)\n"
        ),
        (
            "import agent.runtime.storage_contracts as contracts\n"
            "def _parallel_marker_writer(path):\n"
            "    return contracts.write_layout_marker(path)\n"
        ),
        (
            "def _parallel_receipt_writer_alias_late(path, receipt):\n"
            "    return wr(path, receipt)\n"
            "from agent.runtime.storage_contracts import write_migration_receipt as wr\n"
        ),
    ],
)
def test_single_metadata_writer_gate_rejects_parallel_helpers(mutation: str) -> None:
    import agent.runtime.storage_migration as migration_module

    source = Path(migration_module.__file__).read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_single_metadata_writer_authority(ast.parse(source + "\n" + mutation))


def test_single_metadata_writer_gate_rejects_direct_call_in_migrate_plan() -> None:
    import agent.runtime.storage_migration as migration_module

    tree = ast.parse(Path(migration_module.__file__).read_text(encoding="utf-8"))
    migrate = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "migrate_plan"
    )
    migrate.body.insert(
        0,
        ast.Expr(
            ast.Call(
                func=ast.Name(id="write_text_atomic"),
                args=[ast.Name(id="receipt_path"), ast.Constant(value="parallel")],
                keywords=[],
            )
        ),
    )
    with pytest.raises(AssertionError):
        _assert_single_metadata_writer_authority(tree)


@pytest.mark.parametrize("previous", [None, b"previous receipt\n"])
def test_receipt_rollback_removes_new_or_restores_previous_bytes(
    tmp_path: Path, previous: bytes | None
) -> None:
    import agent.runtime.storage_migration as migration_module

    receipt = tmp_path / "receipt.json"
    marker = tmp_path / "marker.json"
    if previous is not None:
        receipt.write_bytes(previous)
    receipt.write_bytes(b"attempt receipt\n")
    evidence = os.stat(receipt, follow_symlinks=False)
    migration_module._rollback([], receipt, previous, evidence, marker, None)
    if previous is None:
        assert not receipt.exists()
    else:
        assert receipt.read_bytes() == previous


@pytest.mark.parametrize("kind", ["marker", "receipt"])
def test_metadata_replacement_is_preserved_and_rollback_is_unproven(
    tmp_path: Path, kind: str
) -> None:
    import agent.runtime.storage_migration as migration_module

    receipt = tmp_path / "receipt.json"
    marker = tmp_path / "marker.json"
    target = marker if kind == "marker" else receipt
    target.write_bytes(b"attempt\n")
    evidence = os.stat(target, follow_symlinks=False)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"replacement\n")
    os.replace(replacement, target)
    with pytest.raises(StorageMigrationError) as raised:
        migration_module._rollback(
            [],
            receipt,
            None,
            evidence if kind == "receipt" else None,
            marker,
            evidence if kind == "marker" else None,
        )
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert target.read_bytes() == b"replacement\n"


def test_replace_then_interrupt_removes_owned_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    entry = _entry_for_health(paths)
    real_replace = os.replace

    def replace_then_interrupt(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(migration_module.os, "replace", replace_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        migration_module._copy_entry(entry, paths.home_dir)
    assert not entry.destination.exists()


def test_replace_then_foreign_replacement_is_preserved_and_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    entry = _entry_for_health(paths)
    real_replace = os.replace

    def replace_then_interrupt(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        replacement = Path(destination).with_suffix(".foreign")
        replacement.write_bytes(b"foreign")
        real_replace(replacement, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(migration_module.os, "replace", replace_then_interrupt)
    with pytest.raises(StorageMigrationError) as raised:
        migration_module._copy_entry(entry, paths.home_dir)
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert entry.destination.read_bytes() == b"foreign"


@pytest.mark.parametrize(
    ("kind", "previous"),
    [("marker", None), ("receipt", b"previous receipt\n")],
)
def test_metadata_replace_then_interrupt_rolls_back_exact_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    previous: bytes | None,
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    path = paths.storage_layout_file if kind == "marker" else paths.w18_to_w19_migration_receipt_file
    if previous is not None:
        receipt = migration_module.make_migration_receipt(
            source_profile=plan.profile.name,
            copied=[],
            identical=[],
            preserved_legacy_top_level=[],
        )
        migration_module.write_migration_receipt(path, receipt)
        previous = path.read_bytes()
    real_replace = os.replace
    interrupted = False

    def replace_then_interrupt(source: Path, destination: Path) -> None:
        nonlocal interrupted
        real_replace(source, destination)
        if Path(destination) == path and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(migration_module.os, "replace", replace_then_interrupt)
    with pytest.raises(StorageMigrationError):
        migrate_plan(plan, paths)
    assert path.read_bytes() == previous if previous is not None else not path.exists()


def test_target_retry_after_interrupt_has_no_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    entry = _entry_for_health(paths)
    real_replace = os.replace

    with monkeypatch.context() as interrupted:
        def replace_then_interrupt(source: Path, destination: Path) -> None:
            real_replace(source, destination)
            raise KeyboardInterrupt

        interrupted.setattr(migration_module.os, "replace", replace_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            migration_module._copy_entry(entry, paths.home_dir)

    migration_module._copy_entry(entry, paths.home_dir)
    assert entry.destination.exists()


def test_metadata_replace_then_foreign_replacement_is_preserved_and_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    path = tmp_path / "metadata.json"
    path.write_bytes(b"attempt\n")
    evidence = os.stat(path, follow_symlinks=False)
    replacement = path.with_suffix(".foreign")
    replacement.write_bytes(b"foreign")
    os.replace(replacement, path)
    with pytest.raises(StorageMigrationError) as raised:
        migration_module._restore_metadata_if_owned(path, evidence, None)
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert path.read_bytes() == b"foreign"


def test_promotion_ledger_is_recorded_before_wrapper_can_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    original = migration_module._copy_entry

    def interrupt_after_promotion(entry, home, record_created=None):
        original(entry, home, record_created)
        raise KeyboardInterrupt

    monkeypatch.setattr(migration_module, "_copy_entry", interrupt_after_promotion)
    with pytest.raises(StorageMigrationError):
        migrate_plan(build_migration_plan(paths), paths)
    assert not paths.health_report_file.exists()


def test_interrupt_immediately_after_record_created_has_single_cleanup_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    original = migration_module._promote_entry

    def promote(entry, home, temporary, record_created=None):
        def record_then_interrupt(path, evidence):
            assert record_created is not None
            record_created(path, evidence)
            raise KeyboardInterrupt

        return original(entry, home, temporary, record_then_interrupt)

    monkeypatch.setattr(migration_module, "_promote_entry", promote)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(build_migration_plan(paths), paths)
    assert raised.value.reason_code == "MIGRATION_PROMOTION_FAILED"
    assert not paths.health_report_file.exists()


def test_foreign_target_after_record_created_is_preserved_and_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    original = migration_module._promote_entry

    def promote(entry, home, temporary, record_created=None):
        def record_replace_and_interrupt(path, evidence):
            assert record_created is not None
            record_created(path, evidence)
            replacement = path.with_suffix(".foreign")
            replacement.write_bytes(b"foreign")
            os.replace(replacement, path)
            raise KeyboardInterrupt

        return original(entry, home, temporary, record_replace_and_interrupt)

    monkeypatch.setattr(migration_module, "_promote_entry", promote)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(build_migration_plan(paths), paths)
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert paths.health_report_file.read_bytes() == b"foreign"


@pytest.mark.parametrize("with_previous", [False, True])
def test_interrupt_immediately_after_record_receipt_rolls_back_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_previous: bool
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    previous: bytes | None = None
    if with_previous:
        old = migration_module.make_migration_receipt(
            source_profile=plan.profile.name,
            copied=[],
            identical=[],
            preserved_legacy_top_level=[],
        )
        migration_module.write_migration_receipt(paths.w18_to_w19_migration_receipt_file, old)
        previous = paths.w18_to_w19_migration_receipt_file.read_bytes()
    real_write = migration_module.write_migration_receipt

    def interrupting_write(path, receipt, *, publication_prepared=None):
        def record_then_interrupt(evidence):
            assert publication_prepared is not None
            publication_prepared(evidence)
            raise KeyboardInterrupt

        return real_write(path, receipt, publication_prepared=record_then_interrupt)

    monkeypatch.setattr(migration_module, "write_migration_receipt", interrupting_write)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(plan, paths)
    assert raised.value.reason_code == "MIGRATION_PROMOTION_FAILED"
    if previous is None:
        assert not paths.w18_to_w19_migration_receipt_file.exists()
    else:
        assert paths.w18_to_w19_migration_receipt_file.read_bytes() == previous
    assert not paths.health_report_file.exists()


def test_interrupt_immediately_after_record_marker_rolls_back_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    real_write = migration_module.write_layout_marker

    def interrupting_write(path, *, publication_prepared=None):
        def record_then_interrupt(evidence):
            assert publication_prepared is not None
            publication_prepared(evidence)
            raise KeyboardInterrupt

        return real_write(path, publication_prepared=record_then_interrupt)

    monkeypatch.setattr(migration_module, "write_layout_marker", interrupting_write)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(build_migration_plan(paths), paths)
    assert raised.value.reason_code == "MIGRATION_PROMOTION_FAILED"
    assert not paths.storage_layout_file.exists()
    assert not paths.w18_to_w19_migration_receipt_file.exists()
    assert not paths.health_report_file.exists()


@pytest.mark.parametrize("kind", ["receipt", "marker"])
def test_foreign_metadata_after_evidence_transfer_is_preserved_and_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    attribute = "write_migration_receipt" if kind == "receipt" else "write_layout_marker"
    real_write = getattr(migration_module, attribute)
    target = (
        paths.w18_to_w19_migration_receipt_file
        if kind == "receipt"
        else paths.storage_layout_file
    )

    def interrupting_write(path, *args, publication_prepared=None):
        real_write(path, *args, publication_prepared=publication_prepared)
        replacement = target.with_suffix(".foreign")
        replacement.write_bytes(b"foreign")
        os.replace(replacement, target)
        raise KeyboardInterrupt

    monkeypatch.setattr(migration_module, attribute, interrupting_write)
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(build_migration_plan(paths), paths)
    assert raised.value.reason_code == "MIGRATION_ROLLBACK_UNPROVEN"
    assert target.read_bytes() == b"foreign"


def test_valid_receipt_from_different_profile_conflicts_before_promotion(tmp_path: Path) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    receipt_path = paths.w18_to_w19_migration_receipt_file
    wrong = migration_module.make_migration_receipt(
        source_profile="xdg_default",
        copied=[],
        identical=[],
        preserved_legacy_top_level=[],
    )
    migration_module.write_migration_receipt(receipt_path, wrong)
    original = receipt_path.read_bytes()
    with pytest.raises(StorageMigrationError) as raised:
        migrate_plan(plan, paths)
    assert raised.value.reason_code == "MIGRATION_TARGET_CONFLICT"
    assert receipt_path.read_bytes() == original
    assert not paths.health_report_file.exists()
    assert not paths.storage_layout_file.exists()


def test_valid_receipt_from_same_profile_remains_retry_safe(tmp_path: Path) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    previous = migration_module.make_migration_receipt(
        source_profile=plan.profile.name,
        copied=[],
        identical=[],
        preserved_legacy_top_level=[],
    )
    migration_module.write_migration_receipt(paths.w18_to_w19_migration_receipt_file, previous)
    assert migrate_plan(plan, paths) == paths.w18_to_w19_migration_receipt_file
    assert paths.health_report_file.exists()
    assert paths.storage_layout_file.exists()


def test_canonical_receipt_validation_failure_rolls_back_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)
    plan = build_migration_plan(paths)
    previous_receipt = migration_module.make_migration_receipt(
        source_profile=plan.profile.name,
        copied=[],
        identical=[],
        preserved_legacy_top_level=[],
    )
    paths.w18_to_w19_migration_receipt_file.parent.mkdir(parents=True, exist_ok=True)
    migration_module.write_migration_receipt(
        paths.w18_to_w19_migration_receipt_file, previous_receipt
    )
    previous = paths.w18_to_w19_migration_receipt_file.read_bytes()
    real_validator = migration_module.read_migration_receipt
    calls = 0

    def fail_after_publication(path: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageMigrationError("MIGRATION_RECEIPT_INVALID")
        return real_validator(path)

    monkeypatch.setattr(migration_module, "read_migration_receipt", fail_after_publication)
    with pytest.raises(StorageMigrationError):
        migrate_plan(plan, paths)
    assert paths.w18_to_w19_migration_receipt_file.read_bytes() == previous
    assert not paths.storage_layout_file.exists()
    assert not paths.health_report_file.exists()


def test_canonical_marker_validation_failure_rolls_back_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.runtime.storage_migration as migration_module

    paths = _legacy_home(tmp_path)

    def fail_after_publication(_path: Path):
        raise StorageMigrationError("LAYOUT_MARKER_INVALID")

    monkeypatch.setattr(migration_module, "read_layout_marker", fail_after_publication)
    with pytest.raises(StorageMigrationError):
        migrate_plan(build_migration_plan(paths), paths)
    assert not paths.storage_layout_file.exists()
    assert not paths.w18_to_w19_migration_receipt_file.exists()
    assert not paths.health_report_file.exists()
