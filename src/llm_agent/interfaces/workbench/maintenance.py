"""Offline lifecycle for the Workbench installed from a local checkout.

This developer installation is separate from the W18 release-bundle installer.
Candidates are immutable; only current.json is promoted after package validation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

from llm_agent.interfaces.workbench._maintenance_install import MaintenanceInstallMixin

PACKAGE_SUFFIXES = {".py", ".json", ".html", ".css", ".js"}
MANAGED_DIRECTORY = ".workbench-managed"


def package_fingerprint(package: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if "__pycache__" in path.parts or path.suffix not in PACKAGE_SUFFIXES or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):
            raise ValueError(f"Arquivo fora do pacote: {path}")
        relative = path.relative_to(package).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big") + relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def source_fingerprint(source: Path) -> str:
    digest = hashlib.sha256(package_fingerprint(source / "src" / "llm_agent").encode())
    for name in ("pyproject.toml", "README.md", "Gerenciar_Workbench.ps1", "Workbench.cmd", "scripts/manage_workbench.py"):
        path = source / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Metadados invalidos: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class LocalInstallation(MaintenanceInstallMixin):
    def __init__(self, source: Path):
        self.source = source.resolve()
        if not (self.source / "pyproject.toml").is_file() or not (self.source / "src/llm_agent").is_dir():
            raise ValueError("Selecione o checkout do LLM Agent Harness.")
        self.root = self.source / MANAGED_DIRECTORY
        if self.root.is_symlink() or (self.root.exists() and self.root.resolve() != self.root):
            raise ValueError("A pasta da instalacao nao pode ser um link.")

    def candidate(self, identifier: str) -> Path:
        if not isinstance(identifier, str) or not identifier or any(c not in "0123456789abcdef-TZ" for c in identifier):
            raise ValueError("Identidade de instalacao invalida.")
        path = self.root / "versions" / identifier
        if not path.resolve().is_relative_to((self.root / "versions").resolve()) or path.is_symlink():
            raise ValueError("Candidato fora da instalacao gerenciada.")
        return path

    def pointer(self) -> dict[str, Any]:
        path = self.root / "current.json"
        pointer = read_json(path) if path.exists() else {}
        if pointer.get("schema", 1) != 1:
            raise ValueError("Formato do apontamento de instalacao invalido.")
        for key in ("current", "previous"):
            identifier = pointer.get(key)
            if identifier is not None and not isinstance(identifier, str):
                raise ValueError("Identidade no apontamento de instalacao invalida.")
        return pointer

    def status(self) -> dict[str, Any]:
        pointer = self.pointer()
        current = pointer.get("current")
        if not current:
            return {"status": "not_installed", "message": "Workbench gerenciado ainda nao instalado."}
        candidate = self.candidate(current)
        metadata = read_json(candidate / "installation.json")
        if (not isinstance(metadata.get("source_fingerprint"), str)
                or not isinstance(metadata.get("package_fingerprint"), str)
                or not isinstance(metadata.get("version"), str)):
            raise ValueError("Metadados da instalacao invalidos.")
        available = metadata["source_fingerprint"] != source_fingerprint(self.source)
        return {"status": "installed", "current": current, "previous": pointer.get("previous"),
                "version": metadata["version"], "update_available": available, "path": str(candidate)}

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(exist_ok=True)
        with (self.root / "maintenance.lock").open("a+b") as stream:
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError("Outra manutencao esta em andamento.") from exc
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _seed(self) -> Path:
        current = self.pointer().get("current")
        if not current and (self.root / "uninstalled.json").is_file():
            current = read_json(self.root / "uninstalled.json").get("current")
        choices = [self.candidate(current) / "runtime"] if current else []
        choices += sorted(self.source.glob(".workbench-installed-*/runtime"), reverse=True)
        choices += [self.source / ".venv"]
        for runtime in choices:
            if (runtime / "Scripts/python.exe").is_file() and (runtime / "Lib/site-packages/webview").is_dir():
                return runtime
        raise RuntimeError("Dependencias desktop ausentes. Nenhum download foi feito. Instale pywebview em um runtime local antes de continuar.")


    def rollback(self) -> dict[str, Any]:
        with self.locked():
            pointer = self.pointer()
            previous = pointer.get("previous")
            if not previous or not (self.candidate(previous) / "installation.json").is_file():
                raise RuntimeError("Nao existe uma versao anterior gerenciada para restaurar.")
            metadata = read_json(self.candidate(previous) / "installation.json")
            if package_fingerprint(self.candidate(previous) / "runtime/Lib/site-packages/llm_agent") != metadata["package_fingerprint"]:
                raise RuntimeError("A versao anterior foi alterada. Restauracao cancelada.")
            write_json(self.root / "current.json", {"schema": 1, "current": previous, "previous": pointer.get("current")})
            return {"status": "restored", "message": "Versao anterior restaurada. Reinicie o Workbench."}

    def uninstall(self) -> dict[str, Any]:
        with self.locked():
            # Deactivate without destroying candidates, history, configuration or workspaces.
            pointer = self.root / "current.json"
            if pointer.exists():
                os.replace(pointer, self.root / "uninstalled.json")
            return {"status": "uninstalled", "message": "Workbench desativado. Versoes, historico e configuracao preservados para recuperacao."}

    def configuration(self) -> Path:
        from llm_agent.application.workbench.installation_config import prepare_installation_configuration

        return cast(Path, prepare_installation_configuration(self.source, self.root / "data/home"))

    def open(self, workspace: Path | None = None, profile: str | None = None) -> dict[str, Any]:
        current = self.pointer().get("current")
        if not current:
            raise RuntimeError("Instale o Workbench pelo gerenciador primeiro.")
        candidate = self.candidate(current)
        metadata = read_json(candidate / "installation.json")
        if package_fingerprint(candidate / "runtime/Lib/site-packages/llm_agent") != metadata["package_fingerprint"]:
            raise RuntimeError("Os arquivos instalados foram alterados. Use Atualizar para preparar uma instalacao valida.")
        home = self.root / "data/home"
        home.mkdir(parents=True, exist_ok=True)
        arguments = [str(candidate / "runtime/Scripts/python.exe"), "-I", "-m", "llm_agent.interfaces.workbench.webview",
                     "--workspace", str((workspace or self.source).resolve()), "--home", str(home)]
        arguments += ["--config", str(self.configuration())]
        if profile:
            arguments += ["--profile", profile]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".log")
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(arguments, cwd=candidate, env=environment, stdout=stream, stderr=stream,
                                       creationflags=creation_flags)
        return {"status": "opened", "pid": process.pid, "log": str(log)}


def wait_for_process(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        raise ValueError("Processo de reinicio invalido.")
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                if kernel.WaitForSingleObject(handle, 60_000) != 0:
                    raise RuntimeError("Feche a janela anterior antes de abrir a nova versao.")
            finally:
                kernel.CloseHandle(handle)
    else:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.2)
        raise RuntimeError("A janela anterior continua aberta.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gerenciador offline do Workbench local")
    parser.add_argument("operation", choices=["install", "update", "open", "status", "rollback", "uninstall"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--wait-pid", type=int)
    args = parser.parse_args(argv)
    try:
        installation = LocalInstallation(args.source)
        if args.wait_pid:
            if args.operation != "open":
                raise ValueError("wait-pid e exclusivo da abertura apos reinicio.")
            wait_for_process(args.wait_pid)
        if args.operation in ("install", "update"):
            result = installation.install()
        elif args.operation == "open":
            result = installation.open(args.workspace, args.profile)
        else:
            result = getattr(installation, args.operation)()
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=True))
        return 1
