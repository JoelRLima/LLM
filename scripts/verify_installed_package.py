"""Build and verify the installed wheel from outside the source checkout.

The default is the acceptance gate: it creates a venv without system packages
and asks pip to resolve every dependency declared by the wheel. Consequently,
it requires an available package index or wheelhouse.

``--offline-diagnostic`` is intentionally weaker: it reuses packages from the
base interpreter and skips dependency resolution. It can diagnose packaging and
installed behavior locally, but it never represents complete acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import venv
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DECLARED_RUNTIME_IMPORTS = ("ddgs", "requests", "rich")

INSTALLED_PROBE_SOURCE = """\
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

from agent.approval import ApprovalDecision, AutoApprove
from agent.skills import load_skill_registry
from agent.application import AgentApplication
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.authority import TaskAuthoritySnapshot
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


workspace = Path(sys.argv[1]).resolve()
sentinel = Path(sys.argv[2]).resolve()
scratch_dir = Path(sys.argv[3]).resolve()
stdio_process_required = False
sample = workspace / "sample.py"
sentinel_before = sentinel.read_bytes()
sample_before = sample.read_bytes()


class DeterministicJourneyGateway:
    # Small model contract used only by the installed Slice A probe.

    provider_name = "installed-slice-a-fixture"
    model = "installed-slice-a-fixture"
    profile = {"temperature": 0.0, "max_tokens": 256}
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self, objective):
        self.objective = objective
        self.calls = []

    def build_payload(self, request):
        return {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": request.stream,
        }

    def complete_payload(self, payload):
        messages = payload.get("messages", [])
        self.calls.append(payload)
        if "SLICE_A5_PROVIDER_FAILURE" in self.objective:
            raise RuntimeError("provider request failed https://example.test/?api_key=TOPSECRET")
        system = str(messages[0].get("content", "")) if messages else ""
        prompt = str(messages[-1].get("content", "")) if messages else ""
        if "You are a Router Agent" in system:
            return '{"persona": "coder"}'
        if "Crie um plano sequencial" in prompt:
            if "SLICE_A1" in self.objective:
                return '{"plan":[{"tool":"file_reader","args":{"file_path":"notes.txt"}}]}'
            if "SLICE_A2" in self.objective:
                return '{"plan":[{"tool":"grep","args":{"pattern":"SLICE_A2_EVIDENCE","path":"."}}]}'
            if "SLICE_C1" in self.objective or "SLICE_C3" in self.objective:
                return '{"plan":[{"tool":"shell","args":{"command":"git log -1"}}]}'
            if "SLICE_C2" in self.objective:
                return '{"plan":[{"tool":"shell","args":{"command":"git status"}}]}'
            if (
                "SLICE_B1" in self.objective
                or "SLICE_B2" in self.objective
                or "SLICE_B4" in self.objective
                or "SLICE_B5" in self.objective
            ):
                return '{"plan":[{"tool":"code_task","args":{"action":"modify","objective":"%s","targets":["sample.py"]}}]}' % self.objective
            if "SLICE_B3" in self.objective:
                return '{"plan":[{"tool":"file_writer","args":{"action":"write","file_path":"sample.py","content":"bypass\\n"}}]}'
            if "SLICE_D1" in self.objective or "SLICE_D3" in self.objective or "SLICE_D4" in self.objective:
                marker = "D1_EXTERNAL_EVIDENCE" if "SLICE_D1" in self.objective else ("D3_AUTHORITY_DENIED" if "SLICE_D3" in self.objective else "D4_EXTERNAL_FAILURE")
                return '{"plan":[{"tool":"demo_tool","args":{"text":"%s"}}]}' % marker
            return '{"plan":[{"tool":"file_reader","args":{"file_path":"../outside.txt"}}]}'
        if "Objetivo de engenharia:" in prompt and "SLICE_B" in self.objective:
            if "SLICE_B1" in self.objective:
                return '{"changes":[{"path":"sample.py","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"value = 2"}]}]}'
            if "SLICE_B2" in self.objective:
                return '{"changes":[{"path":"sample.py","kind":"modify","content":"def value(:"}]}'
            if "SLICE_B4" in self.objective:
                return '{"changes":[{"path":"../outside.py","kind":"create","content":"unauthorized\\\\n"}]}'
            if "SLICE_B5" in self.objective:
                return '{"changes":[{"path":"sample.py","kind":"modify","content":"value = 2\\\\n"}]}'
        if "Resultados das ferramentas executadas:" in prompt:
            if "SLICE_A1_EVIDENCE" in prompt:
                return "A leitura encontrou SLICE_A1_EVIDENCE no arquivo permitido."
            if "SLICE_A2_EVIDENCE" in prompt:
                return "A busca encontrou SLICE_A2_EVIDENCE no workspace."
            if "initial" in prompt:
                history_line = next(
                    (line.strip() for line in prompt.splitlines() if "initial" in line),
                    "initial",
                )
                return f"O histórico real do repositório confirma: {history_line}"
            if "SLICE_B1" in self.objective and "validado" in prompt.casefold():
                return "A modificaÃ§Ã£o foi aplicada e validada com sucesso pelo validator real."
            if "SLICE_B2" in self.objective:
                return "A modificaÃ§Ã£o nÃ£o foi validada: o validator real falhou e o arquivo foi revertido."
            if "SLICE_B4" in self.objective:
                return "A modificaÃ§Ã£o fora da autoridade foi recusada sem alterar o workspace."
            if "SLICE_B5" in self.objective:
                return "A proposta foi bloqueada antes da aplicaÃ§Ã£o; o arquivo permaneceu inalterado."
            if "D1_EXTERNAL_EVIDENCE" in prompt:
                return "A extensao externa confirmou D1_EXTERNAL_EVIDENCE pelo protocolo stdio real."
            if "RUNTIME_MISMATCH" in prompt or "TASK_AUTHORITY" in prompt or "autoridade" in prompt.casefold():
                return "A extensao foi descoberta, mas o gateway recusou a invocacao por autoridade insuficiente."
            if "D4_EXTERNAL_FAILURE" in prompt or "TOOL_ERROR" in prompt:
                return "A extensao externa falhou; a resposta nao foi considerada sucesso."
            if "acesso negado" in prompt.casefold() or "fora" in prompt.casefold():
                return "NÃ£o foi possÃ­vel ler o caminho externo: acesso negado."
            return "A tarefa foi concluÃ­da com a evidÃªncia retornada pela ferramenta."
        return '{"persona": "coder"}'

    def complete(self, request):
        return ModelResponse(content=self.complete_payload(self.build_payload(request)))

    def send_payload(self, payload, stream):
        del stream
        return self.complete_payload(payload)

    def consume_stream(self, response, callbacks):
        text = str(response)
        callbacks["on_content_chunk"](text)
        callbacks["on_done"]({})
        return text

    def count_tokens(self, text):
        return max(1, len(text) // 4)


class GatewayApprovePreviewPolicy:
    def request(self, request):
        if request.action == "apply_changeset":
            return ApprovalDecision.REQUIRED
        return ApprovalDecision.APPROVED


def project_measurement(name, objective, started_at, application, result, family="a"):
    history = list(application.orchestrator.agent_state.tool_history)
    entry = history[-1] if history else {}
    raw = entry.get("result") if isinstance(entry, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    data = raw.get("data")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    output = json.dumps(data, ensure_ascii=False, default=str) if data is not None else ""
    error = str(raw.get("error") or result.error or "")[:500]
    denied = "acesso negado" in error.casefold()
    outcome = "SUCCESS" if result.status == "succeeded" else ("DENIED" if denied else result.status.upper())
    invocations = []
    modification_id = entry.get("invocation_id") or raw.get("invocation_id")
    if modification_id:
        invocations.append({"phase": "modification", "invocation_id": modification_id, "outcome": outcome})
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    if isinstance(artifacts, (list, tuple)):
        for artifact in artifacts:
            artifact_metadata = artifact.get("metadata") if isinstance(artifact, dict) else None
            if not isinstance(artifact_metadata, dict):
                continue
            validation_id = artifact_metadata.get("validation_invocation_id")
            if validation_id:
                invocations.append({
                    "phase": "validation",
                    "invocation_id": validation_id,
                    "outcome": str(artifact_metadata.get("validation", "")).upper(),
                })
    return {
        "task_id": f"installed-slice-{family}:{name}",
        "objective": objective,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "tools": [entry.get("tool")] if entry.get("tool") else [],
        "invocation_id": entry.get("invocation_id") or raw.get("invocation_id"),
        "terminal_outcome": outcome,
        "error": error,
        "output_chars": int(metadata.get("total_chars", len(output))),
        "truncated": bool(metadata.get("truncated", False)),
        "tool_history_count": len(history),
        "invocations": invocations,
    }


def assert_public_receipt(result, expected_workspace=None):
    receipt = result.receipt
    expected = expected_workspace or workspace
    if not isinstance(receipt, dict) or receipt.get("workspace") != str(expected):
        raise AssertionError(f"receipt publico ausente ou workspace divergente: {result.to_dict()!r}")
    if not result.report_path or not Path(result.report_path).is_file():
        raise AssertionError(f"report_path nao persistido: {result.to_dict()!r}")
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    if bool(report.get("success")) != result.success or report.get("status") != result.status:
        raise AssertionError(f"report e resultado divergentes: {result.to_dict()!r}")
    if report.get("receipt", {}).get("workspace") != str(expected):
        raise AssertionError(f"report nao projetou receipt: {result.to_dict()!r}")


def run_slice_a_journeys(app_home, workspace, scratch_dir, outside):
    paths = AppPaths.discover(app_home, env={})
    ConfigRepository(paths).initialize()
    scenarios = (
        ("a1_read", "SLICE_A1: leia o arquivo permitido e informe a evidÃªncia.", True),
        ("a2_search", "SLICE_A2: busque a evidÃªncia no workspace e informe o resultado.", True),
        ("a3_denied", "SLICE_A3: leia o caminho fora do workspace e informe o resultado.", True),
        ("a5_provider_failure", "SLICE_A5_PROVIDER_FAILURE: leia notes.txt.", True),
        ("a4_no_tool", "oi", False),
    )
    measurements = []
    for name, objective, uses_model in scenarios:
        started_at = time.monotonic()
        gateway = DeterministicJourneyGateway(objective)
        with AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=gateway,
            configure_logging=False,
        ) as application:
            result = application.run(objective)
            if name in {"a1_read", "a2_search"}:
                assert_public_receipt(result)
                if result.receipt.get("executed") is not True or result.receipt.get("files_affected") != []:
                    raise AssertionError(f"receipt de leitura incorreto: {result.to_dict()!r}")
            if name == "a5_provider_failure":
                assert_public_receipt(result)
                visible = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
                visible += Path(result.report_path).read_text(encoding="utf-8") if result.report_path else ""
                if result.status != "failed" or result.error != "Model provider request failed.":
                    raise AssertionError(f"falha de provider sem mensagem estavel: {result.to_dict()!r}")
                if "TOPSECRET" in visible:
                    raise AssertionError(f"segredo do provider exposto no probe instalado: {visible!r}")
            measurement = project_measurement(name, objective, started_at, application, result)
            measurement["answer"] = result.answer[:500]
            measurement["model_calls"] = len(gateway.calls)
            measurement["status"] = result.status
            if uses_model and name == "a4_no_tool":
                raise AssertionError("cenÃ¡rio no-tool nÃ£o deveria usar modelo")
            if name == "a4_no_tool" and measurement["tools"]:
                raise AssertionError("cenÃ¡rio no-tool invocou ferramenta")
            if name == "a3_denied" and measurement["terminal_outcome"] != "DENIED":
                raise AssertionError(f"denial instalada nÃ£o observÃ¡vel: {measurement!r}")
            if name == "a3_denied":
                assert_public_receipt(result)
                if result.receipt.get("executed") is not True or result.receipt.get("files_affected") != []:
                    raise AssertionError(f"denial instalada nao refletiu entrada na capability: {result.to_dict()!r}")
            if name in {"a1_read", "a2_search"} and not any(
                marker in result.answer for marker in ("SLICE_A1_EVIDENCE", "SLICE_A2_EVIDENCE")
            ):
                raise AssertionError(f"resposta nÃ£o consumiu evidÃªncia: {result.answer!r}")
            measurements.append(measurement)
    return measurements


def run_shell_journeys(app_home, workspace, failure_workspace):
    scenarios = (
        ("c1_history", "SLICE_C1: inspecione o histórico recente do repositório.", workspace),
        ("c2_unsupported", "SLICE_C2: execute git status para mostrar o estado do repositório.", workspace),
        ("c3_failure", "SLICE_C3: inspecione o histórico local.", failure_workspace),
    )
    measurements = []
    for name, objective, scenario_workspace in scenarios:
        started_at = time.monotonic()
        gateway = DeterministicJourneyGateway(objective)
        scenario_paths = AppPaths.discover(app_home / name, env={})
        ConfigRepository(scenario_paths).initialize()
        with AgentApplication.create(
            paths=scenario_paths,
            workspace=scenario_workspace,
            gateway=gateway,
            approval_policy=AutoApprove(),
            configure_logging=False,
        ) as application:
            result = application.run(objective)
            measurement = project_measurement(name, objective, started_at, application, result, family="c")
            measurement["answer"] = result.answer[:500]
            measurement["model_calls"] = len(gateway.calls)
            measurement["status"] = result.status
            measurements.append(measurement)
            history = application.orchestrator.agent_state.tool_history
            if not history:
                if name == "c2_unsupported" and result.status not in {"failed", "blocked"}:
                    raise AssertionError(f"capability removida sem resposta coerente: {measurement!r}")
                if name == "c2_unsupported" and "bloqueada" not in result.answer.casefold():
                    raise AssertionError(f"request unsupported não foi bloqueado: {measurement!r}")
                continue
            history_result = history[-1]["result"]
            if name == "c1_history":
                if result.status != "succeeded" or "initial" not in result.answer:
                    raise AssertionError(f"histórico instalado não consumido: {measurement!r}")
            elif name == "c2_unsupported":
                if history_result.get("ok") is not False or "permitido" not in str(history_result.get("error", "")).casefold():
                    raise AssertionError(f"capability removida executada: {measurement!r}")
                if result.receipt.get("executed") is not False:
                    raise AssertionError(f"capability removida indicou execucao: {result.to_dict()!r}")
            elif name == "c3_failure" and result.receipt.get("executed") is not True:
                raise AssertionError(f"failure de shell nao marcou fronteira executada: {result.to_dict()!r}")
            elif result.status == "succeeded" or not result.answer:
                raise AssertionError(f"failure de shell não foi observado: {measurement!r}")
    return measurements


def run_modify_journeys(app_home, workspace):
    scenarios = (
        ("b1_modify_validate", "SLICE_B1: altere sample.py e valide a modificaÃ§Ã£o.", "success"),
        ("b2_validation_failure", "SLICE_B2: aplique a alteraÃ§Ã£o determinÃ­stica e valide.", "failure"),
        ("b3_writer_bypass", "SLICE_B3: use file_writer diretamente para alterar sample.py.", "bypass"),
        ("b4_denied_modify", "SLICE_B4: modifique o alvo fora da autoridade permitida.", "denied"),
        ("b5_preview_blocked", "SLICE_B5: proponha uma modificaÃ§Ã£o sem aplicar.", "preview"),
    )
    measurements = []
    for name, objective, expected in scenarios:
        scenario_workspace = workspace.parent / f"slice-b-{name}"
        scenario_workspace.mkdir(parents=True, exist_ok=True)
        sample = scenario_workspace / "sample.py"
        sample.write_text("value = 1\\n", encoding="utf-8")
        before = sample.read_text(encoding="utf-8")
        outside = workspace.parent / f"{name}-outside.py"
        if expected == "denied":
            outside.write_text("outside = True\\n", encoding="utf-8")
            outside_before = outside.read_text(encoding="utf-8")
        started_at = time.monotonic()
        gateway = DeterministicJourneyGateway(objective)
        scenario_paths = AppPaths.discover(app_home / name, env={})
        ConfigRepository(scenario_paths).initialize()
        with AgentApplication.create(
            paths=scenario_paths,
            workspace=scenario_workspace,
            gateway=gateway,
            approval_policy=GatewayApprovePreviewPolicy() if expected == "preview" else AutoApprove(),
            configure_logging=False,
        ) as application:
            application.orchestrator._route_persona(objective)
            planning_view = application.orchestrator.get_planning_view("linear")
            if expected == "bypass" and planning_view is not None and "file_writer" in planning_view.presented_names:
                raise AssertionError("file_writer permanece model-actionable na jornada suportada")
            result = application.run(objective)
            if expected in {"success", "failure", "bypass", "denied", "preview"}:
                assert_public_receipt(result, scenario_workspace)
            measurement = project_measurement(name, objective, started_at, application, result, family="b")
            measurement["answer"] = result.answer[:500]
            measurement["model_calls"] = len(gateway.calls)
            measurement["status"] = result.status
            measurement["before"] = before
            measurement["after"] = sample.read_text(encoding="utf-8")
            measurements.append(measurement)
            invocations = measurement.get("invocations", [])
            if expected == "success":
                if result.status != "succeeded" or measurement["before"] == measurement["after"]:
                    raise AssertionError(f"B1 nÃ£o modificou com sucesso: {measurement!r}")
                if len(invocations) != 2 or invocations[1].get("outcome") != "PASSED":
                    raise AssertionError(f"B1 nÃ£o observou validaÃ§Ã£o real: {measurement!r}")
                if invocations[0]["invocation_id"] == invocations[1]["invocation_id"]:
                    raise AssertionError(f"B1 reutilizou invocation_id: {measurement!r}")
                if result.receipt.get("executed") is not True or result.receipt.get("files_affected") != ["sample.py"]:
                    raise AssertionError(f"B1 receipt nao refletiu mutacao: {result.to_dict()!r}")
                if result.receipt.get("validation") != {"ran": True, "outcome": "passed"}:
                    raise AssertionError(f"B1 receipt nao refletiu validacao: {result.to_dict()!r}")
                if result.receipt.get("rollback", {}).get("occurred"):
                    raise AssertionError(f"B1 receipt indicou rollback: {result.to_dict()!r}")
                if result.receipt.get("final_state") != "applied":
                    raise AssertionError(f"B1 receipt nao refletiu estado final: {result.to_dict()!r}")
            elif expected == "failure":
                if result.status == "succeeded" or "validada" in result.answer.casefold():
                    raise AssertionError(f"B2 publicou sucesso validado após failure: {measurement!r}")
                if measurement["before"] != measurement["after"] or len(invocations) != 2 or invocations[1].get("outcome") != "FAILED":
                    raise AssertionError(f"B2 nÃ£o preservou failure/rollback: {measurement!r}")
                if result.receipt.get("executed") is not True or result.receipt.get("files_affected") != ["sample.py"]:
                    raise AssertionError(f"B2 receipt nao refletiu execucao: {result.to_dict()!r}")
                if result.receipt.get("validation") != {"ran": True, "outcome": "failed"}:
                    raise AssertionError(f"B2 receipt nao refletiu failure: {result.to_dict()!r}")
                if result.receipt.get("rollback") != {"occurred": True, "outcome": "restored"}:
                    raise AssertionError(f"B2 receipt nao refletiu restauracao: {result.to_dict()!r}")
                if result.receipt.get("final_state") != "restored":
                    raise AssertionError(f"B2 receipt nao refletiu estado restaurado: {result.to_dict()!r}")
            elif expected == "bypass":
                if result.status == "succeeded" or measurement["before"] != measurement["after"]:
                    raise AssertionError(f"B3 writer direto ainda concluiu: {measurement!r}")
            elif expected == "denied":
                if result.status == "succeeded" or outside.read_text(encoding="utf-8") != outside_before:
                    raise AssertionError(f"B4 alterou alvo fora da autoridade: {measurement!r}")
                if result.receipt.get("executed") is not True or result.receipt.get("files_affected") != []:
                    raise AssertionError(f"B4 receipt nao refletiu entrada na capability: {result.to_dict()!r}")
            elif expected == "preview":
                if result.status != "blocked" or measurement["before"] != measurement["after"]:
                    raise AssertionError(f"B5 preview nao foi bloqueado sem mutacao: {measurement!r}")
                if result.receipt.get("executed") is not True:
                    raise AssertionError(f"B5 preview nao refletiu entrada na capability: {result.to_dict()!r}")
                if result.receipt.get("proposed_files") != ["sample.py"]:
                    raise AssertionError(f"B5 preview nao preservou proposed_files: {result.to_dict()!r}")
                if result.receipt.get("files_affected") != [] or result.receipt.get("final_state") == "applied":
                    raise AssertionError(f"B5 preview indicou efeito aplicado: {result.to_dict()!r}")
    return measurements


def run_extension_journeys(base_dir):
    global stdio_process_required
    extension_dir = base_dir / "external-stdio-extension"
    extension_dir.mkdir(parents=True, exist_ok=True)
    marker = extension_dir / "spawned.txt"
    manifest = extension_dir / "manifest.json"
    marker_literal = repr(str(marker))
    (extension_dir / "tool.py").write_text(
        "import json\\n"
        "from pathlib import Path\\n"
        "payload = json.loads(__import__('sys').stdin.read())\\n"
        f"Path({marker_literal}).write_text('spawned', encoding='utf-8')\\n"
        "text = payload.get('args', {}).get('text', '')\\n"
        "if text == 'D4_EXTERNAL_FAILURE':\\n"
        "    response = {'invocation_id': payload.get('invocation_id'), 'status': 'failed', 'message': 'D4_EXTERNAL_FAILURE'}\\n"
        "else:\\n"
        "    response = {'invocation_id': payload.get('invocation_id'), 'status': 'succeeded', 'message': f'externo: {text}', 'data': {'echo': text}}\\n"
        "print(json.dumps(response), flush=True)\\n",
        encoding="utf-8",
    )
    manifest.write_text(json.dumps({
        "id": "installed.demo.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["${python}", "${extension_dir}/tool.py"],
        "timeout_seconds": 5,
        "tools": [{
            "name": "demo_tool",
            "description": "Extensao externa stdio deterministica.",
            "schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            "capabilities": ["read", "process"],
        }],
    }), encoding="utf-8")
    invalid_manifest = base_dir / "missing-process-manifest.json"
    invalid_manifest.write_text(json.dumps({
        "id": "missing.process.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["${python}", "${extension_dir}/tool.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "missing_process_tool", "schema": {}, "capabilities": ["read"]}],
    }), encoding="utf-8")
    invalid_catalog = ExtensionCatalogService(
        ExtensionCatalogStorage(base_dir / "missing-process-catalog.json")
    )
    try:
        invalid_catalog.add(invalid_manifest)
    except RuntimeError:
        stdio_process_required = True
    else:
        raise AssertionError("manifest stdio sem process foi aceito")
    if marker.exists():
        raise AssertionError("validacao de manifest stdio iniciou processo")

    measurements = []
    scenarios = (
        ("d1_success", "SLICE_D1: use a extensao externa e informe D1_EXTERNAL_EVIDENCE.", TaskAuthoritySnapshot(frozenset({"read", "process"}))),
        ("d3_denied", "SLICE_D3: use a extensao externa sem autoridade suficiente.", TaskAuthoritySnapshot(frozenset({"read", "process"}), runtime_identity=RuntimeSnapshotIdentity("wrong-runtime", "workspace"))),
        ("d4_failure", "SLICE_D4: use a extensao externa e reporte D4_EXTERNAL_FAILURE.", TaskAuthoritySnapshot(frozenset({"read", "process"}))),
    )
    for name, objective, authority in scenarios:
        scenario_home = base_dir / f"app-{name}"
        scenario_workspace = base_dir / f"workspace-{name}"
        scenario_home.mkdir(parents=True, exist_ok=True)
        scenario_workspace.mkdir(parents=True, exist_ok=True)
        paths = AppPaths.discover(scenario_home, env={})
        ConfigRepository(paths).initialize()
        catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
        catalog.add(manifest)
        workspace_id = WorkspaceContext.create(scenario_workspace).workspace_id
        service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
        service.enable("installed.demo.extension")
        service.grant("installed.demo.extension", "read")
        service.grant("installed.demo.extension", "process")
        marker.unlink(missing_ok=True)
        started_at = time.monotonic()
        gateway = DeterministicJourneyGateway(objective)
        with AgentApplication.create(paths=paths, workspace=scenario_workspace, gateway=gateway, task_authority=authority, approval_policy=AutoApprove(), configure_logging=False) as application:
            application.orchestrator._route_persona(objective)
            planning_view = application.orchestrator.get_planning_view("linear")
            if planning_view is None or "demo_tool" not in planning_view.presented_names:
                raise AssertionError(f"extension nao ficou visivel pelo planner: names={application.tool_registry.names()!r}, diagnostics={application.bootstrap_diagnostics!r}, view={planning_view!r}")
            result = application.run(objective)
            measurement = project_measurement(name, objective, started_at, application, result, family="d")
            measurement.update({"answer": result.answer[:500], "model_calls": len(gateway.calls), "status": result.status, "spawned": marker.is_file()})
            measurements.append(measurement)
            if name == "d1_success" and (result.status != "succeeded" or "D1_EXTERNAL_EVIDENCE" not in result.answer or not marker.is_file()):
                raise AssertionError(f"D1 nao consumiu processo externo: {measurement!r}")
            if name == "d3_denied" and (marker.exists() or result.status == "succeeded" or not result.answer):
                raise AssertionError(f"D3 nao negou antes do spawn: {measurement!r}")
            if name == "d4_failure" and (result.status == "succeeded" or not marker.is_file() or not result.answer):
                raise AssertionError(f"D4 nao preservou failure externo: {measurement!r}")
    return measurements

