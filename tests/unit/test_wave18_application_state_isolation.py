"""Focused tests for W18 verifier application-state isolation."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from scripts import verify_installed_product as verifier


def test_application_home_and_real_install_root_are_distinct(tmp_path: Path) -> None:
    working = tmp_path / "w18-v003-installed-product-test"
    working.mkdir()
    install_root = tmp_path / "real-local" / "local-llm-agent" / "install"

    application_home = verifier._v3_application_home(working, install_root)

    assert application_home.is_relative_to(working.resolve())
    assert application_home != install_root.resolve()
    assert not application_home.is_relative_to(install_root.resolve())
    assert not install_root.resolve().is_relative_to(application_home)


def test_install_root_remains_derived_from_real_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_appdata = tmp_path / "real-local-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("LLM_AGENT_HOME", str(tmp_path / "host-home"))

    assert verifier._install_root() == (
        local_appdata / "local-llm-agent" / "install"
    ).resolve()


def test_state_sentinels_use_all_paths_from_disposable_app_paths(tmp_path: Path) -> None:
    application_home = tmp_path / "application-home"
    app_paths = AppPaths.discover(
        app_home=application_home,
        env={"LLM_AGENT_HOME": str(tmp_path / "host-home")},
    )

    config, sentinels = verifier._state_sentinels(app_paths)
    verifier._v3_assert_app_paths(application_home.resolve(), app_paths)

    assert config == (application_home / "config" / "config.json").resolve()
    assert sentinels == [
        config,
        (application_home / "global" / "w18-preservation-sentinel.txt").resolve(),
        (application_home / "workspaces" / "w18-preservation-sentinel.txt").resolve(),
        (application_home / "cache" / "w18-preservation-sentinel.txt").resolve(),
        (application_home / "logs" / "w18-preservation-sentinel.txt").resolve(),
    ]
    assert all(path.is_relative_to(application_home.resolve()) for path in sentinels)


def test_host_llm_agent_home_cannot_seize_child_application_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_home = tmp_path / "host-home"
    application_home = tmp_path / "harness-home"
    monkeypatch.setenv("LLM_AGENT_HOME", str(host_home))

    environment = verifier._v3_clean_child_environment(
        "C:\\Windows\\System32",
        application_home,
    )

    assert environment["LLM_AGENT_HOME"] == str(application_home.resolve())
    assert environment["LLM_AGENT_HOME"] != str(host_home)


def test_real_default_config_guard_is_read_only_and_hash_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appdata = tmp_path / "real-appdata"
    config = appdata / "local-llm-agent" / "config" / "config.json"
    config.parent.mkdir(parents=True)
    original = b'{"real": "user state"}\n'
    config.write_bytes(original)
    monkeypatch.setenv("APPDATA", str(appdata))

    before = verifier._snapshot_file(
        verifier._real_default_windows_config(),
        "real default Windows config",
    )
    verifier._assert_file_snapshot_unchanged(before, "real default Windows config")

    assert config.read_bytes() == original
    assert before.present is True
    assert before.sha256 == hashlib.sha256(original).hexdigest()


def test_missing_real_default_config_remains_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "real-appdata"))

    before = verifier._snapshot_file(
        verifier._real_default_windows_config(),
        "real default Windows config",
    )

    verifier._assert_file_snapshot_unchanged(before, "real default Windows config")

    assert before.present is False
    assert before.sha256 is None
    assert not verifier._real_default_windows_config().exists()


def test_existing_real_config_is_never_overwritten_by_preservation_writer(
    tmp_path: Path,
) -> None:
    config = tmp_path / "real" / "config.json"
    config.parent.mkdir()
    original = b"existing config\n"
    config.write_bytes(original)

    with pytest.raises(verifier.ProductVerificationError, match="refuses to overwrite"):
        verifier._write_preservation_state(config, [config])

    assert config.read_bytes() == original


def test_preservation_state_remains_after_install_root_cleanup(tmp_path: Path) -> None:
    application_home = tmp_path / "application-home"
    app_paths = AppPaths.discover(app_home=application_home, env={})
    config, sentinels = verifier._state_sentinels(app_paths)
    verifier._write_preservation_state(config, sentinels)
    install_root = tmp_path / "real-install-root"
    install_root.mkdir()
    (install_root / "installed-product").write_text("product\n", encoding="utf-8")

    for path in sentinels:
        assert path.exists()
    verifier._assert_preservation(sentinels)
    shutil.rmtree(install_root)
    verifier._assert_preservation(sentinels)
    assert application_home.is_dir()
    assert not install_root.exists()
