"""Packaged WebView2 composition tests without sockets or model inference."""

import contextlib
import io
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.services.queries import WorkspaceQueryKind, WorkspaceQueryRequest
from llm_agent.interfaces.workbench.webview import create_session, parse_arguments
from llm_agent.workspace.paths import AppPaths


class EndpointTests(unittest.TestCase):
    def test_entry_point_accepts_real_session_options_and_rejects_fixtures(self):
        args = parse_arguments(["--workspace", ".", "--home", "./agent-home", "--profile", "existing"])
        self.assertEqual(args.profile, "existing")
        self.assertEqual(args.workspace, Path("."))
        for forbidden in ("--demo", "--offline-fixture", "--demo-rounds"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_arguments([forbidden])

    def test_existing_config_is_loaded_lazily_and_local_queries_need_no_runtime(self):
        repository = Path(__file__).resolve().parents[2]
        root = repository / (".workbench-package-test-" + uuid4().hex)
        root.mkdir()
        try:
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "offline-notes.txt").write_text("offline query evidence\n", encoding="utf-8")
            paths = AppPaths.discover(root / "home", env={})
            ConfigRepository(paths).initialize()

            session, selected_workspace = create_session(workspace=workspace, home=paths.home_dir)
            try:
                self.assertEqual(selected_workspace, workspace.resolve())
                self.assertIsNone(session._runtime)
                availability = session.availability_snapshot()
                self.assertTrue(availability["backend"]["configured"])
                self.assertEqual(availability["backend"]["connectivity"], "unverified")

                observation, reference = session.query(
                    WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {"path": "."})
                ).result(timeout=5)
                self.assertEqual(observation["status"], "succeeded")
                self.assertIn("offline-notes.txt", session.read(reference).result(timeout=5).text)
                self.assertIsNone(session._runtime)
            finally:
                session.close().result(timeout=5)
        finally:
            resolved = root.resolve()
            if resolved.parent != repository:
                raise AssertionError("test scratch path escaped the repository root")
            shutil.rmtree(resolved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
