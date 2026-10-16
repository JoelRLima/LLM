"""Explicit diagnostic stages; no full suite, model, ConPTY, or other lifecycle."""
from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics.evidence import digest, write_json
from scripts.w23_diagnostics.inputs import TOOL_ROOT, authenticate, contract, reconstruct
from scripts.w23_diagnostics.process import Commands, StageError, environment, require

TARGET = "tests/unit/test_current_architecture.py::test_current_checker_enforces_the_complete_repository_graph"


def dependencies(work: Path, source: Path, commands: Commands) -> None:
    env = environment(work / "private/temp-dependencies", source)
    common = [sys.executable, "-B", "-m", "pip", "install", "--isolated", "--no-input",
              "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:"]
    bootstrap = ["--require-hashes", "--no-deps", "--requirement", str(source / "distribution/bootstrap-pip.lock")]
    require(commands.run("bootstrap", common + bootstrap, source, env, 240))
    require(commands.run("dependencies", common + ["--requirement", str(source / "requirements-ci.lock")], source, env, 240))
    write_json(work / "publish/dependencies.json", {"pytest": importlib.metadata.version("pytest"),
               "requirements_sha256": digest(source / "requirements-ci.lock"),
               "bootstrap_sha256": digest(source / "distribution/bootstrap-pip.lock")})
    if os.environ.get("W23_RUN_DIAGNOSTIC_TESTS") == "true":
        controller_tests(work, commands)


def controller_tests(work: Path, commands: Commands) -> None:
    xml = work / "private/controller-tests.xml"
    env = environment(work / "private/temp-controller-tests", TOOL_ROOT)
    result = commands.run("controller-tests", [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "--basetemp", str(work / "private/pytest-controller-tests"), "--junitxml", str(xml),
                          "tests/unit/test_w23_manual_diagnostics.py"], TOOL_ROOT, env, 120)
    require(result)
    report = junit(xml, 18)
    write_json(work / "publish/controller-tests.json", report)
    if report["status"] != "PASS":
        raise StageError("FAIL")


def clean_host() -> None:
    try:
        importlib.metadata.version("local-llm-agent")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise StageError("FAIL")
    if shutil.which("llm-agent"):
        raise StageError("FAIL")


def r01(work: Path, commands: Commands) -> dict[str, Any]:
    source = reconstruct(work, "source-base", commands)
    try:
        return _r01_stages(work, source, commands)
    finally:
        authenticate(source, work / "publish/source-base-after.json")


def _r01_stages(work: Path, source: Path, commands: Commands) -> dict[str, Any]:
    clean_host()
    dependencies(work, source, commands)
    env_tools = environment(work / "private/temp-build-tools", TOOL_ROOT)
    downloads = work / "private/downloads"
    downloads.mkdir()
    names = {"python": "cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
             "uv": "uv-x86_64-pc-windows-msvc.zip"}
    for asset in ("python", "uv"):
        require(commands.run("download-" + asset, [sys.executable, "-B", "-m", "scripts.w23_diagnostics.download",
                            asset, str(downloads / names[asset])], TOOL_ROOT, env_tools, 240))
        shutil.copyfile((downloads / names[asset]).with_suffix(".identity.json"),
                        work / "publish" / f"{asset}-download.json")
    build = work / "private/release"
    env_source = environment(work / "private/temp-build", source)
    require(commands.run("build", [sys.executable, "-B", "scripts/build_release_artifacts.py",
                        "--output-dir", str(build), "--python", sys.executable,
                        "--python-artifact", str(downloads / names["python"]), "--uv-artifact", str(downloads / names["uv"])],
                         source, env_source, 480))
    manifest = build / "bundle/release-manifest.json"
    actual = digest(manifest)
    write_json(work / "publish/bundle.json", {"manifest_sha256": actual,
               "expected": contract()["release_manifest_sha256"]})
    if actual != contract()["release_manifest_sha256"]:
        raise StageError("FAIL")
    output = work / "publish/r01"
    result = commands.run("r01-clean-install", [sys.executable, "-B", "-u", "-m", "scripts.w23_diagnostics.r01",
                          "--source", str(source), "--bundle", str(build / "bundle"), "--output", str(output),
                          "--private", str(work / "private/r01"), "--seconds", "180"], TOOL_ROOT, env_tools, 360)
    if result.status == "TIMEOUT" or result.code == 124:
        return {"status": "TIMEOUT", "restoration": "UNKNOWN", "scenario": "clean-install"}
    if result.status == "UNKNOWN" or not (output / "result.json").is_file():
        return {"status": "UNKNOWN", "restoration": "UNKNOWN", "scenario": "clean-install"}
    raw = json.loads((output / "result.json").read_text(encoding="utf-8"))
    if raw.get("status") not in {"PASS", "FAIL", "TIMEOUT", "UNKNOWN"}:
        raise StageError("UNKNOWN")
    if raw.get("status") == "PASS" and result.status != "PASS":
        raw["status"] = "UNKNOWN"
    return dict(raw)