registry = load_skill_registry(
    base_dir=workspace,
    scratch_dir=scratch_dir,
    config={"hardware_profile": "low_vram_8gb"},
)
review = registry.skill("code_task").execute(
    {"action": "review", "targets": ["sample.py"]}
)
diagnostics = review.get("data", {}).get("diagnostics", [])
codes = sorted(
    item.get("code")
    for item in diagnostics
    if isinstance(item, dict) and isinstance(item.get("code"), str)
)

escape_path = os.path.relpath(sentinel, workspace)
denied = registry.skill("file_reader").execute({"file_path": escape_path})
shell = registry.skill("shell")
git_reader = registry.skill("git_reader")
history_results = {
    "git_log": git_reader.execute({"command": "log"}),
    "git_log_one": git_reader.execute({"command": "log", "args": "-1"}),
    "shell_log": shell.execute({"command": "git log -1"}),
}
escape_attempts = {
    "shell_status": shell.execute({"command": "git status"}),
    "shell_diff": shell.execute({"command": "git diff"}),
    "shell_write": shell.execute(
        {"command": shlex.join(["tree", "-o", escape_path, "."])}
    ),
    "git_status": git_reader.execute({"command": "status"}),
    "git_diff": git_reader.execute({"command": "diff"}),
    "git_remerge": git_reader.execute({"command": "log", "args": "--remerge-diff -1"}),
    "shell_remerge": shell.execute({"command": "git log --remerge-diff -1"}),
    "shell_ruff_add_noqa": shell.execute({"command": "ruff check --add-noqa sample.py"}),
    "shell_ruff_add_ignore": shell.execute({"command": "ruff check --add-ignore sample.py"}),
    "shell_ruff_fix": shell.execute({"command": "ruff check --fix sample.py"}),
}
secret = sentinel_before.decode("utf-8")

