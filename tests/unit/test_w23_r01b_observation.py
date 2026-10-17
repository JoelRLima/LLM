"""Authorized local Windows synthetic children only; never run an installer/lifecycle."""
from __future__ import annotations

import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.w23_diagnostics import capture, inputs, metrics, milestones, observation, phases, process, r01
from scripts.w23_diagnostics.evidence import Redactor, digest, write_json
from scripts.w23_diagnostics.native import OwnedJob
from scripts.w23_diagnostics.process import Commands, StageError, environment

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Native Windows synthetic fixture required")
CHILD = """
import pathlib, sys, time
mode, seconds, target = sys.argv[1], float(sys.argv[2]), pathlib.Path(sys.argv[3])
started = time.monotonic()
while time.monotonic() - started < seconds:
    if mode == 'progress':
        sum(i*i for i in range(10000))
        with target.open('ab') as stream:
            stream.write(b'x' * 4096)
    if mode == 'output':
        print('synthetic stdout marker', flush=True)
    time.sleep(0.02)
sys.exit(3 if mode == 'error' else 0)
"""


def sampler_fake(value: Any = None, error: Exception | None = None) -> Any:
    def sample(job: Any) -> Any:
        if error is not None:
            raise error
        return value if value is not None else {"job": {}, "processes": []}
    return SimpleNamespace(sample=sample)


def test_base_and_contract_remain_frozen() -> None:
    spec = inputs.contract()
    changed = [name for name, expected in spec["files"].items() if digest(ROOT / name) != expected]
    assert len(spec["files"]) == 1635
    assert changed == [".github/workflows/wave18-installed-product.yml"]


def test_paths_and_journal_export_only_enum(tmp_path: Path) -> None:
    root = tmp_path / "install"
    monitor = milestones.Milestones(root, root)
    assert monitor.sample()["journal_state"] == "ABSENT"
    write_json(root / "transactions/current.journal.json", {"state": "STAGED", "private": "synthetic-private-marker"})
    measured = monitor.sample()
    assert measured["journal_state"] == "STAGED"
    assert "synthetic-private-marker" not in json.dumps(measured)
    assert measured["interpretation"].endswith("phase UNKNOWN")
    with pytest.raises(ValueError, match="outside"):
        milestones.safe_path(root, root / "../elsewhere")
    with pytest.raises(ValueError, match="root"):
        milestones.Milestones(root, tmp_path / "other")


@pytest.mark.parametrize("content", [b"x" * 65537, b'{"state":"PRIVATE_UNKNOWN_VALUE"}', b"not json"],
                         ids=["oversize", "unknown-state", "malformed-json"])
def test_journal_invalid_or_capped_is_not_accepted(tmp_path: Path, content: bytes) -> None:
    root = tmp_path / "install"
    journal = root / "transactions/current.journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(content)
    with pytest.raises(ValueError):
        milestones.Milestones(root, root).sample()


def test_unavailable_sample_is_unknown_without_exception_text(tmp_path: Path) -> None:
    monitor = observation.Observation(tmp_path, Redactor([tmp_path]))
    monitor.sampler = sampler_fake(error=PermissionError("synthetic-private-exception-marker"))
    monitor.sample(None, SimpleNamespace(pid=123))  # type: ignore[arg-type]
    assert monitor.failed
    row = json.loads(monitor.path.read_text())
    assert row["collection_error_type"] == "PermissionError"
    assert "synthetic-private-exception-marker" not in monitor.path.read_text()
    assert monitor.summary()["status"] == "UNKNOWN"


def test_partial_samples_keep_measured_counters(tmp_path: Path) -> None:
    monitor = observation.Observation(tmp_path, Redactor())
    monitor.sampler = sampler_fake({"job": {"user_seconds": 1.5}, "processes": [],
                                   "unavailable": [{"pid_snapshot": 321, "error_type": "OSError"}]})
    monitor.sample(None, SimpleNamespace(pid=123))  # type: ignore[arg-type]
    assert monitor.failed
    assert monitor.last["status"] == "UNKNOWN"
    assert monitor.last["counters"]["job"]["user_seconds"] == 1.5
    assert monitor.last["counters"]["unavailable"][0]["pid_snapshot"] == 321


