"""Explicit local update operations exposed to the native Workbench UI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from llm_agent.interfaces.workbench.maintenance import (
    LocalInstallation,
    package_fingerprint,
    read_json,
    source_fingerprint,
)


class WorkbenchUpdates:
    def __init__(self, runtime: Path | None = None):
        self.runtime = runtime or Path(sys.prefix)

    def _metadata(self) -> dict[str, Any]:
        metadata = read_json(self.runtime.parent / "installation.json")
        if (metadata.get("schema") != 1
                or not isinstance(metadata.get("source_root"), str)
                or not isinstance(metadata.get("candidate"), str)
                or not isinstance(metadata.get("source_fingerprint"), str)
                or not isinstance(metadata.get("package_fingerprint"), str)
                or not isinstance(metadata.get("version"), str)):
            raise ValueError("Identidade da instalacao invalida.")
        source = Path(metadata["source_root"])
        if not source.is_absolute():
            raise ValueError("Origem da instalacao invalida.")
        installation = LocalInstallation(source)
        if installation.candidate(metadata["candidate"]).resolve() != self.runtime.parent.resolve():
            raise ValueError("A identidade nao corresponde ao runtime em execucao.")
        return metadata

    def status(self) -> dict[str, Any]:
        if not (self.runtime.parent / "installation.json").exists():
            return {"state": "unmanaged", "available": False, "restart_required": False,
                    "message": "Abra Workbench.cmd e escolha Gerenciar instalacao para instalar esta interface com atualizacoes locais."}
        try:
            metadata = self._metadata()
            source = Path(metadata["source_root"])
            pointer = LocalInstallation(source).pointer()
            current = pointer.get("current")
            if current is not None and not isinstance(current, str):
                raise ValueError("Identidade da versao atual invalida.")
            restart = bool(current and current != metadata["candidate"])
            available = metadata["source_fingerprint"] != source_fingerprint(source)
            intact = package_fingerprint(self.runtime / "Lib/site-packages/llm_agent") == metadata["package_fingerprint"]
            available = available or not intact
            if restart and isinstance(current, str):
                target = read_json(LocalInstallation(source).candidate(current) / "installation.json")
                target_fingerprint = target.get("source_fingerprint")
                if not isinstance(target_fingerprint, str):
                    raise ValueError("Metadados da versao atual invalidos.")
                available = target_fingerprint != source_fingerprint(source)
            return {"state": "managed" if current else "uninstalled", "available": available,
                    "restart_required": restart, "version": metadata["version"],
                    "revision": metadata["source_fingerprint"][:12],
                    "message": "Atualizacao local disponivel." if available else "A instalacao corresponde ao codigo local."}
        except (OSError, ValueError, KeyError) as exc:
            return {"state": "error", "available": False, "restart_required": False, "message": str(exc)}

    def apply(self) -> dict[str, Any]:
        metadata = self._metadata()
        source = Path(metadata["source_root"])
        manager = source / "Gerenciar_Workbench.ps1"
        if not manager.is_file() or manager.is_symlink():
            raise RuntimeError("Gerenciador local indisponivel.")
        if not LocalInstallation(source).pointer().get("current"):
            raise RuntimeError("Instalacao desativada. Use o gerenciador para reinstalar.")
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(manager), "-Operation", "Update"],
            cwd=source, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
            creationflags=creation_flags,
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError((result.stderr or result.stdout or "Falha ao executar o gerenciador.")[-2000:]) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("O gerenciador retornou uma resposta invalida.")
        status = payload.get("status")
        message = payload.get("message")
        if not isinstance(status, str) or not isinstance(message, str) or not message.strip():
            raise RuntimeError("O gerenciador retornou uma resposta incompleta.")
        if result.returncode or status not in ("installed", "unchanged"):
            raise RuntimeError(message or "Atualizacao nao foi instalada.")
        return {"accepted": True, "message": message, **self.status()}

    def restart(self, workspace: Path) -> None:
        metadata = self._metadata()
        source = Path(metadata["source_root"])
        if not LocalInstallation(source).pointer().get("current"):
            raise RuntimeError("Instalacao desativada. Use o gerenciador para reinstalar.")
        command = [str(self.runtime / "Scripts/python.exe"), "-I", str(source / "scripts/manage_workbench.py"),
                   "open", "--source", str(source), "--workspace", str(workspace), "--wait-pid", str(os.getpid())]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        logs = source / ".workbench-managed/logs"
        logs.mkdir(exist_ok=True)
        with (logs / "restart.log").open("a", encoding="utf-8") as stream:
            subprocess.Popen(command, cwd=source, stdout=stream, stderr=stream, creationflags=flags)