if not review.get("ok") or review.get("status") != "succeeded":
    raise SystemExit(f"installed code review failed: {review!r}")
if "PYSEC001" not in codes:
    raise SystemExit(f"installed code review missed PYSEC001: {codes!r}")
if denied.get("ok") is not False:
    raise SystemExit("installed file_reader escaped the workspace")
for name, result in escape_attempts.items():
    if result.get("ok") is not False:
        raise SystemExit(f"installed {name} escaped the workspace: {result!r}")
    if secret in json.dumps(result, ensure_ascii=False):
        raise SystemExit(f"installed {name} exposed the external sentinel")
for name, result in history_results.items():
    if result.get("ok") is not True:
        raise SystemExit(f"installed {name} failed: {result!r}")
if sentinel.read_bytes() != sentinel_before or sample.read_bytes() != sample_before:
    raise SystemExit("installed probe mutated its workspace or sentinel")

outside = workspace.parent / "outside.txt"
slice_measurements = run_slice_a_journeys(
    workspace.parent / "slice-a-app-home",
    workspace,
    scratch_dir,
    outside,
)
if len(slice_measurements) != 5:
    raise SystemExit(f"installed Slice A produziu mediÃ§Ã£o incompleta: {slice_measurements!r}")
if any(item.get("invocation_id") is None for item in slice_measurements[:3]):
    raise SystemExit(f"installed Slice A perdeu invocation_id: {slice_measurements!r}")