def junit(path: Path, expected_count: int) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JUnit exceeds evidence cap")
    cases = list(ET.parse(path).getroot().iter("testcase"))
    invalid = any(any(case.find(tag) is not None for tag in ("error", "failure", "skipped")) for case in cases)
    exact = expected_count != 1 or (len(cases) == 1 and cases[0].get("name") == TARGET.split("::")[1])
    return {"status": "PASS" if len(cases) == expected_count and not invalid and exact else "FAIL",
            "cases": [{"name": case.get("name"), "seconds": case.get("time")} for case in cases]}


def focal(work: Path, source: Path, name: str, commands: Commands, regression: bool = False) -> dict[str, Any]:
    xml = work / "private" / f"{name}.xml"
    selection = "tests/unit/test_current_architecture_performance.py" if regression else TARGET
    env = environment(work / "private" / f"temp-{name}", source)
    result = commands.run(name, [sys.executable, "-B", "-m", "pytest", "-v", "--tb=short",
                          "-p", "no:cacheprovider", "-o", "faulthandler_timeout=60", "--durations=5",
                          "--basetemp", str(work / "private" / f"pytest-{name}"),
                          "--junitxml", str(xml), selection], source, env, 60 if regression else 420)
    row: dict[str, Any] = {"status": result.status}
    if xml.is_file():
        try:
            row["junit"] = junit(xml, 3 if regression else 1)
        except (OSError, ValueError, ET.ParseError) as exc:
            row["junit"] = {"status": "UNKNOWN", "exception_type": type(exc).__name__}
        if result.status == "PASS" and row["junit"]["status"] != "PASS":
            row["status"] = row["junit"]["status"]
    elif result.status == "PASS":
        row["status"] = "UNKNOWN"
    write_json(work / "publish" / f"{name}.json", row)
    return row


def profile(work: Path, source: Path, name: str, commands: Commands) -> dict[str, Any]:
    output = work / "publish" / f"profile-{name}"
    env = environment(work / "private" / f"temp-profile-{name}", source)
    result = commands.run("profile-" + name, [sys.executable, "-B", "-u",
                          str(TOOL_ROOT / "scripts/w23_diagnostics/profile.py"),
                          "--source", str(source), "--output", str(output)], source, env, 420)
    if not (output / "result.json").is_file():
        return {"status": result.status if result.status != "PASS" else "UNKNOWN"}
    try:
        row: dict[str, Any] = json.loads((output / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": result.status if result.status != "PASS" else "UNKNOWN",
                "exception_type": type(exc).__name__}
    if row.get("status") not in {"PASS", "FAIL", "TIMEOUT", "UNKNOWN"}:
        row["status"] = "UNKNOWN"
    row["process_status"] = result.status
    if result.status in {"TIMEOUT", "UNKNOWN"} or (result.status != "PASS" and row.get("status") == "PASS"):
        row["status"] = result.status if result.status != "FAIL" else "UNKNOWN"
    return row


def aggregate(states: list[str]) -> str:
    for state in ("TIMEOUT", "FAIL", "UNKNOWN"):
        if state in states:
            return state
    return "PASS" if states and all(state == "PASS" for state in states) else "UNKNOWN"


def r02(work: Path, commands: Commands) -> dict[str, Any]:
    original = reconstruct(work, "original", commands)
    patched = reconstruct(work, "patched", commands, patched=True)
    dependencies(work, original, commands)  # No dependency mutation between samples.
    samples: dict[str, Any] = {}
    for name, source in (("original", original), ("patched", patched)):
        try:
            samples[name] = {"profile": profile(work, source, name, commands),
                             "focal": focal(work, source, name + "-focal", commands)}
        finally:
            authenticate(source, work / "publish" / f"{name}-after.json", patched=name == "patched")
    samples["patched"]["regressions"] = focal(work, patched, "patched-regressions", commands, regression=True)
    authenticate(patched, work / "publish/patched-final.json", patched=True)
    baseline, optimized = samples["original"]["profile"], samples["patched"]["profile"]
    comparable = baseline.get("status") == optimized.get("status") == "PASS"
    equivalent = comparable and baseline.get("counts") == optimized.get("counts") and baseline.get("errors") == optimized.get("errors")
    states = [row["status"] for variant in samples.values() for row in variant.values()]
    status = aggregate(states + (["PASS" if equivalent else "FAIL"] if comparable else ["UNKNOWN"]))
    return {"status": status, "samples": samples, "semantics_comparable": comparable,
            "repository_outputs_equal": equivalent, "global_suite_timeout_resolved": "UNKNOWN",
            "comparison_scope": "one native sample per tree; inclusive rule times; no statistical speedup claim"}
