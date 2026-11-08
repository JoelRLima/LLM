"""Shared local discovery and preparation of test queues; no interface imports."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.context import AppPaths
from llm_agent.application.test_queue_manifest import read_document
from llm_agent.application.test_queue_service import TestQueueService
from llm_agent.filesystem.path_safety import assert_no_link_ancestors
from llm_agent.process.instance_lock import InstanceLock
from llm_agent.storage.json_persistence import write_json_atomic


class TestQueueLibrary:
    __test__ = False

    def __init__(self, paths: AppPaths) -> None:
        self.root = paths.global_dir / "test_queues"
        assert_no_link_ancestors(self.root)
        self.index = self.root / "recent.json"

    @staticmethod
    def identity(path: Path) -> str:
        return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()

    def recent(self) -> list[dict[str, str]]:
        if not self.index.exists():
            return []
        document = read_document(self.index)
        if document.get("schema_version") != 1 or not isinstance(document.get("items"), list):
            raise ValueError("Invalid recent test queue index")
        items = []
        for item in document["items"][:8]:
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "path", "label")):
                continue
            path = Path(item["path"])
            if path.is_absolute() and item["id"] == self.identity(path):
                items.append({key: item[key] for key in ("id", "path", "label")})
        return items

    def _remember(self, service: TestQueueService, label: str) -> str:
        queue_id = self.identity(service.root)
        self.root.mkdir(parents=True, exist_ok=True)
        with InstanceLock.create(self.root / "index.lock", create_parent=False):
            items = [item for item in self.recent() if item["id"] != queue_id]
            items.insert(0, {"id": queue_id, "path": str(service.root), "label": label[:200]})
            write_json_atomic(self.index, {"schema_version": 1, "items": items[:8]})
        return queue_id

    def create(self, source: Path) -> str:
        service = TestQueueService(self.root / ("queue-" + uuid.uuid4().hex))
        service.create(source)
        return self._remember(service, source.name or "Testes")

    def open(self, root: Path) -> str:
        service = TestQueueService(root)
        service.inspect()
        return self._remember(service, service.root.name)

    def resolve(self, queue_id: str) -> TestQueueService:
        if not isinstance(queue_id, str) or len(queue_id) != 64:
            raise ValueError("Invalid queue identity")
        item = next((item for item in self.recent() if item["id"] == queue_id), None)
        if item is None:
            raise ValueError("Queue selection expired; open the queue folder again")
        return TestQueueService(Path(item["path"]))


def queue_configuration_options(paths: AppPaths, config_path: str | Path | None,
                                profile: str | None) -> dict[str, Any]:
    repository = ConfigRepository(paths, config_path=config_path)
    config = repository.load(overrides={"default_model_profile": profile} if profile else None).to_dict()
    profiles = config.get("model_profiles", {})
    return {"config_path": str(repository.path), "selected_profile": profile or config.get("default_model_profile"),
            "profiles": [{"name": name, "provider": value.get("provider"), "model": value.get("model")}
                         for name, value in list(profiles.items())[:100] if isinstance(value, dict)]}
