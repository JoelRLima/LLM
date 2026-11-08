"""UI-neutral test-queue access through the existing interactive worker owner."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

from llm_agent.application.test_queue_library import TestQueueLibrary, queue_configuration_options
from llm_agent.application.test_queue_service import QueueExecutionRequest


class SessionTestQueues:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.library = TestQueueLibrary(session.start.app_paths)
        self.selected: Any = None
        self.queue_id: str | None = None
        self.running = False
        self.pause_requested = Event()
        self.reviewed_identity: str | None = None

    def fence(self, workspace_id: str, queue_id: str | None = None, fingerprint: str | None = None) -> None:
        if self.session._closed or workspace_id != self.session.workspace.workspace_id:
            raise ValueError("Projeto alterado ou sessao encerrada. Reabra Testes.")
        if queue_id is not None and (queue_id != self.queue_id or self.selected is None):
            raise ValueError("Fila alterada. Atualize a selecao.")
        if fingerprint is not None and self.selected.inspect()["manifest_hash"] != fingerprint:
            raise ValueError("Manifesto de testes alterado. Reabra a fila.")

    def configuration(self) -> dict[str, Any]:
        start = self.session.start
        return queue_configuration_options(start.app_paths, start.config_path, start.profile)

    def state(self, workspace_id: str) -> dict[str, Any]:
        self.fence(workspace_id)
        recent = self.library.recent()
        if self.selected is None and recent:
            self.queue_id = recent[0]["id"]
            self.selected = self.library.resolve(self.queue_id)
        config: dict[str, Any]
        try:
            config = self.configuration()
        except (OSError, RuntimeError, ValueError) as exc:
            config = {"profiles": [], "selected_profile": None, "configuration_error": str(exc)[:1000]}
        result = {"workspace_id": workspace_id, "queue_id": self.queue_id, "recent": recent,
                  "run_active": self.running, "pause_requested": self.running and self.pause_requested.is_set(), **config}
        try:
            result["queue"] = self.selected.status() if self.selected is not None else None
        except (OSError, RuntimeError, ValueError) as exc:
            result["queue"] = None
            result["queue_error"] = str(exc)[:1000]
        return result

    def select(self, workspace_id: str, action: str, value: str) -> dict[str, Any]:
        self.fence(workspace_id)
        self.session._check_idle()
        if action == "create":
            queue_id = self.library.create(Path(value).expanduser())
        elif action == "open":
            queue_id = self.library.open(Path(value).expanduser())
        elif action == "select":
            queue_id = value
        else:
            raise ValueError("Unknown queue selection action")
        service = self.library.resolve(queue_id)
        service.inspect()
        self.selected, self.queue_id = service, queue_id
        self.reviewed_identity = None
        return self.state(workspace_id)

    def request(self, options: dict[str, Any]) -> QueueExecutionRequest:
        required = {"profile", "live_authorized", "automatic_approval", "case_seconds", "total_seconds", "skip_interrupted"}
        if not isinstance(options, dict) or set(options) != required:
            raise ValueError("Invalid test execution options")
        config = self.configuration()
        profile = options["profile"]
        if not isinstance(profile, str) or profile not in {item["name"] for item in config["profiles"]}:
            raise ValueError("Escolha um perfil de modelo valido.")
        return QueueExecutionRequest(Path(config["config_path"]), profile=profile,
            live_authorized=options["live_authorized"], automatic_approval=options["automatic_approval"],
            case_seconds=options["case_seconds"], total_seconds=options["total_seconds"],
            skip_interrupted=options["skip_interrupted"])

    def action(self, workspace_id: str, queue_id: str, fingerprint: str, action: str,
               value: str, options: dict[str, Any]) -> dict[str, Any]:
        self.fence(workspace_id, queue_id, fingerprint)
        if action in {"pause", "stop"}:
            if not self.running:
                self.selected.control(action)
            elif action == "pause":
                self.pause_requested.set()
            else:
                self.session.cancel()
            return {"requested": action}
        if action == "details":
            return {"details": self.selected.details(value)}
        if action == "report_document":
            return {"document": self.selected.inspect(include_details=True)}
        if action == "export":
            return {"report_path": str(self.selected.export(value))}
        self.session._check_idle()
        if action == "next":
            self.selected.prioritize(value)
        elif action == "detach":
            return self.detach(options)
        elif action == "check":
            return self.check(options)
        else:
            raise ValueError("Unknown test queue action")
        return self.state(workspace_id)

    def check(self, options: dict[str, Any]) -> dict[str, Any]:
        if not options.get("profile"):
            self.reviewed_identity = None
            return {"preflight": self.selected.check(), "local_only": True}
        request = self.request(options)
        document = self.selected.check(config_path=request.config_path, profile=request.profile,
            automatic_approval=request.automatic_approval, case_seconds=request.case_seconds)
        self.reviewed_identity = document["configuration"]["execution_identity"]
        return {"preflight": document}

    def detach(self, options: dict[str, Any]) -> dict[str, Any]:
        self.session.require_llm_enabled()
        if self.reviewed_identity is None or self.session.controller.pending.list():
            raise ValueError("Confira a preparação e resolva os pedidos pendentes antes de iniciar.")
        request = replace(self.request(options), expected_identity=self.reviewed_identity)
        worker = self.selected.start_detached(request)
        self.reviewed_identity = None
        return {"worker": worker}

    def start(self, workspace_id: str, queue_id: str, fingerprint: str, options: dict[str, Any],
              on_admitted: Any = None) -> Any:
        self.fence(workspace_id, queue_id, fingerprint)
        self.session._check_idle()
        if self.session.controller.pending.list():
            raise ValueError("Envie ou descarte as mensagens pendentes antes de iniciar a bateria.")
        request = self.request(options)
        if self.reviewed_identity is None:
            raise ValueError("Confira a preparacao antes de iniciar a bateria.")
        request = replace(request, expected_identity=self.reviewed_identity)
        service = self.selected

        def execute(_envelope: Any, cancellation: Any, _register: Any) -> Any:
            try:
                result = service.run(request, cancellation=cancellation, pause_requested=self.pause_requested)
                return {"status": "succeeded" if result.assessment == "verified" else result.assessment,
                        "success": result.assessment == "verified", "queue_id": queue_id,
                        "kind": "test_queue", "queue_status": result.document["status"], "counts": result.document["counts"],
                        "answer": f"Bateria de testes: {result.assessment}. Estado: {result.document['status']}. Resultados: {result.document['counts']}. Abra Testes para ver as evidencias."}, None
            finally:
                with self.session._lock:
                    self.running = False

        self.running = True
        self.reviewed_identity = None
        self.pause_requested.clear()
        try:
            return self.session._controlled(execute, owner="test_queue", track_activity=False, on_admitted=on_admitted)
        except BaseException:
            self.running = False
            raise
