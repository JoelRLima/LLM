"""Focused tests for W18 release-builder subprocess diagnostics and preconditions."""

from __future__ import annotations

import json
import os
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


def test_windows_powershell_probe_does_not_inherit_powershell_core_module_path(tmp_path: Path) -> None:
    powershell = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    expected_module_path = powershell.parent / "Modules"
    inherited = {
        "PATH": "safe",
        "PSModulePath": r"C:\Program Files\PowerShell\Modules;C:\Windows\System32\WindowsPowerShell\v1.0\Modules",
    }

    isolated = builder._windows_powershell_environment(inherited, powershell)

    assert isolated["PSModulePath"] == str(expected_module_path)
    assert inherited["PSModulePath"].startswith(r"C:\Program Files\PowerShell")
    assert isolated["PATH"] == "safe"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell probe is Windows-only")
def test_uv_authenticode_failure_preserves_process_and_signature_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_root = tmp_path / "Windows"
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"test-double")
    executable = tmp_path / "uv.exe"
    executable.write_bytes(b"test-double")
    monkeypatch.setenv("SystemRoot", str(system_root))
    probe = {
        "schema": "W18-UV-AUTHENTICODE-DIAGNOSTIC-V1",
        "signature_available": False,
        "status": None,
        "status_message": None,
        "signer_certificate_present": False,
        "timestamp_certificate_present": False,
        "signer_subject": None,
        "signer_thumbprint": None,
        "timestamp_subject": None,
        "timestamp_thumbprint": None,
        "raw_signature": {
            "Status": "UnknownError",
            "StatusMessage": "module load failed",
            "SignerCertificate": None,
            "TimeStamperCertificate": None,
        },
        "error": {"fully_qualified_error_id": "CouldNotAutoloadMatchingModule"},
    }
    completed = subprocess.CompletedProcess(
        [str(powershell)],
        17,
        stdout=json.dumps(probe),
        stderr="Microsoft.PowerShell.Security module could not be loaded",
    )
    calls: list[tuple[object, dict[str, object]]] = []

    def capture_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args[0], kwargs))
        return completed

    monkeypatch.setattr(builder.subprocess, "run", capture_run)

    with pytest.raises(builder.BuildError) as error:
        builder._read_uv_authenticode(executable)

    message = str(error.value)
    assert "exit_code=17" in message
    assert "[stdout]" in message
    assert "[stderr]" in message
    assert "module could not be loaded" in message
    assert "raw_signature=" in message
    assert "Status=null" in message
    assert "StatusMessage=null" in message
    assert "SignerCertificate=unavailable" in message
    assert "TimeStamperCertificate=unavailable" in message
    assert "CouldNotAutoloadMatchingModule" in message
    assert str(executable) not in message
    assert len(calls) == 1
    command, options = calls[0]
    assert isinstance(command, list)
    assert "Import-Module -Name Microsoft.PowerShell.Security" in str(command[-1])
    assert options["check"] is False
    child_environment = options["env"]
    assert isinstance(child_environment, dict)
    assert child_environment["PSModulePath"] == str(powershell.parent / "Modules")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell probe is Windows-only")
def test_uv_authenticode_json_error_preserves_bounded_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_root = tmp_path / "Windows"
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"test-double")
    executable = tmp_path / "uv.exe"
    executable.write_bytes(b"test-double")
    monkeypatch.setenv("SystemRoot", str(system_root))
    completed = subprocess.CompletedProcess(
        [str(powershell)],
        9,
        stdout='{"Status":',
        stderr="probe stderr",
    )
    monkeypatch.setattr(builder.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(builder.BuildError) as error:
        builder._read_uv_authenticode(executable)

    message = str(error.value)
    assert "exit_code=9" in message
    assert "json_error=Expecting value" in message
    assert "[stdout]" in message
    assert "[stderr]" in message
    assert "probe stderr" in message


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell probe is Windows-only")
def test_uv_authenticode_keeps_null_certificate_observations_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_root = tmp_path / "Windows"
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"test-double")
    executable = tmp_path / "uv.exe"
    executable.write_bytes(b"test-double")
    monkeypatch.setenv("SystemRoot", str(system_root))
    probe = {
        "schema": "W18-UV-AUTHENTICODE-DIAGNOSTIC-V1",
        "signature_available": True,
        "status": "UnknownError",
        "status_message": "signature status unavailable",
        "signer_certificate_present": False,
        "timestamp_certificate_present": False,
        "signer_subject": None,
        "signer_thumbprint": None,
        "timestamp_subject": None,
        "timestamp_thumbprint": None,
        "raw_signature": {
            "Status": "UnknownError",
            "StatusMessage": "signature status unavailable",
            "SignerCertificate": None,
            "TimeStamperCertificate": None,
        },
        "error": None,
    }
    completed = subprocess.CompletedProcess([str(powershell)], 0, stdout=json.dumps(probe), stderr="")
    monkeypatch.setattr(builder.subprocess, "run", lambda *_args, **_kwargs: completed)

    observed = builder._read_uv_authenticode(executable)

    assert observed == {
        "status": "UnknownError",
        "signer_subject": "",
        "signer_thumbprint": "",
        "timestamp_subject": "",
        "timestamp_thumbprint": "",
    }
    with pytest.raises(builder.BuildError, match="Authenticode evidence mismatch"):
        builder._validate_uv_signature(observed)


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


