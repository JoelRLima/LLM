"""Deterministic tests for W18 raw User PATH ownership semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.runtime import paths as runtime_paths
from agent.runtime.paths import APP_DIRECTORY_NAME, AppPaths
from distribution.release_identity import APPLICATION_NAMESPACE
from installer.path_semantics import (
    PathSnapshot,
    add_owned_segment,
    equivalent_segment,
    reconstructed_persistent_path,
    remove_owned_segment,
)

OWNED = r"C:\Users\José Teste\AppData\Local\local-llm-agent\install\bin"
ENVIRONMENT = {
    "LOCALAPPDATA": r"C:\Users\José Teste\AppData\Local",
    "SystemRoot": r"C:\Windows",
}
ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer" / "install.ps1"


def test_add_preserves_unrelated_raw_segments_and_empty_entries() -> None:
    snapshot = PathSnapshot(True, r"%SystemRoot%\System32;;C:\Other;", "ExpandString", True)

    result = add_owned_segment(snapshot, OWNED, ENVIRONMENT)

    assert result.changed is True
    assert result.value == snapshot.value + ";" + OWNED
    assert result.owned_count == 1


def test_add_collapses_only_equivalent_duplicate_owners() -> None:
    raw = r"A;%LOCALAPPDATA%\local-llm-agent\install\bin\;B;" + OWNED

    result = add_owned_segment(PathSnapshot(True, raw, "String", True), OWNED, ENVIRONMENT)

    assert result.changed is True
    assert result.value == r"A;%LOCALAPPDATA%\local-llm-agent\install\bin\;B"
    assert result.owned_count == 1


def test_remove_restores_exact_raw_unrelated_representation() -> None:
    raw = r"%SystemRoot%\System32;;A;" + OWNED + ";B;"

    result = remove_owned_segment(PathSnapshot(True, raw, "ExpandString", True), OWNED, ENVIRONMENT)

    assert result.changed is True
    assert result.value == r"%SystemRoot%\System32;;A;B;"
    assert result.owned_count == 0


def test_equivalence_is_case_insensitive_and_quote_tolerant() -> None:
    assert equivalent_segment(r'"%LOCALAPPDATA%\LOCAL-LLM-AGENT\INSTALL\BIN\\"', OWNED, ENVIRONMENT)


def test_empty_user_path_and_missing_value_are_distinct_but_safe() -> None:
    empty = add_owned_segment(PathSnapshot(True, "", "String", True), OWNED, ENVIRONMENT)
    missing = add_owned_segment(PathSnapshot(False, None, None, False), OWNED, ENVIRONMENT)

    assert empty.value == OWNED
    assert missing.value == OWNED
    assert empty.owned_count == missing.owned_count == 1


def test_fresh_path_reconstructs_machine_then_raw_user_path() -> None:
    user = PathSnapshot(True, r"%LOCALAPPDATA%\local-llm-agent\install\bin;A", "String", True)

    assert reconstructed_persistent_path(r"%SystemRoot%\System32", user, ENVIRONMENT) == (
        r"C:\Windows\System32;C:\Users\José Teste\AppData\Local\local-llm-agent\install\bin;A"
    )


def test_production_handles_present_empty_path_without_leading_separator() -> None:
    source = (Path(__file__).resolve().parents[2] / "installer" / "install.ps1").read_text(encoding="utf-8")

    assert '$value = if ([string]::IsNullOrEmpty($raw)) { $OwnedPath }' in source


def test_production_and_verifier_read_real_machine_environment_key() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_installed_product.py").read_text(encoding="utf-8")
    machine_key = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    assert machine_key in installer
    assert machine_key in verifier
    assert "_machine_path() != context.before_machine" in verifier


def test_installer_is_utf8_bom_without_mojibake_and_preserves_success_messages() -> None:
    raw = INSTALLER.read_bytes()

    assert raw.startswith(b"\xef\xbb\xbf")

    source = raw.decode("utf-8-sig")
    assert not any(marker in source for marker in ("Ã", "Â", "�"))
    assert 'Write-Output "W18 instalação concluída offline: $candidateId"' in source
    assert (
        'Write-Output "W18 uninstall concluído; config/data/state/cache/logs preservados"'
        in source
    )


def test_windows_launchers_preserve_environment_and_do_not_mutate_encoding() -> None:
    installer = INSTALLER.read_bytes().decode("utf-8-sig")
    stable_launcher = installer.split("function New-StableLauncherText {", 1)[1].split(
        "function Promote-StableLauncher", 1
    )[0]
    builder = (ROOT / "scripts" / "build_windows_payload.py").read_text(encoding="utf-8")
    candidate_launcher = builder.split("def _write_payload_shim", 1)[1].split(
        "def _assert_required_payload_files", 1
    )[0]

    forbidden_encoding_mutations = (
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "chcp",
        "[Console]::OutputEncoding",
    )
    for launcher in (stable_launcher, candidate_launcher):
        assert not any(marker in launcher for marker in forbidden_encoding_mutations)
        assert "setlocal" in launcher
        assert "PYTHONDONTWRITEBYTECODE=1" in launcher

    assert '%~dp0..\\versions\\$CandidateId\\bin\\llm-agent.cmd' in stable_launcher
    assert '%~dp0..\\\\runtime\\\\python.exe' in candidate_launcher
    assert '%~dp0..\\\\app\\\\launcher.py' in candidate_launcher


def test_future_distribution_alias_cannot_change_durable_app_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_paths.os, "name", "nt")
    appdata = tmp_path / "roaming"
    localappdata = tmp_path / "local"
    environment = {
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(localappdata),
    }
    alias_metadata = {
        "distribution_name": "future-llm-agent",
        "entry_point": "agent.interfaces.cli.app:main",
    }

    canonical = AppPaths.discover(env=environment)
    alternate = AppPaths.discover(env=environment)

    assert APP_DIRECTORY_NAME == APPLICATION_NAMESPACE
    assert alias_metadata["distribution_name"] != APPLICATION_NAMESPACE
    assert alias_metadata["entry_point"] == "agent.interfaces.cli.app:main"
    assert alternate == canonical
    assert canonical.config_dir == appdata / APPLICATION_NAMESPACE
    assert canonical.data_dir == localappdata / APPLICATION_NAMESPACE / "data"
    assert canonical.state_dir == localappdata / APPLICATION_NAMESPACE / "state"
    assert canonical.cache_dir == localappdata / APPLICATION_NAMESPACE / "cache"
    assert canonical.log_dir == localappdata / APPLICATION_NAMESPACE / "logs"
