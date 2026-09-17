"""Focused tests for W18 release-builder subprocess diagnostics and preconditions."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from distribution.release_identity import UV_ASSET_URL, UV_SIGNATURE, UV_VERSION
from scripts import build_windows_payload as builder


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    (
        ("stdout failure\n", "", "[stdout]\nstdout failure"),
        ("", "stderr failure\n", "[stderr]\nstderr failure"),
        (
            "stdout failure\n",
            "stderr failure\n",
            "[stdout]\nstdout failure\n\n[stderr]\nstderr failure",
        ),
    ),
)
def test_run_reports_captured_failure_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    def raise_failure(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(1, ["fake"], output=stdout, stderr=stderr)

    monkeypatch.setattr(builder.subprocess, "run", raise_failure)

    with pytest.raises(builder.BuildError) as error:
        builder._run(["fake"], tmp_path, environment={})

    assert expected in str(error.value)


def test_run_preserves_builder_failure_category_on_command_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_failure(*_args: object, **_kwargs: object) -> object:
        raise subprocess.CalledProcessError(7, ["fake"], stderr="failed\n")

    monkeypatch.setattr(builder.subprocess, "run", raise_failure)

    with pytest.raises(builder.BuildError, match="command failed: fake"):
        builder._run(["fake"], tmp_path, environment={})


def test_verify_build_driver_requires_frozen_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: "pip 25.0.1 from base\n")

    with pytest.raises(builder.BuildError, match=r"must provide pip 26\.2\.1; found 25\.0\.1"):
        builder._verify_build_driver(Path("python.exe"))


def test_verify_build_driver_checks_declared_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> str:
        calls.append(command)
        return "pip 26.2.1 from selected\n"

    monkeypatch.setattr(builder, "_run", fake_run)

    builder._verify_build_driver(Path("python.exe"))

    assert calls == [
        ("python.exe", "-m", "pip", "--version"),
        ("python.exe", "-c", "import setuptools.build_meta"),
    ]


def test_recorded_console_launchers_are_pruned_by_distribution_metadata(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    dependency_dist_info = site_packages / "sample_dependency-1.0.dist-info"
    application_dist_info = site_packages / "local_llm_agent-0.2.0rc1.dist-info"
    dependency_dist_info.mkdir(parents=True)
    application_dist_info.mkdir(parents=True)
    dependency_launcher = site_packages / "bin" / "sample-tool.exe"
    application_launcher = site_packages / "bin" / "llm-agent.exe"
    dependency_launcher.parent.mkdir(parents=True)
    dependency_launcher.write_bytes(b"dependency launcher")
    application_launcher.write_bytes(b"application launcher")
    (dependency_dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nsample-tool = sample_dependency:main\n",
        encoding="utf-8",
    )
    (application_dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nllm-agent = agent.interfaces.cli.app:main\n",
        encoding="utf-8",
    )
    (dependency_dist_info / "RECORD").write_text(
        "bin/sample-tool.exe,sha256=stale,18\n",
        encoding="utf-8",
    )
    (application_dist_info / "RECORD").write_text(
        "bin/llm-agent.exe,sha256=stale,19\n",
        encoding="utf-8",
    )

    removed = builder._remove_recorded_console_launchers(runtime)

    assert removed == {dependency_launcher.resolve(), application_launcher.resolve()}
    assert not dependency_launcher.exists()
    assert not application_launcher.exists()


def _uv_archive(tmp_path: Path) -> Path:
    artifact = tmp_path / Path(UV_ASSET_URL).name
    with zipfile.ZipFile(artifact, "w") as archive:
        for name in ("uv.exe", "uvw.exe", "uvx.exe"):
            archive.writestr(name, name.encode("ascii"))
    return artifact


def _expected_uv_signature() -> dict[str, str]:
    return {key: str(value) for key, value in UV_SIGNATURE.items()}


def test_uv_mismatch_is_rejected_before_archive_acceptance(tmp_path: Path) -> None:
    artifact = _uv_archive(tmp_path)

    with pytest.raises(builder.BuildError, match="SHA-256 mismatch"):
        builder._verify_uv_artifact(artifact)


def test_uv_wrong_version_is_rejected_from_verified_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _uv_archive(tmp_path)
    monkeypatch.setattr(builder, "sha256_file", lambda _path: builder.UV_SHA256)
    monkeypatch.setattr(builder, "_read_uv_authenticode", lambda _path: _expected_uv_signature())
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: "uv 0.12.12 (wrong platform)")

    with pytest.raises(builder.BuildError, match="wrong version"):
        builder._verify_uv_artifact(artifact)


def test_uv_wrong_platform_is_rejected_from_verified_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _uv_archive(tmp_path)
    monkeypatch.setattr(builder, "sha256_file", lambda _path: builder.UV_SHA256)
    monkeypatch.setattr(builder, "_read_uv_authenticode", lambda _path: _expected_uv_signature())
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: f"uv {UV_VERSION} (aarch64-unknown-linux-gnu)")

    with pytest.raises(builder.BuildError, match="wrong platform"):
        builder._verify_uv_artifact(artifact)


def test_uv_correct_version_hash_and_signature_produce_manifest_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _uv_archive(tmp_path)
    monkeypatch.setattr(builder, "sha256_file", lambda _path: builder.UV_SHA256)
    monkeypatch.setattr(builder, "_read_uv_authenticode", lambda _path: _expected_uv_signature())
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: f"uv {UV_VERSION} (x86_64-pc-windows-msvc)")

    verified = builder._verify_uv_artifact(artifact)

    assert verified.artifact_name == artifact.name
    assert verified.manifest_evidence() == {
        "version": UV_VERSION,
        "asset_url": UV_ASSET_URL,
        "sha256": builder.UV_SHA256,
        "expected_signature": _expected_uv_signature(),
    }
    assert json.loads(json.dumps(verified.to_dict()))["verification"].startswith("artifact_hash")


def test_uv_authenticode_mismatch_is_rejected_after_version_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _uv_archive(tmp_path)
    bad_signature = _expected_uv_signature()
    bad_signature["signer_thumbprint"] = "BAD"
    monkeypatch.setattr(builder, "sha256_file", lambda _path: builder.UV_SHA256)
    monkeypatch.setattr(builder, "_read_uv_authenticode", lambda _path: bad_signature)
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: f"uv {UV_VERSION} (x86_64-pc-windows-msvc)")

    with pytest.raises(builder.BuildError, match="Authenticode evidence mismatch"):
        builder._verify_uv_artifact(artifact)


def test_uv_adversarial_cases_use_production_validators() -> None:
    verification = builder.UvVerification(
        artifact_name=Path(UV_ASSET_URL).name,
        artifact_sha256=builder.UV_SHA256,
        version=UV_VERSION,
        asset_url=UV_ASSET_URL,
        archive_members=("uv.exe", "uvw.exe", "uvx.exe"),
        signature=_expected_uv_signature(),
    )

    cases = builder._uv_adversarial_cases(verification)

    assert cases["valid_pinned_artifact"] == {"status": "passed", "outcome": "accepted"}
    assert {name: item["outcome"] for name, item in cases.items() if name != "valid_pinned_artifact"} == {
        "wrong_hash": "rejected",
        "wrong_version": "rejected",
        "wrong_platform": "rejected",
        "authenticode": "rejected",
    }