def test_w18_workflow_uses_locked_host_requirements_without_product_self_contamination() -> None:
    workflow = Path(".github/workflows/wave18-installed-product.yml").read_text(encoding="utf-8")
    locked_install = (
        "python -m pip install --isolated --no-input --disable-pip-version-check "
        "--no-cache-dir --only-binary=:all: --requirement requirements-ci.lock"
    )
    pip_install_lines = [
        line.strip() for line in workflow.splitlines() if "python -m pip install" in line
    ]

    assert locked_install in workflow
    assert "requirements-dev.txt" not in workflow
    assert "--constraint requirements-ci.lock" not in workflow
    assert all("--requirement" in line for line in pip_install_lines)
    assert not any(".[dev]" in line or " -e " in line for line in pip_install_lines)
    assert not any(line.rstrip().endswith(" .") for line in pip_install_lines)
    assert not any(
        "local-llm-agent" in line.casefold() or "local_llm_agent" in line.casefold()
        for line in pip_install_lines
    )
    assert "setuptools==81.0.0" in Path("requirements-ci.lock").read_text(encoding="utf-8")
    assert "import setuptools, setuptools.build_meta" in workflow
    assert "setuptools.__version__" in workflow

    guard_start = workflow.index("name: Assert clean W18 host before product lifecycle")
    lifecycle_start = workflow.index("name: Verify W18 installed product lifecycle")
    guard = workflow[guard_start:lifecycle_start]
    assert "metadata.version('local-llm-agent')" in guard
    assert "PackageNotFoundError" in guard
    assert "host contamination" in guard
    assert 'Get-Command -Name "llm-agent" -CommandType Application' in guard
    assert workflow.index(locked_install) < guard_start < lifecycle_start


def test_w18_workflow_wires_complete_hash_bound_evidence_chain() -> None:
    workflow = Path(".github/workflows/wave18-installed-product.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 60" in workflow
    assert "--conpty-layer-matrix-json $matrix" in workflow
    assert "scripts/build_conpty_authority_evidence.py" in workflow
    assert "--installed-product-summary $summary" in workflow
    assert "--installed-interactive $w17" in workflow
    assert "--layer-matrix $matrix" in workflow
    assert "--output $authority" in workflow
    assert "uv-build-evidence.json" in workflow
    assert "scripts/run_wave18_adversarial.py" in workflow
    for argument in (
        "--installed-evidence $summary",
        "--uv-build-evidence $uv",
        "--conpty-authority-evidence $authority",
        "--installed-interactive-evidence $w17",
        "--conpty-layer-matrix $matrix",
        "--json $output",
    ):
        assert argument in workflow
    assert "scripts/check_w18_artifact_safety.py" in workflow
    assert "w18-conpty-authority-evidence.json" in workflow
    assert "w18-conpty-layer-matrix.json" in workflow
    assert "id: verify_bounded_evidence" in workflow
    assert "steps.verify_bounded_evidence.outcome == 'success'" in workflow
    upload_start = workflow.index("name: Upload W18 local evidence")
    upload_end = workflow.index("name: Remove runner-local RAW ConPTY capture")
    upload = workflow[upload_start:upload_end]
    assert "w18-release/uv-build-evidence.json" in upload
    assert "${{ runner.temp }}/w18-release\n" not in upload
    assert "w18-conpty-layer-matrix.raw.json" not in upload
    assert "w18-conpty-layer-matrix.raw.json" in workflow
    assert "Remove-Item -LiteralPath $raw" in workflow
    assert "gh release create" not in workflow.casefold()
    assert "twine upload" not in workflow.casefold()
    assert "git push" not in workflow.casefold()
    assert "git tag" not in workflow.casefold()


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