if slice_measurements[3].get("tools"):
    raise SystemExit(f"installed Slice A no-tool invocou ferramenta: {slice_measurements[3]!r}")
failure_workspace = workspace.parent / "shell-failure-workspace"
failure_workspace.mkdir(parents=True, exist_ok=True)
shell_measurements = run_shell_journeys(
    workspace.parent / "slice-c-app-home",
    workspace,
    failure_workspace,
)
if len(shell_measurements) != 3:
    raise SystemExit(f"installed Slice C produziu mediÃ§Ã£o incompleta: {shell_measurements!r}")
if not shell_measurements[0].get("invocation_id") or shell_measurements[0].get("terminal_outcome") != "SUCCESS":
    raise SystemExit(f"installed Slice C C1 falhou: {shell_measurements!r}")
if shell_measurements[1].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice C C2 executou capability removida: {shell_measurements!r}")
if shell_measurements[2].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice C C3 não reportou failure: {shell_measurements!r}")
modify_measurements = run_modify_journeys(
    workspace.parent / "slice-b-app-home",
    workspace,
)
if len(modify_measurements) != 5:
    raise SystemExit(f"installed Slice B produziu mediÃ§Ã£o incompleta: {modify_measurements!r}")
if modify_measurements[0].get("terminal_outcome") != "SUCCESS":
    raise SystemExit(f"installed Slice B B1 falhou: {modify_measurements!r}")
