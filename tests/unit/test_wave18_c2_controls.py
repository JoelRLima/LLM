from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.build_windows_payload import _write_payload_shim

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_cleanup_holds_the_mutex_across_deletion() -> None:
    source = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8-sig")
    cleanup = source.split("function Cleanup-StaleCandidates", 1)[1].split(
        "function Remove-InstallRootAfterUninstall", 1
    )[0]
    assert cleanup.index("Enter-W18CandidateLease") < cleanup.index("Remove-OwnedTree")
    assert cleanup.index("Remove-OwnedTree") < cleanup.index("Exit-W18CandidateLease")
    assert 'Global\\W18-candidate-gate-' in source
    assert 'Global\\W18-candidate-active-' in source


def test_uninstall_acquires_candidate_leases_before_path_or_tree_mutation() -> None:
    source = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8-sig")
    uninstall = source.split("function Invoke-Uninstall", 1)[1].split(
        "function Get-W18CandidateDirectories", 1
    )[0]
    assert uninstall.index("Enter-W18UninstallCandidateLeases") < uninstall.index("$pathMutation")
    assert uninstall.index("Enter-W18UninstallCandidateLeases") < uninstall.index("Move-Item -LiteralPath $paths.VersionsRoot")


def test_active_candidate_uninstall_acceptance_snapshots_and_restores_state() -> None:
    source = (ROOT / "scripts" / "verify_installed_product.py").read_text(encoding="utf-8")
    snapshot = source.split("def _v3_snapshot_active_uninstall", 1)[1].split(
        "def _v3_assert_candidate_trees_unchanged", 1
    )[0]
    blocked = source.split("def _v3_assert_blocked_uninstall", 1)[1].split(
        "def _v3_record_blocked_uninstall", 1
    )[0]
    orchestrator = source.split("def _v3_verify_active_candidate_uninstall", 1)[1].split(
        "def _v3_assert_current_previous", 1
    )[0]
    assert "receipt_bytes" in snapshot
    assert "stable_bytes" in snapshot
    assert "candidate_digests" in snapshot
    assert "transaction_state" in snapshot
    assert "_v3_assert_candidate_trees_unchanged" in blocked
    assert "_v3_launch_lease_probe" in orchestrator
    assert "_v3_record_blocked_uninstall" in orchestrator
    assert "_v3_restore_after_active_uninstall" in orchestrator


def test_write_ahead_flags_precede_real_side_effects() -> None:
    source = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8-sig")
    install = source.split("function Invoke-Install", 1)[1].split("function Invoke-Uninstall", 1)[0]
    launcher_flag = install.index("$journal.launcher_promoted = $true")
    launcher_write = install.index("Write-Journal $paths $journal", launcher_flag)
    launcher_replace = install.index("$stableSha = Promote-StableLauncher", launcher_write)
    path_flag = install.index("$journal.path_mutated = $true")
    path_write = install.index("Write-Journal $paths $journal", path_flag)
    path_registry = install.index("Set-UserPathValue $priorUserPath", path_write)
    assert launcher_flag < launcher_write < launcher_replace
    assert path_flag < path_write < path_registry


def test_installed_shim_acquires_before_normal_application_import() -> None:
    source = (ROOT / "scripts" / "build_windows_payload.py").read_text(encoding="utf-8")
    shim = source.split("def _write_payload_shim", 1)[1].split("def _assert_required_payload_files", 1)[0]
    assert "spec_from_file_location" in shim
    assert "runtime' / 'Lib' / 'site-packages' / 'agent' / 'runtime' / 'candidate_lease.py'" in shim
    assert shim.index("exec_module(_lease_module)") < shim.index("from agent.interfaces.cli.app import main")
    assert shim.index("acquire_runtime_candidate_lease(_launcher)") < shim.index(
        "from agent.interfaces.cli.app import main"
    )
    bootstrap = shim[: shim.index("from agent.interfaces.cli.app import main")]
    assert "from agent" not in bootstrap
    assert "import agent" not in bootstrap


def test_generated_launcher_establishes_reference_before_agent_package_import(tmp_path: Path) -> None:
    candidate = tmp_path / ("w18-" + "a" * 32)
    site_packages = candidate / "runtime" / "Lib" / "site-packages"
    lease_source = site_packages / "agent" / "runtime" / "candidate_lease.py"
    lease_source.parent.mkdir(parents=True)
    lease_source.write_text(
        "from pathlib import Path\n"
        "def acquire_runtime_candidate_lease(launcher):\n"
        "    Path(launcher).resolve().parent.parent.joinpath('active-before-agent').write_text('yes')\n"
        "    return object()\n",
        encoding="utf-8",
    )
    agent_root = site_packages / "agent"
    (agent_root / "interfaces" / "cli").mkdir(parents=True)
    (agent_root / "__init__.py").write_text(
        "from pathlib import Path\n"
        "candidate = Path(__file__).resolve().parents[4]\n"
        "assert candidate.joinpath('active-before-agent').is_file()\n",
        encoding="utf-8",
    )
    (agent_root / "interfaces" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "interfaces" / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "interfaces" / "cli" / "app.py").write_text(
        "def main():\n    return 0\n", encoding="utf-8"
    )
    _write_payload_shim(candidate)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(site_packages)
    completed = subprocess.run(
        [sys.executable, str(candidate / "app" / "launcher.py")],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_c2_sources_are_exactly_admitted_by_candidate_provenance() -> None:
    source = (ROOT / "distribution" / "provenance.py").read_text(encoding="utf-8")
    assert '"agent/runtime/candidate_lease.py"' in source
    assert '"scripts/installer_fault_instrumentation.py"' in source


def test_verifier_uses_module_level_winreg_mutation_apis() -> None:
    source = (ROOT / "scripts" / "verify_installed_product.py").read_text(encoding="utf-8")
    helper = source.split("def _v3_write_user_path_snapshot", 1)[1].split(
        "def _v3_assert_receipt_failure_preserved", 1
    )[0]
    assert "winreg.SetValueEx(key," in helper
    assert "winreg.DeleteValue(key," in helper
    assert "key.SetValueEx(" not in helper
    assert "key.DeleteValue(" not in helper