def test_namespace_failure_keeps_cpu_measurements(tmp_path: Path) -> None:
    monitor = observation.Observation(tmp_path, Redactor())
    monitor.sampler = sampler_fake({"job": {"user_seconds": 1.5}})
    monitor.milestones = SimpleNamespace(sample=lambda: (_ for _ in ()).throw(PermissionError()))
    monitor.sample(None, SimpleNamespace(pid=123))  # type: ignore[arg-type]
    assert monitor.failed and monitor.last["counters"]["job"]["user_seconds"] == 1.5


@pytest.mark.parametrize("cap", ["MAX_BYTES", "MAX_SAMPLES"])
def test_evidence_caps_fail_closed(tmp_path: Path, monkeypatch: Any, cap: str) -> None:
    monitor = observation.Observation(tmp_path, Redactor())
    monitor.sampler = sampler_fake()
    monkeypatch.setattr(observation, cap, 0)
    with pytest.raises(ValueError, match="cap"):
        monitor.sample(None, SimpleNamespace(pid=123))  # type: ignore[arg-type]
    assert monitor.summary()["status"] == "UNKNOWN"
    assert monitor.path.stat().st_size == 0


def test_slow_collection_is_not_pass(tmp_path: Path, monkeypatch: Any) -> None:
    monitor = observation.Observation(tmp_path, Redactor())
    monitor.sampler = sampler_fake()
    monkeypatch.setattr(observation, "MAX_SAMPLE_SECONDS", -1)
    monitor.sample(None, SimpleNamespace(pid=123))  # type: ignore[arg-type]
    assert monitor.failed
    assert monitor.last["collection_error_type"] == "ObservationBudgetExceeded"


def test_observation_does_not_reuse_files(tmp_path: Path) -> None:
    observation.Observation(tmp_path, Redactor())
    with pytest.raises(FileExistsError):
        observation.Observation(tmp_path, Redactor())


def test_pid_absent_from_job_is_never_opened() -> None:
    sampler = object.__new__(metrics.ProcessSampler)
    sampler.kernel = SimpleNamespace(OpenProcess=lambda *args: pytest.fail("unowned PID opened"))
    with pytest.raises(ValueError, match="owned snapshot"):
        sampler.one(SimpleNamespace(pids=lambda: []), os.getpid())


def test_bundle_hash_and_verifier_hash_reject_tampering(tmp_path: Path, monkeypatch: Any) -> None:
    write_json(tmp_path / "release-manifest.json", {})
    (tmp_path / "install.ps1").write_bytes(b"# fixture; never executed\n")
    verifier = tmp_path / "scripts/verify_installed_product.py"
    verifier.parent.mkdir()
    verifier.write_bytes(b"raise RuntimeError('must not import')\n")
    spec = {"release_manifest_sha256": digest(tmp_path / "release-manifest.json"),
            "files": {"installer/install.ps1": digest(tmp_path / "install.ps1"),
                      "scripts/verify_installed_product.py": "0" * 64}}
    monkeypatch.setattr(r01, "contract", lambda: spec)
    scenario = object.__new__(r01.Scenario)
    assert scenario.bundle_identity(tmp_path)["install.ps1"] == digest(tmp_path / "install.ps1")
    (tmp_path / "install.ps1").write_bytes(b"# modified\n")
    with pytest.raises(ValueError, match="unauthenticated"):
        scenario.bundle_identity(tmp_path)
    with pytest.raises(ValueError, match="Verifier hash"):
        r01.load_verifier(tmp_path)


def test_invalid_installer_invocation_never_starts(tmp_path: Path) -> None:
    module = SimpleNamespace(ROOT=tmp_path, _powershell_path=lambda: "powershell.exe")
    scenario = r01.Scenario(module, tmp_path / "out", tmp_path / "private", 30)
    scenario.context = SimpleNamespace(install_script=tmp_path / "install.ps1")
    with pytest.raises(ValueError, match="invocation"):
        scenario.command(["python.exe", str(scenario.context.install_script)], cwd=tmp_path, environment={})


