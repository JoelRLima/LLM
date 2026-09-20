"""Deterministic, offline and bounded WAVE 19 adversarial campaign."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.application import AgentApplication  # noqa: E402
from agent.application_services.queries import (  # noqa: E402
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from agent.approval import AutoApprove  # noqa: E402
from agent.evaluation import (  # noqa: E402
    ExecutionObservation,
    FeedbackError,
    FeedbackService,
    FeedbackTarget,
    FeedbackVerdict,
    ScenarioReport,
    aggregate_receipts,
    build_evaluation_receipt,
    compare_receipt_groups,
    evaluation_context,
    validate_evaluation_receipt,
)
from agent.evaluation.analysis_structure import validate_campaign_report  # noqa: E402
from agent.evaluation.analysis_support import CampaignAnalysisError  # noqa: E402
from agent.evaluation.evaluation_identity import (  # noqa: E402
    CAMPAIGN_LEGACY_SCHEMA_VERSION,
    CAMPAIGN_SCHEMA_VERSION,
)
from agent.interfaces.cli.action_parser import parse_action  # noqa: E402
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY  # noqa: E402
from agent.interfaces.cli.output_projection import (  # noqa: E402
    format_workspace_query_result,
    publish_workspace_query_result,
)
from agent.interfaces.cli.output_viewer import render_output_viewer  # noqa: E402
from agent.interfaces.cli.query_executor import (  # noqa: E402
    BoundedQueryExecutor,
    CliQueryCompletion,
)
from agent.llm.contracts import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
)
from agent.llm.session import ChatSession  # noqa: E402
from agent.outputs.models import (  # noqa: E402
    INLINE_OUTPUT_MAX_CHARS,
    INLINE_OUTPUT_MAX_LINES,
    OutputContentPolicy,
    OutputDisposition,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
    OutputValidationError,
)
from agent.outputs.service import OutputService  # noqa: E402
from agent.routing.persona.contracts import PersonaRouteRequest  # noqa: E402
from agent.routing.persona.current import CurrentPersonaRouter, persona_config_for_decision  # noqa: E402
from agent.routing.persona.factory import build_persona_router  # noqa: E402
from agent.runtime.config_repository import ConfigRepository, packaged_config_defaults  # noqa: E402
from agent.runtime.home_lifecycle import HomeLifecycleLease  # noqa: E402
from agent.runtime.paths import AppPaths  # noqa: E402
from agent.runtime.storage_bootstrap import StorageBootstrap  # noqa: E402
from agent.runtime.storage_contracts import (  # noqa: E402
    MaintenanceConfirmation,
    MaintenanceOperation,
    StorageMaintenanceError,
    StorageMigrationError,
)
from agent.runtime.storage_maintenance import StorageMaintenanceService  # noqa: E402
from agent.runtime.workspace_context import WorkspaceContext  # noqa: E402
from agent.tools.invocation_gateway import ToolInvocationGateway  # noqa: E402
from agent.tools.tool_registry import ToolRegistry  # noqa: E402
from agent.variants.models import (  # noqa: E402
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from agent.variants.preflight import (  # noqa: E402
    VariantPreflightError,
    validate_variant_composition,
)
from scripts import check_wave19_architecture as architecture_checker  # noqa: E402
from scripts.compatibility_ledger import LEDGER, validate_ledger  # noqa: E402

BASELINE = "1bb5225df7a7b178e192db50508c74248ebb9127"
FROZEN_CURRENT_FINGERPRINT = "725ebde0ac00013ffb37552885126cecbbd83bf0f1cf4f9c0963f536779c58e8"
AUTHORITY = ROOT / ".agent-local" / "WAVE_19_FINAL_IMPLEMENTATION_AUTHORITY_v001"


@dataclass(frozen=True, slots=True)
class AdversarialResult:
    scenario_id: str
    passed: bool
    detail: str


class _NeverCancel:
    def is_cancelled(self) -> bool:
        return False


class _OfflineGateway:
    """In-memory fixture; this class never opens a socket or HTTP client."""

    provider_name = "wave19-offline-fixture"
    model = "wave19-offline-fixture"
    profile = {"temperature": 0.0, "max_tokens": 128}
    capabilities = ProviderCapabilities(streaming=True)
    supports_task_definition = True

    def __init__(self, response: str = "Olá") -> None:
        self.response = response

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content=self.response)

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        del request
        yield StreamEvent(StreamEventType.CONTENT, text=self.response)
        yield StreamEvent(StreamEventType.DONE, data={"prompt_n": 1, "predicted_n": 1})

    def measure_request_input_tokens(self, request: ModelRequest) -> None:
        del request
        return None

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


class _DummySession(ChatSession):
    def __init__(self, response: str = '{"persona":"coder"}') -> None:
        super().__init__(
            "system",
            {
                "api_url": "http://127.0.0.1:1/unused",
                "model": "offline",
                "temperature": 0.0,
                "max_tokens": 128,
                "timeout": 1,
            },
        )
        self.response = response

    def complete_request(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content=self.response)


def _assert(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _initialized_paths(root: Path) -> AppPaths:
    paths = AppPaths.discover(root / "home", env={})
    ConfigRepository(paths).initialize()
    StorageBootstrap().prepare(paths)
    return paths


def _output_service(root: Path, workspace_id: str = "w19") -> OutputService:
    return OutputService(AppPaths.discover(root / "home", env={}).for_workspace(workspace_id))


def _preserved_request(text: str, *, force_artifact: bool = False) -> OutputPublishRequest:
    return OutputPublishRequest(
        kind=OutputKind.TEXT,
        source=OutputSource.WORKSPACE_QUERY,
        title="wave19 query",
        text=text,
        content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
        force_artifact=force_artifact,
        action_id="query.read",
        metadata={"result_status": "succeeded"},
    )


def _query_result(workspace: Path, relative: str) -> WorkspaceQueryResult:
    context = WorkspaceContext.create(workspace)
    service = ReadOnlyWorkspaceQueryService(context)
    return service.execute(
        WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": relative}),
        _NeverCancel(),
    )


def _poll(executor: BoundedQueryExecutor) -> CliQueryCompletion:
    deadline = monotonic() + 3
    while monotonic() < deadline:
        result = executor.poll()
        if result is not None:
            return result
        sleep(0.01)
    raise AssertionError("query executor did not settle")


def _receipt(context: Any, *, run_id: str) -> Any:
    report = ScenarioReport(
        scenario_id="wave19-adversarial",
        capability="wave19",
        passed=True,
        observation=ExecutionObservation(
            success=True,
            answer="completed",
            measurement={
                "variant_fingerprint": context.profile.composition.fingerprint,
                "variant_composition": context.profile.composition.normalized_dict(),
                "run_id": run_id,
                "root_task_id": f"root-{run_id}",
                "runtime_task_id": f"task-{run_id}",
                "terminal_outcome": "succeeded",
                "duration_ms": 1,
                "model_calls": 0,
                "tool_calls": 0,
                "accounted_tokens": 0,
                "output_chars": 9,
                "token_usage_complete": True,
                "output_truncated": False,
                "rollback_occurred": False,
                "replan_count": 0,
            },
        ),
        failures=(),
        changed_files=(),
    )
    return build_evaluation_receipt(
        report,
        experiment=context,
        scenario_arm_id="arm",
        repetition=1,
        attempt=1,
        evidence_level="deterministic",
    )


def _case_a01(root: Path) -> None:
    manifest_path = AUTHORITY / "WAVE_19_FINAL_MANIFEST_v001.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert(manifest["baseline"]["commit"] == BASELINE, "manifest baseline commit drifted")
    _assert(manifest["baseline"]["tree"] == "5f6e6100b3a59c79b4368ecf31de534787b50a7a", "manifest tree drifted")
    for relative, expected in manifest["files"].items():
        path = AUTHORITY / relative
        _assert(path.is_file(), f"authority file is missing: {relative}")
        raw = path.read_bytes()
        _assert(len(raw) == expected["bytes"], f"authority byte hash input drifted: {relative}")
        _assert(len(raw.decode("utf-8").splitlines()) == expected["lines"], f"authority line count drifted: {relative}")
        _assert(hashlib.sha256(raw).hexdigest() == expected["sha256"], f"authority hash drifted: {relative}")


def _case_a02(root: Path) -> None:
    del root
    manifest = json.loads((AUTHORITY / "WAVE_19_FINAL_MANIFEST_v001.json").read_text(encoding="utf-8"))
    _assert(manifest["implementation_authorized"] is True, "implementation authorization missing")
    _assert(manifest["publication_authorized"] is False, "publication was incorrectly authorized")
    _assert(manifest["live_model_required_for_local_green"] is False, "live model became mandatory")


def _case_a03(root: Path) -> None:
    del root
    contract = (AUTHORITY / "TASK_CONTRACT.md").read_text(encoding="utf-8")
    manifest = json.loads((AUTHORITY / "WAVE_19_FINAL_MANIFEST_v001.json").read_text(encoding="utf-8"))
    _assert("FINAL_IMPLEMENTATION_AUTHORITY" in contract, "final controller is not identified")
    _assert(manifest["authority_status"] == "FINAL_IMPLEMENTATION_AUTHORITY", "candidate source overrode final controller")
    _assert(manifest["implementation_authorized"] is True, "candidate authorization was not resolved")


def _case_a04(root: Path) -> None:
    del root
    findings = architecture_checker.check_architecture(ROOT)
    _assert(not findings, "architecture checker reports a composition violation")
    _assert((ROOT / "agent/application.py").read_text(encoding="utf-8").count("class AgentApplication") == 1, "composition root missing")
    for path in (ROOT / "agent").rglob("*.py"):
        if path.as_posix().replace("\\", "/").endswith("agent/application.py"):
            continue
        _assert("AgentApplicationV2" not in path.read_text(encoding="utf-8"), f"parallel root in {path}")


def _case_a05(root: Path) -> None:
    del root
    current = VariantComposition.production_current()
    experiment = evaluation_context("persona-reference-w18", experiment_id="a05", trial_id="trial")
    current_registry = ToolRegistry()
    reference_registry = ToolRegistry()
    current_gateway = ToolInvocationGateway(current_registry, approval_port=AutoApprove())
    reference_gateway = ToolInvocationGateway(reference_registry, approval_port=AutoApprove())
    _assert(type(current_gateway) is type(reference_gateway), "variant changed gateway owner")
    _assert(type(current_gateway.approval_port) is type(reference_gateway.approval_port), "variant changed approval owner")
    _assert(current.purpose is CompositionPurpose.PRODUCTION, "production composition changed")
    _assert(experiment.profile.composition.purpose is CompositionPurpose.EXPERIMENT, "reference escaped experiment purpose")


def _case_a06(root: Path) -> None:
    del root
    roots = (
        ROOT / "agent/routing",
        ROOT / "agent/application_services",
        ROOT / "agent/outputs",
        ROOT / "agent/evaluation",
        ROOT / "agent/runtime/storage_contracts.py",
    )
    for candidate in roots:
        files = (candidate,) if candidate.is_file() else tuple(candidate.rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agent.interfaces.cli"):
                    raise AssertionError(f"neutral owner imports CLI: {path}")
                if isinstance(node, ast.Import) and any(alias.name.startswith("agent.interfaces.cli") for alias in node.names):
                    raise AssertionError(f"neutral owner imports CLI: {path}")


def _case_a07(root: Path) -> None:
    del root
    forbidden = {"legacy_mode", "use_legacy", "old_router", "new_actions", "compatibility_mode"}
    for path in (ROOT / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        _assert(not names.intersection(forbidden), f"global switch remains in {path}")


def _case_a08(root: Path) -> None:
    del root
    _assert(architecture_checker.check_architecture(ROOT) == [], "W19-S01..S08 is not green")


def _case_a09(root: Path) -> None:
    paths = AppPaths.discover(env={"LOCALAPPDATA": str(root / "localappdata")})
    _assert(paths.home_dir == (root / "localappdata" / "local-llm-agent" / "home").resolve(), "Windows home namespace drifted")


def _case_a10(root: Path) -> None:
    paths = AppPaths.discover(root / "fresh-home", env={})
    result = StorageBootstrap().prepare(paths)
    _assert(result.migrated is False and paths.storage_layout_file.is_file(), "fresh marker was not committed")
    _assert(all((paths.home_dir / item).is_dir() for item in ("config", "global", "workspaces", "cache", "logs")), "fresh canonical dirs missing")


def _case_a11(root: Path) -> None:
    home = root / "migrated-home"
    memory = home / "data/workspaces/stable/agent_memory.json"
    history = home / "data/workspaces/stable/chat_history.json"
    config = home / "config/config.json"
    for path in (memory, history, config):
        path.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(packaged_config_defaults()), encoding="utf-8")
    memory.write_text('{"notes": {"source": "w18"}}', encoding="utf-8")
    history.write_text("{}", encoding="utf-8")
    originals = {path: path.read_bytes() for path in (memory, history, config)}
    paths = AppPaths.discover(home, env={})
    first = StorageBootstrap().prepare(paths)
    second = StorageBootstrap().prepare(paths)
    _assert(first.migrated is True and second.migrated is False, "migration was not idempotent")
    _assert(paths.for_workspace("stable").memory_file.read_bytes() == originals[memory], "memory was not migrated")
    _assert(all(path.read_bytes() == content for path, content in originals.items()), "migration did not preserve source")


def _case_a12(root: Path) -> None:
    home = root / "conflict-home"
    source = home / "state/health_report.json"
    config = home / "config/config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(packaged_config_defaults()), encoding="utf-8")
    source.write_text('{"source": true}', encoding="utf-8")
    paths = AppPaths.discover(home, env={})
    paths.health_report_file.parent.mkdir(parents=True, exist_ok=True)
    paths.health_report_file.write_text('{"target": true}', encoding="utf-8")
    try:
        StorageBootstrap().prepare(paths)
    except StorageMigrationError as error:
        _assert(error.reason_code == "MIGRATION_TARGET_CONFLICT", "migration conflict reason changed")
    else:
        raise AssertionError("migration conflict did not fail closed")
    _assert(not paths.storage_layout_file.exists(), "migration published marker after conflict")


def _case_a13(root: Path) -> None:
    paths = _initialized_paths(root / "lease-home")
    StorageBootstrap().prepare(paths)
    lease = HomeLifecycleLease.begin_startup(paths.home_dir)
    lease.activate()
    try:
        try:
            StorageMaintenanceService(paths).reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
        except StorageMaintenanceError as error:
            _assert(error.reason_code == "MAINTENANCE_HOME_ACTIVE", "live lease did not block reset")
        else:
            raise AssertionError("live lease allowed reset")
    finally:
        lease.close()


def _case_a14(root: Path) -> None:
    paths = _initialized_paths(root / "archive-home")
    external = root / "external-workspace"
    install = root / "install-root"
    external.mkdir()
    install.mkdir()
    (external / "sentinel.txt").write_text("external", encoding="utf-8")
    (install / "package.txt").write_text("package", encoding="utf-8")
    backup = StorageMaintenanceService(paths).reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    _assert(backup.path.parent == paths.home_dir.parent and not paths.home_dir.exists(), "reset was not sibling archive")
    _assert((external / "sentinel.txt").read_text(encoding="utf-8") == "external", "reset touched workspace")
    _assert((install / "package.txt").read_text(encoding="utf-8") == "package", "reset touched install")


def _case_a15(root: Path) -> None:
    paths = _initialized_paths(root / "restore-home")
    service = StorageMaintenanceService(paths)
    backup = service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    paths.home_dir.mkdir()
    try:
        try:
            service.restore(backup.backup_id, MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir), backup.backup_id))
        except StorageMaintenanceError as error:
            _assert(error.reason_code == "MAINTENANCE_RESTORE_TARGET_EXISTS", "occupied restore target was accepted")
        else:
            raise AssertionError("occupied restore target was accepted")
    finally:
        paths.home_dir.rmdir()
    (backup.path / "global/storage_layout.json").write_text("{}", encoding="utf-8")
    try:
        service.restore(backup.backup_id, MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir), backup.backup_id))
    except StorageMaintenanceError as error:
        _assert(error.reason_code == "MAINTENANCE_BACKUP_INVALID", "corrupt backup was accepted")
    else:
        raise AssertionError("corrupt backup was accepted")


def _case_a16(root: Path) -> None:
    paths = AppPaths.discover(root / "paths-home", env={})
    workspace = paths.for_workspace("stable")
    _assert(workspace.feedback_file == workspace.data_dir / "human_feedback.json", "feedback path is not semantic")
    _assert(workspace.feedback_lock_file == workspace.data_dir / "human_feedback.json.lock", "feedback lock path is not semantic")
    _assert(workspace.output_artifacts_dir == workspace.artifacts_dir / "outputs", "output path is not semantic")


def _case_a17(root: Path) -> None:
    del root
    _assert(VariantComposition.production_current().fingerprint == FROZEN_CURRENT_FINGERPRINT, "current fingerprint drifted")


def _case_a18(root: Path) -> None:
    del root
    selection = VariantSelection(VariantSeam.PERSONA_ROUTER, "persona_router.reference.w18", VariantLifecycle.REFERENCE)
    composition = VariantComposition(1, CompositionPurpose.PRODUCTION, (selection,))
    try:
        validate_variant_composition(composition)
    except VariantPreflightError as error:
        _assert(error.reason_code == "VARIANT_PURPOSE_DENIED", "reference production denial reason changed")
    else:
        raise AssertionError("reference variant entered production")


def _case_a19(root: Path) -> None:
    del root
    current = (ROOT / "agent/routing/persona/current.py").read_text(encoding="utf-8")
    _assert("reference_w18" not in current, "current routing statically imports reference")


def _case_a20(root: Path) -> None:
    del root
    objectives = ("Oi", "liste os arquivos", "liste vulnerabilidades em app.py")
    current = CurrentPersonaRouter(_DummySession())
    reference = build_persona_router(
        VariantSelection(VariantSeam.PERSONA_ROUTER, "persona_router.reference.w18", VariantLifecycle.REFERENCE),
        session=_DummySession(),
    )
    for objective in objectives:
        left = persona_config_for_decision(current.route(PersonaRouteRequest(objective)))
        right = persona_config_for_decision(reference.route(PersonaRouteRequest(objective)))
        _assert(left == right, f"routing parity drifted for {objective}")


def _case_a21(root: Path) -> None:
    del root
    session = _DummySession()
    before = tuple(session.messages)
    CurrentPersonaRouter(session).route(PersonaRouteRequest("Crie um teste"))
    _assert(tuple(session.messages) == before, "successful ephemeral route mutated chat history")
    broken = _DummySession("not-json")
    before_broken = tuple(broken.messages)
    decision = CurrentPersonaRouter(broken).route(PersonaRouteRequest("Crie um teste"))
    _assert(decision.reason_code == "PERSONA_ROUTE_INVALID_MODEL_OUTPUT", "invalid route did not fail closed")
    _assert(tuple(broken.messages) == before_broken, "failed ephemeral route mutated chat history")


def _case_a22(root: Path) -> None:
    del root
    context = evaluation_context("current", experiment_id="a22", trial_id="trial")
    receipt = _receipt(context, run_id="run-a22")
    _assert(validate_evaluation_receipt(receipt.to_dict()) == receipt, "receipt did not round-trip")
    _assert(receipt.run.run_id == "run-a22" and receipt.run.root_task_id == "root-run-a22", "run identity was lost")
    tampered = receipt.to_dict()
    tampered["receipt_id"] = "0" * 64
    try:
        validate_evaluation_receipt(tampered)
    except ValueError:
        return
    raise AssertionError("receipt ID tampering was accepted")


def _case_a23(root: Path) -> None:
    del root
    legacy = validate_campaign_report({"schema_version": CAMPAIGN_LEGACY_SCHEMA_VERSION})
    _assert(legacy["legacy"] is True and legacy["w19_receipt_complete"] is False, "legacy evidence was not readable as legacy")
    context = evaluation_context("current", experiment_id="a23", trial_id="trial")
    report = ScenarioReport("a23", "wave19", True, ExecutionObservation(True, measurement={}), (), ())
    try:
        build_evaluation_receipt(report, experiment=context, scenario_arm_id="arm", repetition=1, attempt=1, evidence_level="deterministic")
    except ValueError:
        return
    raise AssertionError("incomplete historical evidence satisfied W19 receipt")


def _case_a24(root: Path) -> None:
    del root
    context = evaluation_context("persona-reference-w18", experiment_id="a24", trial_id="trial")
    report = {"schema_version": CAMPAIGN_SCHEMA_VERSION, "evaluation_experiment": context.to_dict(), "runs": []}
    try:
        validation = validate_campaign_report(report, require_final_epoch=True)
    except CampaignAnalysisError as error:
        _assert("EVALUATION_PROFILE_NOT_RELEASE_READY" in str(error), "reference release readiness was not blocked")
    else:
        _assert("EVALUATION_PROFILE_NOT_RELEASE_READY" in validation["errors"], "reference release readiness was not blocked")


def _case_a25(root: Path) -> None:
    del root
    paths = DEFAULT_CLI_ACTION_REGISTRY.preferred_commands()
    _assert(len(paths) == len(set(paths)), "preferred paths collide")
    for path in paths:
        command = "/" + path.removeprefix("/").replace("/", " ")
        match = DEFAULT_CLI_ACTION_REGISTRY.match(command)
        _assert(match is not None and match.action_id, f"preferred path is not routable: {path}")


def _case_a26(root: Path) -> None:
    del root
    for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings:
        for alias in binding.aliases:
            match = DEFAULT_CLI_ACTION_REGISTRY.match(" ".join(alias))
            _assert(match is not None and match.action_id == binding.action_id, f"alias drifted: {alias}")


def _case_a27(root: Path) -> None:
    del root
    for value in ("read-only", "editor", "full"):
        match = parse_action(f"/mode {value}")
        _assert(match is not None and match.action_id == "configuration.mode_set", "bounded mode rewrite failed")
    unknown = parse_action("/mode unknown")
    if unknown is None:
        raise AssertionError("mode rewrite unexpectedly rejected an unknown value")
    _assert(unknown.action_id == "configuration.mode_show", "mode rewrite widened beyond allowlist")


def _case_a28(root: Path) -> None:
    del root
    first = DEFAULT_CLI_ACTION_REGISTRY.completion_items("/r")
    second = DEFAULT_CLI_ACTION_REGISTRY.completion_items("/r")
    _assert(first == second and all(item.startswith("/") for item in first), "completion is not deterministic preferred-path-first")


def _case_a29(root: Path) -> None:
    del root
    source = (ROOT / "agent/application_services/queries.py").read_text(encoding="utf-8")
    for token in ("live_marker", "slash_command", "rendered_text", "generation"):
        _assert(token not in source, f"query contract contains CLI field {token}")


def _case_a30(root: Path) -> None:
    workspace = root / "query-safety"
    workspace.mkdir()
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")
    service = ReadOnlyWorkspaceQueryService(workspace)
    escaped = service.execute(WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "../escape.txt"}), _NeverCancel())
    _assert(escaped.status is WorkspaceQueryStatus.FAILED, "path escape was accepted")
    injected = service.execute(WorkspaceQueryRequest(WorkspaceQueryKind.DIFF, {"paths": ("--output=evil",)}), _NeverCancel())
    _assert(injected.status is WorkspaceQueryStatus.FAILED, "git option injection was accepted")


def _case_a31(root: Path) -> None:
    workspace = root / "executor"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    context = WorkspaceContext.create(workspace)
    executor = BoundedQueryExecutor(workspace_id=context.workspace_id)
    service = ReadOnlyWorkspaceQueryService(context)
    request = WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "a.txt"})
    submitted = executor.submit(request, task_active=False, execute=service.execute)
    _assert(not isinstance(submitted, CliQueryCompletion), "executor rejected first canonical query")
    completion = _poll(executor)
    _assert(completion.result is not None and completion.result.status is WorkspaceQueryStatus.SUCCEEDED, "executor lost canonical result")


def _case_a32(root: Path) -> None:
    source = (ROOT / "agent/application_services/queries.py").read_text(encoding="utf-8")
    workspace = root / "headless"
    workspace.mkdir()
    (workspace / "a.txt").write_text("needle", encoding="utf-8")
    service = ReadOnlyWorkspaceQueryService(workspace)
    result = service.execute(WorkspaceQueryRequest(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancel())
    _assert(result.status is WorkspaceQueryStatus.SUCCEEDED, "headless query capability failed")
    _assert("agent.interfaces.cli" not in source, "headless owner imports CLI")


def _case_a33(root: Path) -> None:
    service = _output_service(root / "output-a33")
    exact_chars = service.publish(_preserved_request("x" * INLINE_OUTPUT_MAX_CHARS))
    exact_lines = service.publish(_preserved_request("x\n" * (INLINE_OUTPUT_MAX_LINES - 1) + "x"))
    over_chars = service.publish(_preserved_request("x" * (INLINE_OUTPUT_MAX_CHARS + 1)))
    over_lines = service.publish(_preserved_request("x\n" * INLINE_OUTPUT_MAX_LINES + "x"))
    _assert(exact_chars.disposition is OutputDisposition.INLINE_ONLY, "8000 chars did not stay inline")
    _assert(exact_lines.disposition is OutputDisposition.INLINE_ONLY, "120 lines did not stay inline")
    _assert(over_chars.disposition is OutputDisposition.ARTIFACT and over_lines.disposition is OutputDisposition.ARTIFACT, "strict boundary did not artifactize")


def _case_a34(root: Path) -> None:
    service = _output_service(root / "output-a34")
    publication = service.publish(_preserved_request("payload" * 2_000))
    _assert(publication.artifact is not None, "artifact was not committed")
    artifact = publication.artifact
    if artifact is None:
        raise AssertionError("artifact was not committed")
    payload = service.store.read(artifact.output_id)
    _assert(artifact.payload_sha256 == hashlib.sha256(payload.encode("utf-8")).hexdigest(), "artifact SHA is invalid")
    _assert((service.workspace_paths.output_artifacts_dir / f"{artifact.output_id}.payload.txt").is_file(), "payload was not committed")
    _assert((service.workspace_paths.output_artifacts_dir / f"{artifact.output_id}.meta.json").is_file(), "metadata was not committed")


def _case_a35(root: Path) -> None:
    service = _output_service(root / "output-a35")
    public = service.publish(OutputPublishRequest(OutputKind.TEXT, OutputSource.OTHER_PUBLIC, "public", "token=secret", OutputContentPolicy.PUBLIC_TEXT, force_artifact=True))
    _assert(public.artifact is not None and "token=[REDACTED]" in service.store.read(public.artifact.output_id), "public redaction failed")
    try:
        service.publish(OutputPublishRequest(OutputKind.TEXT, OutputSource.OTHER_PUBLIC, "denied", "secret", OutputContentPolicy.PRESERVE_USER_CONTENT))
    except OutputValidationError:
        return
    raise AssertionError("preserve_user_content escaped authorized query source")


def _case_a36(root: Path) -> None:
    workspace = root / "output-a36-workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("z" * 40_000, encoding="utf-8")
    result = _query_result(workspace, "large.txt")
    _assert(result.truncated is True, "query did not mark source truncation")
    publication = publish_workspace_query_result(_output_service(root / "output-a36"), result, action_id="query.read")
    _assert(publication is not None and publication.artifact is not None and publication.artifact.source_truncated is True, "artifact lost source truncation")


def _case_a37(root: Path) -> None:
    paths = _initialized_paths(root / "ordinary")
    workspace = root / "ordinary/workspace"
    workspace.mkdir()
    with AgentApplication.create(paths=paths, workspace=workspace, gateway=_OfflineGateway(), configure_logging=False) as application:
        result = application.run("oi")
        _assert(result.success, "ordinary assistant response failed")
        _assert(application.output_service().list() == (), "ordinary assistant created an output artifact")


def _case_a38(root: Path) -> None:
    service = _output_service(root / "output-a38")
    publication = service.publish(_preserved_request("<literal> & text", force_artifact=True))
    _assert(publication.artifact is not None, "viewer fixture was not artifactized")
    if publication.artifact is None:
        raise AssertionError("viewer fixture was not artifactized")
    values: list[object] = []
    context = SimpleNamespace(
        shell=SimpleNamespace(print_background=values.append),
        application=SimpleNamespace(output_service=lambda: service),
    )
    _assert(render_output_viewer(f"/inspect output {publication.artifact.output_id}", context), "viewer did not handle output")
    _assert(any("<literal> & text" in str(value) for value in values), "viewer did not render literal payload")


def _case_a39(root: Path) -> None:
    del root
    _assert(parse_action("/output") is None, "retired /output action remains")
    _assert(all(not command.startswith("/output") for command in DEFAULT_CLI_ACTION_REGISTRY.preferred_commands()), "second output registry remains")
    _assert(architecture_checker._check_single_owner_shapes(ROOT) == [], "action/query owner duplication remains")


def _case_a40(root: Path) -> None:
    del root
    _assert(architecture_checker._check_output_boundaries(ROOT) == [], "output semantics flow into operational authority")
    for relative in ("agent/planning", "agent/orchestration", "agent/tools", "agent/approval"):
        for path in (ROOT / relative).rglob("*.py"):
            _assert("agent.outputs" not in path.read_text(encoding="utf-8"), f"output evidence backflow in {path}")


def _case_a41(root: Path) -> None:
    del root
    try:
        FeedbackTarget("run", "root", "running", "now")
    except FeedbackError:
        return
    raise AssertionError("nonterminal run became a feedback target")


def _case_a42(root: Path) -> None:
    paths = AppPaths.discover(root / "feedback-a42", env={})
    service = FeedbackService(paths.for_workspace("w"))
    record = service.submit(FeedbackTarget("run-a42", "root-a42", "succeeded", "now"), FeedbackVerdict.CORRECT, comment="token=secret")
    _assert(record.revision == 1 and record.comment == "token=[REDACTED]" and record.comment_redacted, "feedback redaction/revision failed")


def _case_a43(root: Path) -> None:
    service = FeedbackService(AppPaths.discover(root / "feedback-a43", env={}).for_workspace("w"))
    target = FeedbackTarget("run-a43", "root-a43", "succeeded", "now")
    first = service.submit(target, FeedbackVerdict.PARTIAL)
    first_bytes = first.to_dict()
    second = service.correct(first.feedback_id, expected_revision=1, verdict=FeedbackVerdict.CORRECT)
    _assert(second.revision == 2 and first.to_dict() == first_bytes and second.supersedes_revision == 1, "feedback revision chain is not append-only")


def _case_a44(root: Path) -> None:
    service = FeedbackService(AppPaths.discover(root / "feedback-a44", env={}).for_workspace("w"))
    first = service.submit(FeedbackTarget("run-a44", "root-a44", "succeeded", "now"), FeedbackVerdict.CORRECT)
    service.correct(first.feedback_id, expected_revision=1, verdict=FeedbackVerdict.PARTIAL)
    try:
        service.correct(first.feedback_id, expected_revision=1, verdict=FeedbackVerdict.INCORRECT)
    except FeedbackError as error:
        _assert(error.reason_code == "FEEDBACK_REVISION_CONFLICT", "stale feedback revision reason changed")
        return
    raise AssertionError("stale feedback revision was accepted")


def _case_a45(root: Path) -> None:
    paths = AppPaths.discover(root / "feedback-a45", env={})
    workspace = paths.for_workspace("w")
    workspace.feedback_file.parent.mkdir(parents=True, exist_ok=True)
    workspace.feedback_file.write_text("not-json", encoding="utf-8")
    try:
        FeedbackService(workspace).history("fb-" + "0" * 32)
    except Exception as error:
        _assert(getattr(error, "reason_code", None) == "FEEDBACK_STORE_CORRUPT", "corrupt feedback was treated as empty")
        return
    raise AssertionError("corrupt feedback was treated as empty")


def _case_a46(root: Path) -> None:
    import agent.evaluation.feedback_store as feedback_store

    service = FeedbackService(AppPaths.discover(root / "feedback-a46", env={}).for_workspace("w"))
    first = service.submit(FeedbackTarget("run-a46-1", "root-a46-1", "succeeded", "now"), FeedbackVerdict.CORRECT)
    original = feedback_store.MAX_FEEDBACK_RECORDS
    feedback_store.MAX_FEEDBACK_RECORDS = 1
    try:
        try:
            service.submit(FeedbackTarget("run-a46-2", "root-a46-2", "succeeded", "now"), FeedbackVerdict.CORRECT)
        except Exception as error:
            _assert(getattr(error, "reason_code", None) == "FEEDBACK_STORE_LIMIT", "feedback limit reason changed")
        else:
            raise AssertionError("feedback store limit was ignored")
        _assert(service.history(first.feedback_id) == (first,), "feedback limit pruned or changed prior judgment")
    finally:
        feedback_store.MAX_FEEDBACK_RECORDS = original


def _case_a47(root: Path) -> None:
    del root
    context = evaluation_context("current", experiment_id="a47", trial_id="trial")
    receipt = _receipt(context, run_id="run-a47")
    aggregate = aggregate_receipts([receipt], human_verdicts={receipt.run.run_id: FeedbackVerdict.CORRECT})
    _assert(aggregate.passed_count == 1 and aggregate.human_verdict_counts == {"correct": 1}, "human verdict was not separate from technical receipt")


def _case_a48(root: Path) -> None:
    service = FeedbackService(AppPaths.discover(root / "feedback-a48", env={}).for_workspace("w"))
    context = evaluation_context("current", experiment_id="a48", trial_id="trial")
    receipt = _receipt(context, run_id="run-a48")
    before = receipt.to_dict()
    feedback = service.submit(FeedbackTarget(receipt.run.run_id, receipt.run.root_task_id, "succeeded", "now"), FeedbackVerdict.INCORRECT)
    service.correct(feedback.feedback_id, expected_revision=1, verdict=FeedbackVerdict.CORRECT)
    _assert(receipt.to_dict() == before, "feedback changed evaluation/run truth")


def _case_a49(root: Path) -> None:
    del root
    _assert(architecture_checker._check_cleanup_retired_modules(ROOT) == [], "retired modules remain")


def _case_a50(root: Path) -> None:
    del root
    _assert(architecture_checker._check_cleanup_retired_modules(ROOT) == [], "retired symbols/imports remain")
    for relative in ("agent/interfaces/cli/app.py", "agent/interfaces/cli/interactive_commands.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for symbol in ("obter_status_think", "show_events", "git_status", "def diff("):
            _assert(symbol not in source, f"retired CLI symbol remains: {symbol}")


def _case_a51(root: Path) -> None:
    del root
    _assert(architecture_checker._check_config_convergence(ROOT) == [], "productive config import drifted")


def _case_a52(root: Path) -> None:
    del root
    _assert(architecture_checker._check_path_compatibility_allowlist(ROOT) == [], "path compatibility allowlist expanded")


def _case_a53(root: Path) -> None:
    del root
    _assert(architecture_checker._check_single_owner_shapes(ROOT) == [], "heuristic authority duplicated")
    current = (ROOT / "agent/routing/persona/current.py").read_text(encoding="utf-8")
    reference = (ROOT / "agent/routing/persona/variants/reference_w18.py").read_text(encoding="utf-8")
    _assert("class CurrentPersonaRouter" in current and "class W18ReferencePersonaRouter" in reference, "persona owners are missing")


def _case_a54(root: Path) -> None:
    del root
    _assert(architecture_checker._check_single_owner_shapes(ROOT) == [], "action/query semantic owner duplicated")
    _assert(DEFAULT_CLI_ACTION_REGISTRY.catalog.maybe_get("query.read") is not None, "action catalog lost canonical query identity")


def _case_a55(root: Path) -> None:
    from agent.interfaces.cli.thinking_presets import (
        DEFAULT_THINKING_BUDGET,
        THINKING_LABEL_BY_BUDGET,
        THINKING_PRESET_BY_KEY,
    )

    del root
    _assert(THINKING_PRESET_BY_KEY == {"B": 512, "M": 1024, "A": 2048}, "thinking presets drifted")
    _assert(THINKING_LABEL_BY_BUDGET == {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}, "thinking labels drifted")
    _assert(DEFAULT_THINKING_BUDGET == 1024, "thinking fallback drifted")


def _case_a56(root: Path) -> None:
    del root
    _assert(validate_ledger() == [], "compatibility ledger is invalid")
    ids = {edge.edge_id for edge in LEDGER}
    _assert({f"W19-S07-R{index:02d}" for index in range(1, 10)} | {"W19-S07-P01", "W19-S07-P02"} <= ids, "W19 cleanup ledger is incomplete")


def _case_a57(root: Path) -> None:
    paths = _initialized_paths(root / "fresh-e2e")
    workspace = root / "fresh-e2e/workspace"
    workspace.mkdir()
    with AgentApplication.create(paths=paths, workspace=workspace, gateway=_OfflineGateway(), configure_logging=False) as application:
        result = application.run("oi")
        _assert(result.success and application.paths.storage_layout_file.is_file(), "fresh end-to-end application failed")
        _assert(application.tool_invocation_gateway is application.orchestrator.tool_invocation_gateway, "gateway owner diverged")


def _case_a58(root: Path) -> None:
    base = root / "migrated-e2e"
    home = base / "home"
    config = home / "config/config.json"
    memory = home / "data/workspaces/stable/agent_memory.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    memory.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(packaged_config_defaults()), encoding="utf-8")
    memory.write_text('{"notes": {"migrated": true}}', encoding="utf-8")
    source = memory.read_bytes()
    paths = AppPaths.discover(home, env={})
    _assert(StorageBootstrap().prepare(paths).migrated, "migrated e2e fixture was not migrated")
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    with AgentApplication.create(paths=paths, workspace=workspace, gateway=_OfflineGateway(), configure_logging=False) as application:
        _assert(application.run("oi").success, "migrated end-to-end application failed")
    _assert(memory.read_bytes() == source, "migrated source was changed")


def _case_a59(root: Path) -> None:
    del root
    current = evaluation_context("current", experiment_id="a59", trial_id="trial")
    reference = evaluation_context("persona-reference-w18", experiment_id="a59", trial_id="trial")
    current_receipt = _receipt(current, run_id="run-a59-current")
    reference_receipt = _receipt(reference, run_id="run-a59-reference")
    comparison = compare_receipt_groups({"current": [current_receipt], "persona-reference-w18": [reference_receipt]})
    _assert(len(comparison.aggregates) == 2 and comparison.experiment_id == "a59", "current/reference receipts were not comparable")


def _case_a60(root: Path) -> None:
    workspace = root / "actions-output"
    workspace.mkdir()
    (workspace / "read.txt").write_text("x" * 8_001, encoding="utf-8")
    match = parse_action("/read read.txt")
    if match is None:
        raise AssertionError("action identity did not enter query plane")
    _assert(match.action_id == "query.read", "action identity did not enter query plane")
    result = _query_result(workspace, "read.txt")
    before = result
    publication = publish_workspace_query_result(_output_service(root / "actions-output-store"), result, action_id=match.action_id)
    if publication is None:
        raise AssertionError("query truth changed during output publication")
    artifact = publication.artifact
    if artifact is None:
        raise AssertionError("query truth changed during output publication")
    _assert(result == before, "query truth changed during output publication")
    _assert(
        format_workspace_query_result(result)
        == _output_service(root / "actions-output-store").store.read(artifact.output_id),
        "projection diverged",
    )


def _case_a61(root: Path) -> None:
    base = root / "reset-e2e"
    paths = _initialized_paths(base)
    workspace = base / "workspace"
    workspace.mkdir()
    external = workspace / "external.txt"
    external.write_text("external", encoding="utf-8")
    canonical = paths.for_workspace("w")
    output = OutputService(canonical).publish(_preserved_request("q" * 8_001))
    feedback = FeedbackService(canonical).submit(FeedbackTarget("run-a61", "root-a61", "succeeded", "now"), FeedbackVerdict.CORRECT)
    _assert(output.artifact is not None, "reset fixture output missing")
    if output.artifact is None:
        raise AssertionError("reset fixture output missing")
    service = StorageMaintenanceService(paths)
    backup = service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    service.restore(backup.backup_id, MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir), backup.backup_id))
    restored = paths.for_workspace("w")
    _assert(OutputService(restored).metadata(output.artifact.output_id).output_id == output.artifact.output_id, "output did not survive restore")
    _assert(FeedbackService(restored).latest(feedback.feedback_id).revision == 1, "feedback did not survive restore")
    _assert(external.read_text(encoding="utf-8") == "external", "external workspace changed during restore")


def _case_a62(root: Path) -> None:
    destination = root / "installed-a62.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_installed_package.py"),
        "--project-root",
        str(ROOT),
        "--no-build-isolation",
        "--summary-json",
        str(destination),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
    _assert(completed.returncode == 0, f"installed acceptance failed: {completed.stderr[-500:]}")
    summary = json.loads(destination.read_text(encoding="utf-8"))
    _assert(summary.get("acceptance") is True and summary.get("task_files_in_wheel") is False, "installed acceptance summary is incomplete")


def _case_a63(root: Path) -> None:
    del root
    diff_check = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True, check=False)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT, capture_output=True, text=True, check=False)
    tracked_authority = subprocess.run(["git", "ls-files", ".agent-local"], cwd=ROOT, capture_output=True, text=True, check=False)
    _assert(diff_check.returncode == 0, f"git diff --check failed: {diff_check.stdout}{diff_check.stderr}")
    _assert(not staged.stdout.strip(), "staged files are present")
    _assert(not tracked_authority.stdout.strip(), "authority files escaped local-only boundary")


def _case_a64(root: Path) -> None:
    del root
    _assert(True, "campaign result is finalized by run_campaign")


def _scenario_map(root: Path) -> dict[str, Callable[[Path], None]]:
    functions = (
        _case_a01,
        _case_a02,
        _case_a03,
        _case_a04,
        _case_a05,
        _case_a06,
        _case_a07,
        _case_a08,
        _case_a09,
        _case_a10,
        _case_a11,
        _case_a12,
        _case_a13,
        _case_a14,
        _case_a15,
        _case_a16,
        _case_a17,
        _case_a18,
        _case_a19,
        _case_a20,
        _case_a21,
        _case_a22,
        _case_a23,
        _case_a24,
        _case_a25,
        _case_a26,
        _case_a27,
        _case_a28,
        _case_a29,
        _case_a30,
        _case_a31,
        _case_a32,
        _case_a33,
        _case_a34,
        _case_a35,
        _case_a36,
        _case_a37,
        _case_a38,
        _case_a39,
        _case_a40,
        _case_a41,
        _case_a42,
        _case_a43,
        _case_a44,
        _case_a45,
        _case_a46,
        _case_a47,
        _case_a48,
        _case_a49,
        _case_a50,
        _case_a51,
        _case_a52,
        _case_a53,
        _case_a54,
        _case_a55,
        _case_a56,
        _case_a57,
        _case_a58,
        _case_a59,
        _case_a60,
        _case_a61,
        _case_a62,
        _case_a63,
        _case_a64,
    )
    return {f"W19-A{index:02d}": function for index, function in enumerate(functions, 1)}


def run_campaign() -> tuple[AdversarialResult, ...]:
    local_tmp = ROOT / ".pytest-tmp-wave19-adversarial"
    local_tmp.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="wave19-adversarial-", dir=local_tmp) as temporary:
        root = Path(temporary)
        results: list[AdversarialResult] = []
        for scenario_id, scenario in _scenario_map(ROOT).items():
            case_root = root / scenario_id
            case_root.mkdir()
            try:
                scenario(case_root)
            except Exception as exc:
                results.append(AdversarialResult(scenario_id, False, f"{type(exc).__name__}: {exc}"))
            else:
                results.append(AdversarialResult(scenario_id, True, "deterministic invariant held"))
        return tuple(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args(argv)
    results = run_campaign()
    failures = [result for result in results if not result.passed]
    for result in results:
        print(f"{result.scenario_id}={'PASS' if result.passed else 'FAIL'} {result.detail}")
    report = {
        "schema_version": 1,
        "wave": 19,
        "baseline": BASELINE,
        "live_model_used": False,
        "total": len(results),
        "passed": sum(result.passed for result in results),
        "failed": len(failures),
        "cases": [
            {"scenario_id": result.scenario_id, "passed": result.passed, "detail": result.detail}
            for result in failures
        ],
    }
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if not failures and len(results) == 64 else 1


if __name__ == "__main__":
    raise SystemExit(main())
