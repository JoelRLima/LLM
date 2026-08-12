import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_installed_package import (
    INSTALLED_PROBE_SOURCE,
    CommandResult,
    VerificationError,
    _run,
    _validate_slice_a_payload,
    _validate_slice_b_payload,
    _validate_slice_c_payload,
    _validate_slice_d_payload,
    installation_mode,
    installed_cli_commands,
    parse_json_output,
    snapshot_tree,
    verify_installed_package,
    wheel_install_command,
)


def test_gate_declares_required_installed_cli_journeys(tmp_path: Path) -> None:
    executable = tmp_path / "llm-agent"
    workspace = tmp_path / "workspace"

    commands = dict(installed_cli_commands(executable, workspace))

    assert commands == {
        "version": (str(executable), "--version"),
        "config-init": (str(executable), "config", "init"),
        "doctor": (str(executable), "doctor", "--json"),
        "run": (
            str(executable),
            "run",
            "--json",
            "--workspace",
            str(workspace),
            "oi",
        ),
    }


def test_gate_snapshot_detects_runtime_writes(tmp_path: Path) -> None:
    before = snapshot_tree(tmp_path)
    (tmp_path / "unexpected.txt").write_text("side effect", encoding="utf-8")

    assert snapshot_tree(tmp_path) != before


def test_gate_accepts_only_clean_non_empty_json_objects() -> None:
    assert parse_json_output(CommandResult("doctor", '{"status": "ok"}', "")) == {
        "status": "ok"
    }

    with pytest.raises(VerificationError, match="JSON puro"):
        parse_json_output(CommandResult("doctor", 'log\n{"status": "ok"}', ""))
    with pytest.raises(VerificationError, match="não vazio"):
        parse_json_output(CommandResult("doctor", "{}", ""))


def test_ci_runs_installed_wheel_gate_after_pytest() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    assert workflow.index("- name: Pytest") < workflow.index(
        "python scripts/verify_installed_package.py"
    )
    gate_lines = [
        line.strip()
        for line in workflow.splitlines()
        if "scripts/verify_installed_package.py" in line
    ]

    assert gate_lines == ["run: python scripts/verify_installed_package.py"]


def test_clean_acceptance_is_default_and_offline_mode_is_explicitly_weaker() -> None:
    clean = installation_mode()
    offline = installation_mode(offline_diagnostic=True)

    assert clean.name == "clean-acceptance"
    assert clean.system_site_packages is False
    assert clean.install_dependencies is True
    assert clean.acceptance is True
    assert offline.name == "offline-diagnostic"
    assert offline.system_site_packages is True
    assert offline.install_dependencies is False
    assert offline.acceptance is False


def test_clean_install_resolves_declared_dependencies() -> None:
    clean_command = wheel_install_command(
        Path("python"),
        Path("package.whl"),
        installation_mode(),
    )
    offline_command = wheel_install_command(
        Path("python"),
        Path("package.whl"),
        installation_mode(offline_diagnostic=True),
    )

    assert "--no-deps" not in clean_command
    assert "--force-reinstall" not in clean_command
    assert "--no-deps" in offline_command
    assert "--force-reinstall" in offline_command


def test_gate_closes_stdin_for_subprocesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _run("stdin-policy", ("python", "--version"), cwd=tmp_path)

    assert captured["stdin"] is subprocess.DEVNULL


def test_site_packages_snapshot_precedes_all_runtime_probes() -> None:
    source = inspect.getsource(verify_installed_package)

    snapshot = source.index("site_before = snapshot_tree(site_packages)")
    dependencies = source.index("_verify_declared_dependencies(")
    installed_probe = source.index("_verify_installed_probe(")
    import_probe = source.index("_verify_import_origin(")

    assert snapshot < dependencies < installed_probe < import_probe


def test_installed_gate_includes_extension_aware_bootstrap_probe() -> None:
    source = inspect.getsource(verify_installed_package)

    assert "_verify_extension_aware_bootstrap(" in source
    assert "extension-workspace" in source
    assert "extension-app-home" in source


def test_installed_probe_covers_local_history_and_remerge_denials() -> None:
    source = inspect.getsource(verify_installed_package)
    assert "_verify_missing_config_recovery(" in source

    assert '"git_log_one": git_reader.execute' in INSTALLED_PROBE_SOURCE
    assert '"shell_log": shell.execute' in INSTALLED_PROBE_SOURCE
    assert '"git_remerge": git_reader.execute' in INSTALLED_PROBE_SOURCE
    assert '"shell_remerge": shell.execute' in INSTALLED_PROBE_SOURCE
    source = inspect.getsource(verify_installed_package)
    assert source.index("_prepare_local_history_workspace(") < source.index(
        "workspace_before = snapshot_tree(workspace)"
    )
    assert '"shell_ruff_add_noqa"' in INSTALLED_PROBE_SOURCE
    assert '"shell_ruff_add_ignore"' in INSTALLED_PROBE_SOURCE
    assert '"shell_ruff_fix"' in INSTALLED_PROBE_SOURCE


def test_installed_probe_covers_slice_a_journey_and_measurement() -> None:
    assert "AgentApplication.create" in INSTALLED_PROBE_SOURCE
    assert "run_slice_a_journeys" in INSTALLED_PROBE_SOURCE
    for marker in ("SLICE_A1", "SLICE_A2", "SLICE_A3", "a4_no_tool"):
        assert marker in INSTALLED_PROBE_SOURCE
    for field in (
        "task_id",
        "duration_ms",
        "invocation_id",
        "terminal_outcome",
        "output_chars",
        "truncated",
    ):
        assert field in INSTALLED_PROBE_SOURCE
    source = inspect.getsource(_validate_slice_a_payload)
    assert 'payload.get("slice_a")' in source


def test_installed_probe_covers_slice_c_journey_and_reuses_measurement() -> None:
    assert "run_shell_journeys" in INSTALLED_PROBE_SOURCE
    for marker in ("SLICE_C1", "SLICE_C2", "SLICE_C3", "git log -1", "git status"):
        assert marker in INSTALLED_PROBE_SOURCE
    assert 'project_measurement(name, objective, started_at, application, result, family="c")' in INSTALLED_PROBE_SOURCE
    source = inspect.getsource(_validate_slice_c_payload)
    assert 'payload.get("slice_c")' in source


def test_installed_probe_covers_slice_b_modify_validate_journey() -> None:
    assert "run_modify_journeys" in INSTALLED_PROBE_SOURCE
    for marker in ("SLICE_B1", "SLICE_B2", "SLICE_B3", "SLICE_B4", "code_task", "file_writer"):
        assert marker in INSTALLED_PROBE_SOURCE
    source = inspect.getsource(_validate_slice_b_payload)
    assert 'payload.get("slice_b")' in source
    assert "validation_invocation_id" in INSTALLED_PROBE_SOURCE


def test_installed_probe_covers_external_stdio_slice_d() -> None:
    assert "run_extension_journeys" in INSTALLED_PROBE_SOURCE
    for marker in ("SLICE_D1", "SLICE_D3", "SLICE_D4", "demo_tool", "TaskAuthoritySnapshot", "stdio_process_required"):
        assert marker in INSTALLED_PROBE_SOURCE
    source = inspect.getsource(_validate_slice_d_payload)
    assert 'payload.get("slice_d")' in source
    assert "spawned" in source