if modify_measurements[1].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice B B2 publicou sucesso indevido: {modify_measurements!r}")
if modify_measurements[2].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice B B3 manteve bypass: {modify_measurements!r}")
if modify_measurements[3].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice B B4 alterou alvo negado: {modify_measurements!r}")
if modify_measurements[4].get("terminal_outcome") == "SUCCESS":
    raise SystemExit(f"installed Slice B B5 aplicou preview bloqueado: {modify_measurements!r}")
extension_measurements = run_extension_journeys(workspace.parent / "slice-d")
if len(extension_measurements) != 3:
    raise SystemExit(f"installed Slice D produziu mediÃƒÂ§ÃƒÂ£o incompleta: {extension_measurements!r}")
print(
    json.dumps(
        {
            "diagnostic_codes": codes,
            "stdio_process_required": stdio_process_required,
            "escape_denied": True,
            "process_escape_denied": sorted(escape_attempts),
            "slice_a": slice_measurements,
            "slice_c": shell_measurements,
            "slice_b": modify_measurements,
            "slice_d": extension_measurements,
            "status": "ok",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
)
"""

EXTENSION_BOOTSTRAP_PROBE_SOURCE = """\
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import agent
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.contracts import ToolAdapter, ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


app_home = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
checkout = Path(sys.argv[3]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
extension_dir = workspace.parent / "extension-source"
extension_dir.mkdir(parents=True, exist_ok=True)
manifest = extension_dir / "manifest.json"
manifest.write_text(
    json.dumps(
        {
            "id": "wheel.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
            "transport": "stdio",
            "entrypoint": ["${python}", "${extension_dir}/tool.py"],
            "timeout_seconds": 5,
            "tools": [{"name": "wheel_tool", "schema": {}, "capabilities": ["read", "process"]}],
        }
    ),
    encoding="utf-8",
)
paths = AppPaths.discover(app_home, env={})
ConfigRepository(paths).initialize()
catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
catalog.add(manifest)
workspace_id = WorkspaceContext.create(workspace).workspace_id
service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
service.enable("wheel.extension")
service.grant("wheel.extension", "read")
service.grant("wheel.extension", "process")
process_calls = []


class BuiltinProbeAdapter(ToolAdapter):
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (ToolDescriptor("echo", "builtin", schema={}),)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED)


def forbidden(name):
    def fail(*args, **kwargs):
        process_calls.append(name)
        raise AssertionError(name)
    return fail


with patch.object(subprocess, "Popen", forbidden("Popen")), \
     patch.object(subprocess, "run", forbidden("run")), \
     patch.object(os, "system", forbidden("system")), \
     patch.object(asyncio, "create_subprocess_exec", forbidden("create_subprocess_exec")), \
     patch.object(asyncio, "create_subprocess_shell", forbidden("create_subprocess_shell")):
    result = ApplicationExtensionBootstrap(paths, workspace_id, workspace).build(
        BuiltinProbeAdapter()
    )
    adapter = result.registry._descriptors_cache["wheel_tool"][0]
    payload = {
        "tool": "wheel_tool",
        "cwd_ok": adapter.cwd == workspace,
        "builtins": "echo" in result.registry.names(),
        "process_calls": process_calls,
        "checkout_import": checkout in Path(agent.__file__).resolve().parents,
    }

print(json.dumps(payload, sort_keys=True))
"""


class VerificationError(RuntimeError):
    """Raised when the installed artifact violates a distribution invariant."""


@dataclass(frozen=True)
class CommandResult:
    name: str
    stdout: str
    stderr: str


class _F1ModelHandler(BaseHTTPRequestHandler):
    """Deterministic OpenAI-compatible model used by installed F1 acceptance."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract.
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        messages = payload.get("messages", [])
        system = str(messages[0].get("content", "")) if messages else ""
        prompt = str(messages[-1].get("content", "")) if messages else ""
        if "You are a Router Agent" in system:
            content = '{"persona":"coder"}'
        elif "Crie um plano sequencial" in prompt:
            content = '{"plan":[{"tool":"wheel_tool","args":{}}]}'
        elif "Resultados das ferramentas executadas:" in prompt:
            content = "F1_INSTALLED_EVIDENCE"
        else:
            content = '{"persona":"coder"}'
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def _emit_failure_annotation(message: str) -> None:
    compact = " ".join(message.split())[:1200]
    escaped = (
        compact.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )
    print(
        f"::error title=Installed wheel acceptance::{escaped}",
        file=sys.stderr,
    )


@dataclass(frozen=True)
class InstallationMode:
    name: str
    system_site_packages: bool
    install_dependencies: bool
    acceptance: bool


def installation_mode(offline_diagnostic: bool = False) -> InstallationMode:
    if offline_diagnostic:
        return InstallationMode(
            name="offline-diagnostic",
            system_site_packages=True,
            install_dependencies=False,
            acceptance=False,
        )
    return InstallationMode(
        name="clean-acceptance",
        system_site_packages=False,
        install_dependencies=True,
        acceptance=True,
    )


def installed_cli_commands(
    executable: Path,
    workspace: Path,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("version", (str(executable), "--version")),
        ("config-init", (str(executable), "config", "init")),
        ("doctor", (str(executable), "doctor", "--json")),
        (
            "run",
            (
                str(executable),
                "run",
                "--json",
                "--workspace",
                str(workspace),
                "oi",
            ),
        ),
    )


def snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Capture content and write-sensitive metadata for every regular file."""

    if not root.exists():
        return {}
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[path.relative_to(root).as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
            digest,
        )
    return snapshot


def parse_json_output(result: CommandResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"{result.name} não produziu JSON puro: {result.stdout!r}"
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise VerificationError(f"{result.name} deve produzir um objeto JSON não vazio.")
    return payload


def _run(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = 180,
) -> CommandResult:
    print(f"[installed-gate] {name}...", flush=True)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(
            f"{name} excedeu o timeout de {timeout_seconds}s."
        ) from exc
    result = CommandResult(name, completed.stdout, completed.stderr)
    if completed.returncode != 0:
        raise VerificationError(
            f"{name} falhou com exit code {completed.returncode}.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return result


def _run_expected_failure(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> CommandResult:
    print(f"[installed-gate] {name}...", flush=True)
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    if completed.returncode == 0:
        raise VerificationError(f"{name} deveria falhar, mas retornou sucesso.")
    return CommandResult(name, completed.stdout, completed.stderr)


def _venv_executable(environment_dir: Path, name: str) -> Path:
    scripts = environment_dir / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return scripts / f"{name}{suffix}"


def wheel_install_command(
    venv_python: Path,
    wheel: Path,
    mode: InstallationMode,
) -> tuple[str, ...]:
    command = [
        str(venv_python),
        "-m",
        "pip",
        "install",
    ]
    if not mode.install_dependencies:
        command.extend(("--no-deps", "--force-reinstall"))
    command.extend(
        (
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            str(wheel),
        )
    )
    return tuple(command)


def _site_packages(venv_python: Path, cwd: Path, environment: Mapping[str, str]) -> Path:
    code = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    result = _run(
        "locate-site-packages",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    return Path(result.stdout.strip()).resolve()


def _build_wheel(
    project_root: Path,
    wheel_dir: Path,
    python: Path,
    *,
    no_build_isolation: bool,
) -> Path:
    command = [
        str(python),
        "-m",
        "pip",
        "wheel",
        "--no-deps",
    ]
    if no_build_isolation:
        command.append("--no-build-isolation")
    command.extend(
        [
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            "--wheel-dir",
            str(wheel_dir),
            str(project_root),
        ]
    )
    _run(
        "build-wheel",
        command,
        cwd=wheel_dir,
    )
    wheels = sorted(wheel_dir.glob("local_llm_agent-*.whl"))
    if len(wheels) != 1:
        raise VerificationError(
            f"Esperado exatamente um wheel da aplicação; encontrados: {wheels}"
        )
    return wheels[0]


def _prepare_local_history_workspace(workspace: Path, sample: Path) -> None:
    for name, command in (
        ("installed-git-init", ("git", "init", "-q")),
        ("installed-git-user", ("git", "config", "user.name", "Installed Gate")),
        (
            "installed-git-email",
            ("git", "config", "user.email", "installed-gate@example.invalid"),
        ),
        ("installed-git-add", ("git", "add", sample.name)),
        ("installed-git-commit", ("git", "commit", "-qm", "initial")),
    ):
        _run(name, command, cwd=workspace)


def _install_wheel(
    wheel: Path,
    environment_dir: Path,
    external_cwd: Path,
    mode: InstallationMode,
) -> tuple[Path, Path]:
    print(f"[installed-gate] create-venv ({mode.name})...", flush=True)
    venv.EnvBuilder(
        with_pip=True,
        system_site_packages=mode.system_site_packages,
        clear=True,
    ).create(environment_dir)
    venv_python = _venv_executable(environment_dir, "python")
    _run(
        "install-wheel",
        wheel_install_command(venv_python, wheel, mode),
        cwd=external_cwd,
    )
    entrypoint = _venv_executable(environment_dir, "llm-agent")
    if not entrypoint.is_file():
        raise VerificationError(f"Console script não foi instalado: {entrypoint}")
    return venv_python, entrypoint


def _runtime_environment(app_home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["LLM_AGENT_HOME"] = str(app_home)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["NO_COLOR"] = "1"
    return environment


def _verify_import_origin(
    venv_python: Path,
    site_packages: Path,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    code = "from pathlib import Path; import agent; print(Path(agent.__file__).resolve())"
    result = _run(
        "installed-import",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    imported = Path(result.stdout.strip()).resolve()
    try:
        imported.relative_to(site_packages)
    except ValueError as exc:
        raise VerificationError(
            f"'agent' foi importado fora do site-packages isolado: {imported}"
        ) from exc


def _verify_declared_dependencies(
    venv_python: Path,
    site_packages: Path,
    cwd: Path,
    environment: Mapping[str, str],
    mode: InstallationMode,
) -> None:
    modules = json.dumps(DECLARED_RUNTIME_IMPORTS)
    code = (
        "import importlib, json; "
        f"names = {modules}; "
        "print(json.dumps({name: importlib.import_module(name).__file__ "
        "for name in names}, sort_keys=True))"
    )
    result = _run(
        "declared-dependencies",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    origins = parse_json_output(result)
    if not mode.acceptance:
        return
    for name in DECLARED_RUNTIME_IMPORTS:
        raw_origin = origins.get(name)
        if not isinstance(raw_origin, str):
            raise VerificationError(f"Dependência declarada sem origem válida: {name}")
        origin = Path(raw_origin).resolve()
        try:
            origin.relative_to(site_packages)
        except ValueError as exc:
            raise VerificationError(
                f"Dependência '{name}' não foi instalada no venv limpo: {origin}"
            ) from exc


def _verify_installed_probe(
    venv_python: Path,
    workspace: Path,
    sentinel: Path,
    scratch_dir: Path,
    probe_script: Path,
    environment: Mapping[str, str],
) -> None:
    result = _run(
        "installed-offline-probe",
        (
            str(venv_python),
            str(probe_script),
            str(workspace),
            str(sentinel),
            str(scratch_dir),
        ),
        cwd=probe_script.parent,
        environment=environment,
    )
    payload = parse_json_output(result)
    if payload.get("status") != "ok":
        raise VerificationError("Probe instalado não reportou status=ok.")
    if payload.get("escape_denied") is not True:
        raise VerificationError("Probe instalado não confirmou confinamento ao workspace.")
    if payload.get("stdio_process_required") is not True:
        raise VerificationError("Probe instalado aceitou manifest stdio sem capability process.")
    codes = payload.get("diagnostic_codes")
    if not isinstance(codes, list) or "PYSEC001" not in codes:
        raise VerificationError("Probe instalado não confirmou análise de código real.")
    process_guards = payload.get("process_escape_denied")
    if process_guards != [
        "git_diff",
        "git_remerge",
        "git_status",
        "shell_diff",
        "shell_remerge",
        "shell_ruff_add_ignore",
        "shell_ruff_add_noqa",
        "shell_ruff_fix",
        "shell_status",
        "shell_write",
    ]:
        raise VerificationError(
            "Probe instalado não confirmou confinamento de ShellSkill/GitSkill."
        )
    _validate_slice_a_payload(payload)
    _validate_slice_c_payload(payload)
    _validate_slice_b_payload(payload)
    _validate_slice_d_payload(payload)


def _validate_slice_a_payload(payload: Mapping[str, Any]) -> None:
    slice_a = payload.get("slice_a")
    expected_ids = [
        "installed-slice-a:a1_read",
        "installed-slice-a:a2_search",
        "installed-slice-a:a3_denied",
        "installed-slice-a:a5_provider_failure",
        "installed-slice-a:a4_no_tool",
    ]
    if not isinstance(slice_a, list) or len(slice_a) != 5:
        raise VerificationError("Probe instalado nao executou os cinco cenarios Slice A.")
    if [item.get("task_id") for item in slice_a] != expected_ids:
        raise VerificationError("Probe instalado nao executou os cinco cenarios Slice A.")
    if [item.get("terminal_outcome") for item in slice_a[:2]] != ["SUCCESS", "SUCCESS"]:
        raise VerificationError("Slice A read/search instalada nÃ£o produziu sucesso.")
    if slice_a[2].get("terminal_outcome") != "DENIED":
        raise VerificationError("Slice A nÃ£o observou denial de path externo.")
    if slice_a[3].get("terminal_outcome") != "FAILED" or slice_a[3].get("error") != "Model provider request failed.":
        raise VerificationError("Slice A provider failure instalada nao preservou mensagem estavel.")
    if bool(slice_a[4].get("tools")) or bool(slice_a[4].get("model_calls")):
        raise VerificationError("Slice A no-tool executou modelo/tool indevidamente.")
    if not all(item.get("invocation_id") for item in slice_a[:3]):
        raise VerificationError("Slice A perdeu invocation_id em ferramenta executada.")
    if not all("duration_ms" in item and "output_chars" in item for item in slice_a):
        raise VerificationError("Slice A nÃ£o produziu measurement mÃ­nimo.")


def _validate_slice_c_payload(payload: Mapping[str, Any]) -> None:
    shell = payload.get("slice_c")
    expected_ids = [
        "installed-slice-c:c1_history",
        "installed-slice-c:c2_unsupported",
        "installed-slice-c:c3_failure",
    ]
    if not isinstance(shell, list) or len(shell) != 3:
        raise VerificationError("Probe instalado nÃ£o executou os cenÃ¡rios Slice C.")
    if [item.get("task_id") for item in shell] != expected_ids:
        raise VerificationError("Probe instalado nÃ£o preservou a identidade dos cenÃ¡rios Slice C.")
    if shell[0].get("terminal_outcome") != "SUCCESS" or "initial" not in str(shell[0].get("answer", "")):
        raise VerificationError("Slice C C1 nÃ£o consumiu o histórico real.")
    if shell[1].get("terminal_outcome") == "SUCCESS":
        raise VerificationError("Slice C C2 executou capability removida.")
    if shell[2].get("terminal_outcome") == "SUCCESS":
        raise VerificationError("Slice C C3 nÃ£o preservou failure.")
    if not shell[0].get("invocation_id") or not shell[2].get("invocation_id"):
        raise VerificationError(f"Slice C perdeu invocation_id: {shell!r}")
    if not all("duration_ms" in item and "output_chars" in item for item in shell):
        raise VerificationError("Slice C nÃ£o reutilizou measurement mÃ­nimo.")


def _validate_slice_b_payload(payload: Mapping[str, Any]) -> None:
    modify = payload.get("slice_b")
    expected_ids = [
        "installed-slice-b:b1_modify_validate",
        "installed-slice-b:b2_validation_failure",
        "installed-slice-b:b3_writer_bypass",
        "installed-slice-b:b4_denied_modify",
        "installed-slice-b:b5_preview_blocked",
    ]
    if not isinstance(modify, list) or len(modify) != 5:
        raise VerificationError("Probe instalado nao executou os cinco cenarios Slice B.")
    if [item.get("task_id") for item in modify] != expected_ids:
        raise VerificationError("Probe instalado nÃ£o preservou a identidade dos cenÃ¡rios Slice B.")
    if modify[0].get("terminal_outcome") != "SUCCESS" or modify[0].get("before") == modify[0].get("after"):
        raise VerificationError("Slice B B1 nÃ£o produziu modificaÃ§Ã£o validada.")
    if modify[1].get("terminal_outcome") == "SUCCESS" or "validada" in str(modify[1].get("answer", "")).casefold():
        raise VerificationError("Slice B B2 publicou sucesso validado apÃ³s failure.")
    if modify[2].get("terminal_outcome") == "SUCCESS" or modify[2].get("before") != modify[2].get("after"):
        raise VerificationError("Slice B B3 manteve writer direto model-actionable.")
    if modify[3].get("terminal_outcome") == "SUCCESS":
        raise VerificationError("Slice B B4 alterou alvo fora da autoridade.")
    if modify[4].get("terminal_outcome") == "SUCCESS" or modify[4].get("before") != modify[4].get("after"):
        raise VerificationError("Slice B B5 aplicou preview bloqueado.")
    _validate_slice_b_invocations(modify[0])
    _validate_slice_b_invocations(modify[1])
    if not all("duration_ms" in item and "output_chars" in item for item in modify):
        raise VerificationError("Slice B nÃ£o reutilizou measurement mÃ­nimo.")


def _validate_slice_b_invocations(item: Mapping[str, Any]) -> None:
    invocations = item.get("invocations")
    if not isinstance(invocations, list) or len(invocations) != 2:
        raise VerificationError(f"Slice B nÃ£o projetou modification+validation: {item!r}")
    if invocations[0].get("invocation_id") == invocations[1].get("invocation_id"):
        raise VerificationError("Slice B reutilizou invocation_id entre modificaÃ§Ã£o e validaÃ§Ã£o.")


def _validate_slice_d_payload(payload: Mapping[str, Any]) -> None:
    extension = payload.get("slice_d")
    expected_ids = [
        "installed-slice-d:d1_success",
        "installed-slice-d:d3_denied",
        "installed-slice-d:d4_failure",
    ]
    if not isinstance(extension, list) or [item.get("task_id") for item in extension] != expected_ids:
        raise VerificationError("Probe instalado nao executou os cenarios Slice D.")
    success, denied, failure = extension
    if success.get("terminal_outcome") != "SUCCESS" or not success.get("spawned") or "D1_EXTERNAL_EVIDENCE" not in str(success.get("answer", "")):
        raise VerificationError("Slice D D1 nao provou processo externo e consumo pelo modelo.")
    if denied.get("terminal_outcome") == "SUCCESS" or denied.get("spawned") or not denied.get("answer"):
        raise VerificationError("Slice D D3 nao negou antes do efeito externo.")
    if failure.get("terminal_outcome") == "SUCCESS" or not failure.get("spawned") or not failure.get("answer"):
        raise VerificationError("Slice D D4 publicou sucesso indevido.")
    if not all(item.get("invocation_id") for item in extension):
        raise VerificationError("Slice D perdeu invocation_id externo.")
    if not all("duration_ms" in item and "output_chars" in item for item in extension):
        raise VerificationError("Slice D nao reutilizou measurement minimo.")


def _verify_extension_aware_bootstrap(
    venv_python: Path,
    app_home: Path,
    workspace: Path,
    project_root: Path,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    result = _run(
        "installed-extension-aware-bootstrap",
        (
            str(venv_python),
            "-I",
            "-c",
            EXTENSION_BOOTSTRAP_PROBE_SOURCE,
            str(app_home),
            str(workspace),
            str(project_root),
        ),
        cwd=cwd,
        environment=environment,
    )
    payload = parse_json_output(result)
    if payload.get("tool") != "wheel_tool":
        raise VerificationError("Wheel nÃ£o publicou o descriptor da extension.")
    if payload.get("cwd_ok") is not True:
        raise VerificationError("Adapter instalado recebeu cwd incorreto.")
    if payload.get("builtins") is not True:
        raise VerificationError("Bootstrap instalado perdeu builtins.")
    if payload.get("process_calls") != []:
        raise VerificationError("Bootstrap instalado iniciou subprocesso.")
    if payload.get("checkout_import") is not False:
        raise VerificationError("Bootstrap instalado importou o checkout.")


def _verify_f1_installed(
    entrypoint: Path,
    app_home: Path,
    workspace: Path,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    """Exercise canonical CLI administration, authority and a real stdio child."""

    f1_home = app_home.parent / "f1-app-home"
    f1_workspace = workspace.parent / "f1-workspace"
    extension_dir = workspace.parent / "f1-extension"
    marker = workspace.parent / "f1-extension-spawned.txt"
    f1_workspace.mkdir()
    extension_dir.mkdir()
    (extension_dir / "tool.py").write_text(
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('spawned', encoding='utf-8')\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'invocation_id': payload['invocation_id'], 'status': 'succeeded', 'message': 'F1_EXTERNAL_EVIDENCE'}), flush=True)\n",
        encoding="utf-8",
    )
    manifest = extension_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "wheel.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["${python}", "${extension_dir}/tool.py"],
                "timeout_seconds": 5,
                "tools": [
                    {
                        "name": "wheel_tool",
                        "description": "installed F1 tool",
                        "schema": {},
                        "capabilities": ["process"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _F1ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    f1_environment = dict(environment)
    f1_environment["LLM_AGENT_API_URL"] = (
        f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    )
    common = ("--home", str(f1_home), "--workspace", str(f1_workspace))
    try:
        registered = _run(
            "f1-installed-register",
            (str(entrypoint), "extensions", "register", str(manifest), *common, "--json"),
            cwd=cwd,
            environment=f1_environment,
        )
        if parse_json_output(registered).get("extension_id") != "wheel.extension":
            raise VerificationError("F1 instalada nao registrou a extension no catalogo moderno.")
        _run(
            "f1-installed-enable",
            (str(entrypoint), "extensions", "enable", "wheel.extension", *common, "--json"),
            cwd=cwd,
            environment=f1_environment,
        )
        _run(
            "f1-installed-grant",
            (str(entrypoint), "extensions", "grant", "wheel.extension", "process", *common, "--json"),
            cwd=cwd,
            environment=f1_environment,
        )
        _run(
            "f1-installed-config-init",
            (str(entrypoint), "config", "init", "--home", str(f1_home)),
            cwd=cwd,
            environment=f1_environment,
        )
        if marker.exists():
            marker.unlink()
        denied = _run_expected_failure(
            "f1-installed-yes-without-authority",
            (
                str(entrypoint),
                "run",
                "--json",
                "--yes",
                *common,
                "Use wheel_tool sem task authority",
            ),
            cwd=cwd,
            environment=f1_environment,
        )
        denied_payload = parse_json_output(denied)
        if denied_payload.get("success") is True or marker.exists():
            raise VerificationError("F1 instalada permitiu --yes substituir task authority.")

        succeeded = _run(
            "f1-installed-run-with-authority",
            (
                str(entrypoint),
                "run",
                "--json",
                "--yes",
                "--task-authority",
                "process",
                *common,
                "Use wheel_tool with F1_EXTERNAL_EVIDENCE",
            ),
            cwd=cwd,
            environment=f1_environment,
        )
        succeeded_payload = parse_json_output(succeeded)
        if succeeded_payload.get("success") is not True:
            raise VerificationError("F1 instalada nao concluiu a jornada com authority explicita.")
        if "F1_EXTERNAL_EVIDENCE" not in json.dumps(succeeded_payload, ensure_ascii=False):
            raise VerificationError("F1 instalada nao consumiu o resultado estruturado do processo.")
        if not marker.exists():
            raise VerificationError("F1 instalada nao iniciou o processo stdio real.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _verify_version(result: CommandResult) -> None:
    if not re.search(r"\b\d+\.\d+\.\d+\b", result.stdout):
        raise VerificationError(f"--version não informou versão semântica: {result.stdout!r}")


def _verify_config(app_home: Path) -> None:
    config_file = app_home / "config" / "config.json"
    if not config_file.is_file():
        raise VerificationError(f"config init não criou {config_file}")
    try:
        document = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError("config init criou JSON inválido.") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise VerificationError("config init não criou configuração schema_version=1.")


def _verify_greeting(payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).casefold()
    if not any(term in rendered for term in ("olá", "ola", "ajudar")):
        raise VerificationError("run headless não retornou a resposta trivial esperada.")
    if payload.get("success") is False or payload.get("ok") is False:
        raise VerificationError("run headless reportou falha.")


def verify_installed_package(
    project_root: Path = ROOT,
    python: Path = Path(sys.executable),
    *,
    no_build_isolation: bool = False,
    offline_diagnostic: bool = False,
) -> None:
    project_root = project_root.resolve()
    mode = installation_mode(offline_diagnostic)
    with tempfile.TemporaryDirectory(prefix="llm-agent-installed-") as raw_temp:
        temp = Path(raw_temp)
        wheel_dir = temp / "wheel"
        environment_dir = temp / "venv"
        external_cwd = temp / "outside-checkout"
        workspace = temp / "workspace"
        app_home = temp / "app-home"
        wheel_dir.mkdir()
        external_cwd.mkdir()
        workspace.mkdir()
        sentinel = external_cwd / "sentinel.txt"
        (workspace / "notes.txt").write_text(
            "SLICE_A1_EVIDENCE: nota permitida.\n", encoding="utf-8"
        )
        (workspace / "facts.md").write_text(
            "SLICE_A2_EVIDENCE: dado encontrado.\n", encoding="utf-8"
        )
        (temp / "outside.txt").write_text(
            "outside-secret-must-not-be-read\n", encoding="utf-8"
        )
        probe_script = external_cwd / "installed_probe.py"
        sample = workspace / "sample.py"
        sentinel.write_text("outside-workspace-sentinel\n", encoding="utf-8")
        probe_script.write_text(INSTALLED_PROBE_SOURCE, encoding="utf-8")
        sample.write_text(
            "def evaluate(expression: str) -> object:\n"
            "    return eval(expression)\n",
            encoding="utf-8",
        )
        _prepare_local_history_workspace(workspace, sample)

        wheel = _build_wheel(
            project_root,
            wheel_dir,
            python.resolve(),
            no_build_isolation=no_build_isolation,
        )
        cwd_before = snapshot_tree(external_cwd)
        workspace_before = snapshot_tree(workspace)
        venv_python, entrypoint = _install_wheel(
            wheel,
            environment_dir,
            external_cwd,
            mode,
        )
        runtime_environment = _runtime_environment(app_home)
        site_packages = _site_packages(
            venv_python,
            external_cwd,
            runtime_environment,
        )
        site_before = snapshot_tree(site_packages)
        _verify_declared_dependencies(
            venv_python,
            site_packages,
            external_cwd,
            runtime_environment,
            mode,
        )
        _verify_installed_probe(
            venv_python,
            workspace,
            sentinel,
            app_home / "probe-scratch",
            probe_script,
            runtime_environment,
        )
        _verify_extension_aware_bootstrap(
            venv_python,
            temp / "extension-app-home",
            temp / "extension-workspace",
            project_root,
            external_cwd,
            runtime_environment,
        )
        _verify_f1_installed(
            entrypoint,
            app_home,
            workspace,
            external_cwd,
            runtime_environment,
        )
        _verify_import_origin(
            venv_python,
            site_packages,
            external_cwd,
            runtime_environment,
        )

        results: dict[str, CommandResult] = {}
        for name, command in installed_cli_commands(entrypoint, workspace):
            results[name] = _run(
                name,
                command,
                cwd=external_cwd,
                environment=runtime_environment,
            )

        _verify_version(results["version"])
        _verify_config(app_home)
        parse_json_output(results["doctor"])
        _verify_greeting(parse_json_output(results["run"]))
        if snapshot_tree(external_cwd) != cwd_before:
            raise VerificationError("A CLI escreveu no diretório externo de execução.")
        if snapshot_tree(workspace) != workspace_before:
            raise VerificationError("O artefato instalado modificou o workspace no probe.")
        if snapshot_tree(site_packages) != site_before:
            raise VerificationError("A CLI escreveu no site-packages após a instalação.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--no-build-isolation",
        action="store_true",
        help="usa build requirements já instalados (útil em ambientes sem rede)",
    )
    parser.add_argument(
        "--offline-diagnostic",
        action="store_true",
        help=(
            "reutiliza dependências do Python base e instala o wheel sem resolvê-las; "
            "diagnóstico local mais fraco, não é um gate de aceitação"
        ),
    )
    arguments = parser.parse_args(argv)
    try:
        verify_installed_package(
            arguments.project_root,
            arguments.python,
            no_build_isolation=arguments.no_build_isolation,
            offline_diagnostic=arguments.offline_diagnostic,
        )
    except VerificationError as exc:
        print(f"Installed package verification failed: {exc}", file=sys.stderr)
        _emit_failure_annotation(str(exc))
        return 1
    if arguments.offline_diagnostic:
        print(
            "Offline installed-package diagnostic passed "
            "(dependency completeness was not verified; this is not an acceptance gate)."
        )
    else:
        print("Installed package clean acceptance passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
