from __future__ import annotations

from pathlib import Path

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.evaluation import FeedbackTarget, FeedbackVerdict
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.variants.models import VariantComposition
from llm_agent.application.services.queries import ReadOnlyWorkspaceQueryService
from llm_agent.outputs.service import OutputService
from llm_agent.workspace.paths import AppHomeOrigin, AppPaths
from tests.support.offline_scenarios import OfflineChatGateway


def _initialized_paths(root: Path) -> AppPaths:
    paths = AppPaths.discover(root / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


def test_agent_facade_and_product_services_keep_separate_owners(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = workspace / "sentinel.txt"
    sentinel.write_text("source workspace remains untouched", encoding="utf-8")
    source_files_before = tuple(
        sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
    )
    paths = _initialized_paths(tmp_path)
    expected_composition = VariantComposition.production_current()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineChatGateway("unused"),
        configure_logging=False,
    ) as application:
        assert paths.home_origin is AppHomeOrigin.ARGUMENT
        assert paths.storage_layout_file.exists()
        assert application.variant_composition == expected_composition
        assert application.variant_fingerprint == expected_composition.fingerprint
        assert application.orchestrator.tool_invocation_gateway is application.tool_invocation_gateway

        assert application.feedback_service() is application.feedback_service()
        query_service = ReadOnlyWorkspaceQueryService(application.workspace)
        output_service = OutputService(application.workspace_paths)
        assert query_service.workspace is application.workspace

        # External composition of Product services must not create a second
        # storage tree in the user workspace.
        source_files_after_services = tuple(
            sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
        )
        assert source_files_after_services == source_files_before

        result = application.run("oi")
        assert result.snapshot is not None
        assert result.snapshot.correlation.run_id
        target = FeedbackTarget.from_run_result(result)
        record = application.feedback_service().submit(target, FeedbackVerdict.CORRECT)
        assert record.revision == 1
        assert output_service.list() == ()

    assert not application._home_lease.lease_path.exists()
    assert not application.workspace_paths.lock_file.exists()
    assert sentinel.read_text(encoding="utf-8") == "source workspace remains untouched"