@pytest.mark.parametrize("remaining", [500, 600])
def test_scenario_reserves_global_budget(tmp_path: Path, remaining: int) -> None:
    commands = SimpleNamespace(deadline=time.monotonic() + remaining)
    if remaining < 510:
        with pytest.raises(StageError, match="UNKNOWN"):
            phases.r01_budget(tmp_path, commands)
    else:
        phases.r01_budget(tmp_path, commands)
    value = json.loads((tmp_path / "publish/r01b-budget.json").read_text())
    assert value["scenario_seconds"] + value["restoration_seconds"] + 60 == value["outer_seconds"] == 480


def synthetic(
    root: Path, monkeypatch: Any, mode: str, seconds: float, limit: int, observed: bool = True
) -> tuple[Any, observation.Observation | None, float]:
    root.mkdir()
    script = root / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    output = root / "evidence"
    redactor = Redactor([root])
    commands = Commands(output, redactor)
    monitor = observation.Observation(output, redactor) if observed else None
    monkeypatch.setattr(process, "INTERVAL", 0.25)  # Stress at 60x the production frequency.
    env = environment(root / "temp", ROOT)
    started = time.monotonic()
    result = commands.run("synthetic", [sys.executable, "-B", "-u", str(script), mode, str(seconds), str(root / "growth")],
                          root, env, limit, observation=monitor)
    return result, monitor, time.monotonic() - started


@WINDOWS
@pytest.mark.parametrize(("mode", "duration", "limit", "status"), [
    ("progress", 1.2, 5, "PASS"), ("blocked", 1.2, 5, "PASS"),
    ("error", 0.2, 5, "FAIL"), ("blocked", 3, 1, "TIMEOUT"),
])
def test_native_synthetic_results(tmp_path: Path, monkeypatch: Any, mode: str, duration: float, limit: int, status: str) -> None:
    result, monitor, _wall = synthetic(tmp_path / "run", monkeypatch, mode, duration, limit)
    assert result.status == status
    assert monitor is not None and not monitor.failed
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    assert rows and all(row["status"] == "PASS" for row in rows)
    assert all(p["ownership"] == "IsProcessInJob" for row in rows for p in row["counters"]["processes"])
    if mode == "progress":
        assert len(rows) >= 3
        assert rows[-1]["counters"]["job"]["io"]["write_bytes"] > rows[0]["counters"]["job"]["io"]["write_bytes"]
        assert rows[-1]["counters"]["job"]["user_seconds"] > rows[0]["counters"]["job"]["user_seconds"]


@WINDOWS
def test_native_win64_layout_and_foreign_pid_rejection() -> None:
    assert ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(metrics.IoCounters) == 48
    assert ctypes.sizeof(metrics.MemoryCounters) == 80
    assert ctypes.sizeof(metrics.JobAccounting) == 96
    sampler, job = metrics.ProcessSampler(), OwnedJob()
    try:
        # Deliberately corrupt the snapshot: the handle membership check must still reject it.
        job.pids = lambda: [os.getpid()]  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="this Job Object"):
            sampler.one(job, os.getpid())
        assert sampler.kernel.OpenProcess.restype == ctypes.wintypes.HANDLE
        assert sampler.kernel.CloseHandle.argtypes == [ctypes.wintypes.HANDLE]
    finally:
        job.close()


@WINDOWS
def test_native_cancel_closes_only_owned_job(tmp_path: Path, monkeypatch: Any) -> None:
    def cancel(*args: Any) -> None:
        raise KeyboardInterrupt
    monkeypatch.setattr(observation.Observation, "sample", cancel)
    result, _monitor, wall = synthetic(tmp_path / "run", monkeypatch, "blocked", 10, 5)
    assert result.status == "UNKNOWN" and wall < 5
    record = json.loads((tmp_path / "run/evidence/001-synthetic.json").read_text())
    assert record["exception_type"] == "KeyboardInterrupt"


@WINDOWS
def test_native_capture_failure_cannot_pass(tmp_path: Path, monkeypatch: Any) -> None:
    def fail(*args: Any) -> None:
        raise OSError("injected capture failure")
    monkeypatch.setattr(capture.Capture, "_write", fail)
    result, _monitor, _wall = synthetic(tmp_path / "run", monkeypatch, "output", 2, 5)
    assert result.status == "UNKNOWN"


