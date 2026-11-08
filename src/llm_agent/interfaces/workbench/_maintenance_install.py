"""Offline candidate preparation and package verification for Workbench."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _already_current(self: Any, before: dict[str, Any], fingerprint: str, package_fingerprint: Any, read_json: Any) -> bool:
    current_id = before.get("current")
    if not current_id:
        return False
    current = read_json(self.candidate(current_id) / "installation.json")
    package = self.candidate(current_id) / "runtime/Lib/site-packages/llm_agent"
    return bool(
        current["source_fingerprint"] == fingerprint
        and package_fingerprint(package) == current["package_fingerprint"]
    )


def _prepare_candidate(self: Any, candidate: Path, seed: Path) -> tuple[Path, Path, Path, Path, dict[str, str], Path]:
    build = candidate / "build-source"
    build.mkdir()
    shutil.copytree(self.source / "src", build / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(self.source / name, build / name)
    runtime = candidate / "runtime"
    shutil.copytree(seed, runtime, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    python = runtime / "Scripts/python.exe"
    builder = self.source / ".venv/Scripts/python.exe"
    if not builder.is_file():
        builder = python
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        environment.pop(key, None)
    temp = candidate / "temp"
    temp.mkdir()
    environment.update(
        TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), PYTHONDONTWRITEBYTECODE="1",
        PIP_NO_INDEX="1", PIP_CONFIG_FILE=os.devnull,
    )
    return build, runtime, builder, python, environment, candidate / "maintenance.log"


def _run_install_command(command: list[str], build: Path, environment: dict[str, str], log: Path) -> str:
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = subprocess.run(
        command,
        cwd=build,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        creationflags=creation_flags,
    )
    with log.open("a", encoding="utf-8") as stream:
        stream.write(result.stdout + result.stderr + "\n")
    if result.returncode:
        detail = (result.stderr or result.stdout)[-1500:]
        raise RuntimeError(f"A instalacao anterior foi preservada. Consulte {log}\n{detail}")
    return result.stdout.strip()


def _remove_seed_package(site: Path, candidate: Path) -> None:
    targets = [site / "llm_agent", *site.glob("local_llm_agent-*.dist-info")]
    for path in targets:
        if not path.exists():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(candidate.resolve()):
            raise ValueError("Destino de instalacao fora do candidato.")
        shutil.rmtree(path)


def _build_candidate(
    self: Any,
    candidate: Path,
    seed: Path,
    fingerprint: str,
    before: dict[str, Any],
    package_fingerprint: Any,
    source_fingerprint: Any,
    write_json: Any,
) -> dict[str, Any]:
    build, runtime, builder, python, environment, log = _prepare_candidate(self, candidate, seed)
    wheels = candidate / "wheels"
    wheels.mkdir()
    _run_install_command(
        [str(builder), "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(wheels), str(build)],
        build, environment, log,
    )
    wheel_files = list(wheels.glob("local_llm_agent-*.whl"))
    if len(wheel_files) != 1:
        raise RuntimeError("O build nao produziu exatamente um wheel da aplicacao.")
    site = runtime / "Lib/site-packages"
    _remove_seed_package(site, candidate)
    _run_install_command(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel_files[0])],
        build, environment, log,
    )
    _run_install_command([str(python), "-m", "pip", "check"], build, environment, log)
    probe = (
        "import importlib.metadata as m, pathlib, sys; import webview, llm_agent; "
        "from llm_agent.interfaces.workbench.webview import WorkbenchBridge; "
        "expected = pathlib.Path(sys.argv[1]).resolve(); "
        "assert pathlib.Path(sys.prefix).resolve() == expected; "
        "assert pathlib.Path(llm_agent.__file__).resolve().parent == expected / 'Lib/site-packages/llm_agent'; "
        "print(m.version('local-llm-agent'))"
    )
    version = _run_install_command([str(python), "-I", "-c", probe, str(runtime)], build, environment, log)
    if package_fingerprint(build / "src/llm_agent") != package_fingerprint(site / "llm_agent"):
        raise RuntimeError("O pacote instalado difere do codigo preparado; promocao cancelada.")
    if fingerprint != source_fingerprint(self.source):
        raise RuntimeError("O codigo mudou durante a instalacao. Execute Atualizar novamente.")
    self.configuration()
    write_json(
        candidate / "installation.json",
        {
            "schema": 1,
            "source_root": str(self.source),
            "source_fingerprint": fingerprint,
            "package_fingerprint": package_fingerprint(site / "llm_agent"),
            "version": version,
            "candidate": candidate.name,
            "wheel_sha256": hashlib.sha256(wheel_files[0].read_bytes()).hexdigest(),
        },
    )
    write_json(self.root / "current.json", {
        "schema": 1,
        "current": candidate.name,
        "previous": before.get("current"),
    })
    return {
        "status": "installed",
        "current": candidate.name,
        "version": version,
        "message": "Instalacao validada. Abra ou reinicie o Workbench.",
    }


class MaintenanceInstallMixin:
    def install(self: Any) -> dict[str, Any]:
        from llm_agent.interfaces.workbench.maintenance import (
            package_fingerprint,
            read_json,
            source_fingerprint,
            write_json,
        )
        with self.locked():
            before = self.pointer()
            fingerprint = source_fingerprint(self.source)
            if _already_current(self, before, fingerprint, package_fingerprint, read_json):
                return {"status": "unchanged", "message": "A instalacao ja corresponde ao codigo local."}
            seed = self._seed()
            identifier = (
                fingerprint[:12]
                + "-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            )
            candidate = self.candidate(identifier)
            candidate.mkdir(parents=True)
            return _build_candidate(
                self,
                candidate,
                seed,
                fingerprint,
                before,
                package_fingerprint,
                source_fingerprint,
                write_json,
            )
