from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.application import AgentApplication
from agent.evaluation import FeedbackService, FeedbackTarget, FeedbackVerdict
from agent.outputs.models import (
    OutputContentPolicy,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
)
from agent.outputs.service import OutputService
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.storage_contracts import (
    MaintenanceConfirmation,
    MaintenanceOperation,
    StorageMaintenanceError,
)
from agent.runtime.storage_maintenance import StorageMaintenanceService
from tests.support.offline_scenarios import OfflineChatGateway


def test_feedback_is_revisioned_and_reset_restore_is_lease_safe_and_hash_validated(
    tmp_path: Path,
) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("workspace remains unchanged", encoding="utf-8")
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineChatGateway("unused"),
        configure_logging=False,
    )
    workspace_id = application.workspace.workspace_id
    try:
        result = application.run("oi")
        assert result.snapshot is not None
        technical_receipt = dict(result.receipt)
        target = FeedbackTarget.from_run_result(result)
        feedback_service = application.feedback_service()
        first = feedback_service.submit(target, FeedbackVerdict.PARTIAL, comment="revisar")
        first_snapshot = first.to_dict()
        second = feedback_service.correct(
            first.feedback_id,
            expected_revision=1,
            verdict=FeedbackVerdict.CORRECT,
            comment="corrigido",
        )
        assert first.to_dict() == first_snapshot
        assert second.revision == 2
        assert feedback_service.history(first.feedback_id) == (first, second)
        assert result.receipt == technical_receipt

        output_service = application.output_service()
        publication = output_service.publish(
            OutputPublishRequest(
                kind=OutputKind.TEXT,
                source=OutputSource.WORKSPACE_QUERY,
                title="terminal output",
                text="z" * 8_001,
                content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
                action_id="query.read",
                metadata={"result_status": "succeeded"},
            )
        )
        assert publication.artifact is not None
        with pytest.raises(StorageMaintenanceError) as raised:
            StorageMaintenanceService(paths).reset(
                MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir))
            )
        assert raised.value.reason_code == "MAINTENANCE_HOME_ACTIVE"
    finally:
        application.close()

    maintenance = StorageMaintenanceService(paths)
    backup = maintenance.reset(
        MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir))
    )
    assert not paths.home_dir.exists()
    paths.home_dir.mkdir()
    with pytest.raises(StorageMaintenanceError) as occupied:
        maintenance.restore(
            backup.backup_id,
            MaintenanceConfirmation(
                MaintenanceOperation.RESTORE,
                str(paths.home_dir),
                backup.backup_id,
            ),
        )
    assert occupied.value.reason_code == "MAINTENANCE_RESTORE_TARGET_EXISTS"
    paths.home_dir.rmdir()
    restored = maintenance.restore(
        backup.backup_id,
        MaintenanceConfirmation(
            MaintenanceOperation.RESTORE,
            str(paths.home_dir),
            backup.backup_id,
        ),
    )
    assert restored.restored_home == paths.home_dir
    assert paths.storage_layout_file.exists()

    restored_paths = paths.for_workspace(workspace_id)
    restored_output = OutputService(restored_paths)
    assert publication.artifact is not None
    restored_artifact = restored_output.metadata(publication.artifact.output_id)
    restored_payload = restored_output.store.read(restored_artifact.output_id)
    assert restored_artifact.payload_sha256 == hashlib.sha256(
        restored_payload.encode("utf-8")
    ).hexdigest()
    restored_feedback = FeedbackService(restored_paths)
    assert [item.revision for item in restored_feedback.history(first.feedback_id)] == [1, 2]
    assert source.read_text(encoding="utf-8") == "workspace remains unchanged"

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineChatGateway("unused"),
        configure_logging=False,
    ) as restarted:
        assert restarted.paths.storage_layout_file.exists()
        assert restarted.feedback_service().latest(first.feedback_id).revision == 2
        assert restarted.output_service().metadata(publication.artifact.output_id).output_id == publication.artifact.output_id
