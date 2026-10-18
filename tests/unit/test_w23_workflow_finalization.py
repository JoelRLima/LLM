"""Local finalizer regressions; no diagnostics, lifecycle, or remote Actions run."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / ".github/workflows/wave18-installed-product.yml"
TEMPLATE = ROOT / "quality/w23/diagnostics/manual-only.workflow.yml"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def finalizer(path: Path) -> str:
    block = path.read_text(encoding="utf-8").split(
        "      - name: Finalize bounded public evidence even after failure\n", 1
    )[1].split("      - name: Upload only redacted diagnostic evidence\n", 1)[0]
    return textwrap.dedent(block.split("        run: |\n", 1)[1]).strip() + "\n"


def test_finalizers_match_and_upload_requires_validation() -> None:
    assert finalizer(MAIN) == finalizer(TEMPLATE)
    for path in (MAIN, TEMPLATE):
        source = path.read_text(encoding="utf-8")
        job_env = source.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
        assert "runner.temp" not in job_env
        assert "W23_DIAG_WORK:" not in job_env
        assert "run: ./scripts/w23_diagnostics/secure-work.ps1" in source
        assert "if: always() && steps.finalize.outputs.ready == 'true'" in source
        assert "path: ${{ steps.finalize.outputs.upload }}" in source


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("mode", ["", "normal", "r01", "r02"])
def test_job_selection_is_exclusive(event: str, mode: str) -> None:
    source = MAIN.read_text(encoding="utf-8")
    assert ("github.event_name != 'workflow_dispatch' || inputs.diagnostic_mode == '' || "
            "inputs.diagnostic_mode == 'normal'") in source
    assert ("github.event_name == 'workflow_dispatch' && inputs.diagnostic_mode != '' && "
            "inputs.diagnostic_mode != 'normal'") in source
    normal = event != "workflow_dispatch" or mode in ("", "normal")
    manual = event == "workflow_dispatch" and mode not in ("", "normal")
    assert normal != manual
    assert manual == (event == "workflow_dispatch" and mode in ("r01", "r02"))


# This function replaces command resolution for python in the PowerShell child.
# It writes only fixture evidence, never imports or runs diagnostic code.
EXPORT_STUB = r"""
function python {
  $env:STUB_CALLED = 'true'
  'called' | Set-Content -LiteralPath $env:STUB_MARKER
  if ($env:STUB_CASE -eq 'throw') { throw 'Injected unavailable Python' }
  $target = Join-Path $env:W23_DIAG_WORK 'upload'
  New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
  $state = if ($env:STUB_CASE -in @('FAIL','TIMEOUT','UNKNOWN')) { $env:STUB_CASE } else { 'PASS' }
  $completed = if ($env:STUB_CASE -eq 'incomplete') { $false } else { $true }
  @{status=$state; completed=$completed; w23='BLOCKED'} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $target 'summary.json') -Encoding utf8
  if ($env:STUB_CASE -eq 'partial') { $global:LASTEXITCODE = 2; return }
  $hash = (Get-FileHash -LiteralPath (Join-Path $target 'summary.json') -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($env:STUB_CASE -eq 'bad-hash') { $hash = '0' * 64 }
  @{'summary.json'=$hash} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'SHA256.json') -Encoding utf8
  if ($env:STUB_CASE -eq 'extra') { 'unlisted' | Set-Content -LiteralPath (Join-Path $target 'extra.log') }
  $global:LASTEXITCODE = if ($state -ne 'PASS' -or $env:STUB_CASE -eq 'exit-one') { 1 } else { 0 }
}
"""


def fixture_environment(tmp_path: Path, case: str) -> dict[str, str]:
    profile, workspace, runner_temp = (tmp_path / name for name in ("profile", "workspace", "runner-temp"))
    for path in (profile, workspace, runner_temp):
        path.mkdir()
    work = profile / "w23-manual-123-1"
    work.mkdir()
    sentinel = work / "private-sentinel.txt"
    sentinel.write_bytes(b"fixture must remain untouched")
    tooling = workspace / "tooling/scripts/w23_diagnostics"
    if case not in ("pre-checkout", "no-tooling"):
        tooling.mkdir(parents=True)
        (tooling / "entry.py").write_bytes(b"# never executed\n")
    if case == "preexisting":
        (work / "upload").mkdir()
        (work / "upload/SHA256.json").write_bytes(b"{}")
    output = tmp_path / "github-output"
    env = dict(os.environ)
    for name in ("GITHUB_ACTIONS", "W23_DIAG_WORK", "GITHUB_ENV"):
        env.pop(name, None)
    env.update(
        LOCALAPPDATA=str(profile), GITHUB_WORKSPACE=str(workspace), RUNNER_TEMP=str(runner_temp),
        GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1", W23_DIAG_MODE="r01", W23_RUN_OUTCOME="success",
        GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(tmp_path / "step-summary"),
        STUB_CASE=case, STUB_MARKER=str(tmp_path / "stub-called"),
    )
    configure_failure(env, case, work, workspace, runner_temp, tmp_path)
    return env


def configure_failure(
    env: dict[str, str], case: str, work: Path, workspace: Path, runner_temp: Path, tmp_path: Path
) -> None:
    if case not in ("pre-checkout", "pre-initialize", "unsafe-temp", "missing-temp", "root-temp", "junction-temp"):
        env["W23_DIAG_WORK"] = str(work)
    if case == "unexpected-work":
        env["W23_DIAG_WORK"] = str(workspace)
    if case == "step-failure":
        env["W23_RUN_OUTCOME"] = "failure"
    if case == "unsafe-temp":
        env["RUNNER_TEMP"] = str(workspace)
    if case == "missing-temp":
        env.pop("RUNNER_TEMP")
    if case == "root-temp":
        env["RUNNER_TEMP"] = workspace.anchor
    if case == "junction-temp":
        env["RUNNER_TEMP"] = str(tmp_path / "runner-junction")
        env["STUB_TARGET"] = str(runner_temp)


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="Native Windows PowerShell fixture required")
@pytest.mark.parametrize(
    ("case", "expected_status", "fallback", "code"),
    [
        ("pre-checkout", "UNKNOWN", True, 1),
        ("pre-initialize", "UNKNOWN", True, 1),
        ("no-tooling", "UNKNOWN", True, 1),
        ("throw", "UNKNOWN", True, 1),
        ("partial", "UNKNOWN", True, 1),
        ("bad-hash", "UNKNOWN", True, 1),
        ("extra", "UNKNOWN", True, 1),
        ("preexisting", "UNKNOWN", True, 1),
        ("unexpected-work", "UNKNOWN", True, 1),
        ("incomplete", "UNKNOWN", True, 1),
        ("exit-one", "UNKNOWN", True, 1),
        ("step-failure", "UNKNOWN", True, 1),
        ("PASS", "PASS", False, 0),
        ("FAIL", "FAIL", False, 1),
        ("TIMEOUT", "TIMEOUT", False, 1),
        ("UNKNOWN", "UNKNOWN", False, 1),
        ("unsafe-temp", None, False, 1),
        ("missing-temp", None, False, 1),
        ("root-temp", None, False, 1),
        ("junction-temp", None, False, 1),
    ],
)
def test_exact_finalizer_in_native_shell(
    tmp_path: Path, case: str, expected_status: str | None, fallback: bool, code: int
) -> None:
    env = fixture_environment(tmp_path, case)
    work = Path(env["LOCALAPPDATA"]) / "w23-manual-123-1"
    sentinel = work / "private-sentinel.txt"
    before = sentinel.read_bytes()
    output = Path(env["GITHUB_OUTPUT"])
    script = tmp_path / "fixture.ps1"
    prefix = ""
    if case == "junction-temp":
        prefix = ("New-Item -ItemType Junction -Path $env:RUNNER_TEMP -Target $env:STUB_TARGET "
                  "-ErrorAction Stop | Out-Null\n")
    script.write_text(prefix + EXPORT_STUB + finalizer(MAIN), encoding="utf-8")
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script.read_text(encoding="utf-8")],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert sentinel.read_bytes() == before
    if expected_status is None:
        assert not output.exists()
        assert not list(Path(env["GITHUB_WORKSPACE"]).glob("w23-fallback-*"))
        if case in ("root-temp", "unsafe-temp"):
            assert "Unexpected fallback parent" in result.stderr
        if case == "junction-temp":
            assert Path(env["RUNNER_TEMP"]).is_dir()
            assert Path(env["RUNNER_TEMP"]).lstat().st_file_attributes & 0x400
            assert "Reparse path rejected" in result.stderr
            assert not list(Path(env["STUB_TARGET"]).iterdir())
        return
    assert output.exists(), result.stdout + result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8-sig").splitlines())
    assert outputs["ready"] == "true"
    upload = Path(outputs["upload"])
    assert upload.parent == (Path(env["RUNNER_TEMP"]) if fallback else work)
    summary = json.loads((upload / "summary.json").read_text(encoding="utf-8-sig"))
    assert summary["status"] == expected_status
    hashes = json.loads((upload / "SHA256.json").read_text(encoding="utf-8-sig"))
    assert hashes == {"summary.json": hashlib.sha256((upload / "summary.json").read_bytes()).hexdigest()}
    if case in ("preexisting", "unexpected-work", "no-tooling", "pre-checkout", "pre-initialize"):
        assert not (tmp_path / "stub-called").exists()
    if case == "preexisting":
        assert (work / "upload/SHA256.json").read_bytes() == b"{}"
    if case == "partial":
        assert (work / "upload/summary.json").exists()
        assert not (work / "upload/SHA256.json").exists()
