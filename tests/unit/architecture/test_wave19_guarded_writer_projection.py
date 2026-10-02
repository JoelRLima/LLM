from __future__ import annotations

import ast
from pathlib import Path

from scripts import check_wave19_architecture as checker

_CURRENT_MIGRATION_PATH = "src/llm_agent/application/state_migration/operations.py"
_CURRENT_MIGRATION_GUARDS = (
    "HomeLifecycleLease.begin_transient",
    "StorageBootstrap().prepare",
)


def _remove_line_containing(source: str, needle: str) -> str:
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if needle in line:
            del lines[index]
            return "".join(lines)
    raise AssertionError(f"missing mutation target: {needle}")


def _write_function(root: Path, relative: str, function: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(f"{existing}\ndef {function}():\n{body}\n", encoding="utf-8")


def _seed_other_guarded_writers(root: Path, *, include_c4: bool = True) -> None:
    guard_body = "    HomeLifecycleLease.begin_transient(home)\n    StorageBootstrap().prepare(paths)"
    _write_function(
        root,
        "src/llm_agent/agent/application.py",
        "create",
        "    HomeLifecycleLease.begin_startup(home)\n"
        "    StorageBootstrap().prepare(paths)\n"
        "    home_lease.activate()",
    )
    for function in ("initialize_config", "run_config"):
        _write_function(root, "src/llm_agent/interfaces/cli/maintenance.py", function, guard_body)
    for function, method, argument in (
        ("initialize_configuration", "initialize", ""),
        ("migrate_configuration", "migrate", "source"),
    ):
        call = f"repository.{method}({argument})" if argument else f"repository.{method}()"
        _write_function(
            root,
            "src/llm_agent/application/configuration_admin.py",
            function,
            "    repository = ConfigRepository(app_paths, config_path=config_path)\n"
            "    target = repository.path.resolve()\n"
            "    home = app_paths.home_dir.resolve()\n"
            "    try:\n"
            "        target.relative_to(home)\n"
            "    except ValueError:\n"
            f"        return {call}\n"
            "    lease = HomeLifecycleLease.begin_transient(home)\n"
            "    try:\n"
            "        StorageBootstrap().prepare(app_paths)\n"
            f"        return {call}\n"
            "    finally:\n"
            "        lease.close()",
        )
    if include_c4:
        for function, mutation in (("add_legacy_extension", "add"), ("set_legacy_extension_enabled", "set_enabled")):
            _write_function(
                root,
                "src/llm_agent/application/legacy_extension_registry.py",
                function,
                "    target = _target(paths, state_path)\n"
                "    if _canonical_target(target, paths):\n"
                "        lease = HomeLifecycleLease.begin_transient(home)\n"
                "        try:\n"
                "            StorageBootstrap().prepare(paths)\n"
                "            registry = ExtensionRegistry(target)\n"
                f"            return registry.{mutation}(id, value)\n"
                "        finally:\n"
                "            lease.close()\n"
                "    registry = ExtensionRegistry(target)\n"
                f"    return registry.{mutation}(id, value)",
            )
    _write_function(root, "src/llm_agent/interfaces/cli/extensions.py", "run_extensions", guard_body)
    _write_function(root, "src/llm_agent/agent/health/standalone.py", "write_health_report", guard_body)
    _write_function(
        root,
        "src/llm_agent/interfaces/cli/first_run.py",
        "recover_first_run_config",
        "    lease = HomeLifecycleLease.begin_transient(home)\n"
        "    try:\n"
        "        StorageBootstrap().prepare(paths)\n"
        "        view = read_first_run_configuration(paths, None)\n"
        "        _complete_guided_setup(args, view, paths, console, prompt)\n"
        "    finally:\n"
        "        lease.close()",
    )
    _write_function(
        root,
        "src/llm_agent/interfaces/cli/first_run.py",
        "_complete_guided_setup",
        "    selected = prompt('profile')\n"
        "    update_first_run_configuration(paths, None, selected, model, endpoint)",
    )


def _state_migration_findings(root: Path) -> list[checker.ArchitectureViolation]:
    return [
        finding
        for finding in checker._check_guarded_writers(root)
        if finding.rule_id == "W19-S05-009" and finding.path == _CURRENT_MIGRATION_PATH
    ]


def test_w19_s05_009_follows_state_migration_owner_and_rejects_missing_guards(tmp_path: Path) -> None:
    guard_body = "    HomeLifecycleLease.begin_transient(home)\n    StorageBootstrap().prepare(paths)"

    passing_root = tmp_path / "passing"
    _seed_other_guarded_writers(passing_root)
    _write_function(
        passing_root,
        "src/llm_agent/interfaces/cli/maintenance.py",
        "run_state",
        "    return migrate_state(request)",
    )
    _write_function(passing_root, _CURRENT_MIGRATION_PATH, "migrate_state", guard_body)
    assert _state_migration_findings(passing_root) == []

    for index, missing_guard in enumerate(_CURRENT_MIGRATION_GUARDS):
        failing_root = tmp_path / f"missing-{index}"
        _seed_other_guarded_writers(failing_root, include_c4=False)
        _write_function(
            failing_root,
            "src/llm_agent/interfaces/cli/maintenance.py",
            "run_state",
            "    return migrate_state(request)",
        )
        remaining_guards = [guard for guard in _CURRENT_MIGRATION_GUARDS if guard != missing_guard]
        _write_function(
            failing_root,
            _CURRENT_MIGRATION_PATH,
            "migrate_state",
            "\n".join(f"    {guard}(value)" for guard in remaining_guards) or "    pass",
        )
        findings = _state_migration_findings(failing_root)
        assert len(findings) == 1
        assert missing_guard in findings[0].detail

    old_owner_root = tmp_path / "old-owner-only"
    _seed_other_guarded_writers(old_owner_root)
    _write_function(
        old_owner_root,
        "src/llm_agent/interfaces/cli/maintenance.py",
        "run_state",
        guard_body,
    )
    _write_function(old_owner_root, _CURRENT_MIGRATION_PATH, "migrate_state", "    pass")
    findings = _state_migration_findings(old_owner_root)
    assert len(findings) == 2
    assert {finding.detail for finding in findings} == {
        "migrate_state is missing HomeLifecycleLease.begin_transient",
        "migrate_state is missing StorageBootstrap().prepare",
    }


def test_w19_config_writers_follow_application_owner_and_reject_missing_guard(tmp_path: Path) -> None:
    root = tmp_path / "configuration-writers"
    _seed_other_guarded_writers(root)
    relative = "src/llm_agent/application/configuration_admin.py"
    findings = [
        item for item in checker._check_guarded_writers(root)
        if item.rule_id == "W19-S05-009" and item.path == relative
    ]
    assert findings == []

    path = root / relative
    source = path.read_text(encoding="utf-8")
    guard = "StorageBootstrap().prepare(app_paths)\n"
    assert guard in source
    path.write_text(source.replace(guard, "pass\n", 1), encoding="utf-8")
    findings = [
        item for item in checker._check_guarded_writers(root)
        if item.rule_id == "W19-S05-009" and item.path == relative
    ]
    assert findings

    first_run = root / "src/llm_agent/interfaces/cli/first_run.py"
    first_run_source = first_run.read_text(encoding="utf-8")
    first_run.write_text(first_run_source.replace("read_first_run_configuration(paths, None)", "object()", 1), encoding="utf-8")
    findings = [
        item for item in checker._check_guarded_writers(root)
        if item.rule_id == "W19-S05-009"
        and item.path == "src/llm_agent/interfaces/cli/first_run.py"
    ]
    assert findings


def test_w19_s05_009_follows_legacy_registry_canonical_mutations_and_order(tmp_path: Path) -> None:
    relative = "src/llm_agent/application/legacy_extension_registry.py"
    for function in ("add_legacy_extension", "set_legacy_extension_enabled"):
        passing_root = tmp_path / f"passing-{function}"
        _seed_other_guarded_writers(passing_root)
        findings = [
            item for item in checker._check_guarded_writers(passing_root)
            if item.path == relative
        ]
        assert findings == []

    invalid_bodies = (
        "    target = _target(paths, state_path)\n"
        "    if _canonical_target(target, paths):\n"
        "        lease = HomeLifecycleLease.begin_transient(home)\n"
        "        try:\n"
        "            registry = ExtensionRegistry(target)\n"
        "            StorageBootstrap().prepare(paths)\n"
        "            return registry.add(id, value)\n"
        "        finally:\n"
        "            lease.close()\n"
        "    registry = ExtensionRegistry(target)\n"
        "    return registry.add(id, value)",
        "    target = _target(paths, state_path)\n"
        "    if _canonical_target(target, paths):\n"
        "        lease = HomeLifecycleLease.begin_transient(home)\n"
        "        try:\n"
        "            StorageBootstrap().prepare(paths)\n"
        "            registry = ExtensionRegistry(target)\n"
        "            return registry.add(id, value)\n"
        "        finally:\n"
        "            pass\n"
        "    registry = ExtensionRegistry(target)\n"
        "    return registry.add(id, value)",
        "    target = _target(paths, state_path)\n"
        "    if _canonical_target(target, paths):\n"
        "        lease = HomeLifecycleLease.begin_transient(home)\n"
        "        try:\n"
        "            StorageBootstrap().prepare(paths)\n"
        "            registry = ExtensionRegistry(target)\n"
        "            return registry.add(id, value)\n"
        "        finally:\n"
        "            lease.close()\n"
        "    registry = ExtensionRegistry(target)\n"
        "    return registry.add(id, value)",
    )
    for index, body in enumerate(invalid_bodies):
        failing_root = tmp_path / f"invalid-{index}"
        _seed_other_guarded_writers(failing_root, include_c4=False)
        if index == 2:
            body = _remove_line_containing(body, "StorageBootstrap().prepare(paths)")
        _write_function(failing_root, relative, "add_legacy_extension", body)
        mutated_source = (failing_root / relative).read_text(encoding="utf-8")
        ast.parse(mutated_source)
        findings = [
            item for item in checker._check_guarded_writers(failing_root)
            if item.path == relative and "add_legacy_extension" in item.detail
        ]
        assert findings, f"invalid guarded-writer variant {index} was accepted"

    cli_only_root = tmp_path / "cli-only"
    _seed_other_guarded_writers(cli_only_root, include_c4=False)
    _write_function(
        cli_only_root,
        "src/llm_agent/interfaces/cli/maintenance.py",
        "run_tools",
        "    HomeLifecycleLease.begin_transient(home)\n    StorageBootstrap().prepare(paths)",
    )
    cli_only_findings = [
        item for item in checker._check_guarded_writers(cli_only_root)
        if item.path == relative
    ]
    assert len(cli_only_findings) == 4
    assert any("add_legacy_extension is missing" in item.detail for item in cli_only_findings)
    assert any("set_legacy_extension_enabled is missing" in item.detail for item in cli_only_findings)