@WINDOWS
def test_native_public_logs_do_not_export_environment(tmp_path: Path, monkeypatch: Any) -> None:
    marker = "fixture-" + os.urandom(16).hex()
    monkeypatch.setenv("W23_R01B_SECRET", marker)
    result, monitor, _wall = synthetic(tmp_path / "run", monkeypatch, "progress", 0.5, 5)
    assert result.status == "PASS" and monitor is not None
    for path in (tmp_path / "run/evidence").iterdir():
        assert marker not in path.read_text()
        assert "W23_R01B_SECRET" not in path.read_text()


@WINDOWS
def test_native_inaccessible_collection_does_not_promote_zero_exit(tmp_path: Path, monkeypatch: Any) -> None:
    def fail(*args: Any) -> Any:
        raise PermissionError("synthetic-query-error")
    monkeypatch.setattr(metrics.ProcessSampler, "sample", fail)
    result, monitor, _wall = synthetic(tmp_path / "run", monkeypatch, "progress", 0.5, 5)
    assert result.code == 0 and result.status == "UNKNOWN"
    assert monitor is not None and monitor.failed


@WINDOWS
def test_native_fatal_checkpoint_never_claims_restoration(tmp_path: Path) -> None:
    driver = (
        "from pathlib import Path; from types import SimpleNamespace; import sys; "
        "from scripts.w23_diagnostics.r01 import Scenario; "
        "root=Path(sys.argv[1]); "
        "s=Scenario(SimpleNamespace(ROOT=root),root/'out',root/'private',30); "
        "s.active=['synthetic_operation']; s.fatal('TIMEOUT',124)"
    )
    commands = Commands(tmp_path / "evidence", Redactor([tmp_path]))
    result = commands.run("synthetic-fatal", [sys.executable, "-B", "-c", driver, str(tmp_path)], tmp_path,
                          environment(tmp_path / "temp", ROOT), 5)
    assert result.code == 124
    checkpoint = json.loads((tmp_path / "out/result.json").read_text())
    assert checkpoint["status"] == "TIMEOUT" and checkpoint["restoration"] == "UNKNOWN"
    assert checkpoint["fatal"] and checkpoint["active"] == ["synthetic_operation"]


@WINDOWS
def test_native_junction_observation_is_rejected(tmp_path: Path) -> None:
    target, junction = tmp_path / "target", tmp_path / "link"
    target.mkdir()
    env = dict(os.environ, W23_FIXTURE_TARGET=str(target), W23_FIXTURE_LINK=str(junction))
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                             "New-Item -ItemType Junction -Path $env:W23_FIXTURE_LINK -Target $env:W23_FIXTURE_TARGET -ErrorAction Stop | Out-Null"],
                            env=env, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0
    with pytest.raises(ValueError, match="Reparse"):
        milestones.Milestones(junction, junction)
    assert list(target.iterdir()) == []


@WINDOWS
def test_native_overhead_predeclared_tolerance(tmp_path: Path, monkeypatch: Any) -> None:
    baseline, observed, cpu = [], [], []
    callbacks = []
    original = observation.Observation.sample
    def timed(self: Any, job: Any, child: Any) -> None:
        started = time.perf_counter()
        try:
            original(self, job, child)
        finally:
            callbacks.append(time.perf_counter() - started)
    monkeypatch.setattr(observation.Observation, "sample", timed)
    for pair in range(3):
        for enabled in ((False, True) if pair % 2 == 0 else (True, False)):
            started_cpu = time.process_time()
            result, monitor, wall = synthetic(tmp_path / f"pair-{pair}-{enabled}", monkeypatch, "progress", 2, 5, enabled)
            cpu.append(time.process_time() - started_cpu)
            assert result.status == "PASS"
            (observed if enabled else baseline).append(wall)
            if monitor is not None:
                assert not monitor.failed
    base, measured = statistics.median(baseline), statistics.median(observed)
    report = {"baseline_seconds": baseline, "observed_seconds": observed, "driver_cpu_seconds": cpu,
              "median_baseline": base, "median_observed": measured, "tolerance": max(base * 0.2, 0.2),
              "fixture_interval": 0.25, "production_interval": 15, "collector_callback_wall_seconds": callbacks}
    write_json(tmp_path / "overhead.json", report)
    print(json.dumps(report, sort_keys=True))
    assert measured - base <= max(base * 0.2, 0.2)
