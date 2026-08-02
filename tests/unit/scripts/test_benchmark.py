from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import benchmark


class _Application:
    def __init__(self, results_file: Path, *, success: bool = True) -> None:
        self.workspace_paths = SimpleNamespace(benchmark_results_file=results_file)
        self.orchestrator = SimpleNamespace(
            verbose=False,
            agent_state=SimpleNamespace(tool_history=[], last_result=None),
        )
        self.success = success
        self.closed = False
        self.objectives: list[str] = []

    def run(self, objective: str) -> Any:
        self.objectives.append(objective)
        return SimpleNamespace(
            success=self.success,
            answer=f"resposta: {objective}",
            error=None if self.success else "falha",
        )

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


def test_run_task_uses_application_boundary() -> None:
    application = _Application(Path("unused.json"))

    result = benchmark.run_task(application, "objetivo")

    assert result["success"] is True
    assert result["answer_preview"] == "resposta: objetivo"
    assert result["steps"] == 0
    assert application.objectives == ["objetivo"]


def test_run_benchmark_writes_workspace_scoped_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result_file = tmp_path / "state" / "benchmark_results.json"
    application = _Application(result_file)

    written = benchmark.run_benchmark(application, tasks=("primeira", "segunda"))

    document = json.loads(written.read_text(encoding="utf-8"))
    assert written == result_file
    assert document["summary"]["total_tasks"] == 2
    assert document["summary"]["successful_tasks"] == 2
    assert [item["objective"] for item in document["results"]] == ["primeira", "segunda"]
    assert str(result_file) in capsys.readouterr().out


def test_run_benchmark_does_not_reuse_application_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application(tmp_path / "result.json")
    objectives: list[str] = []

    def timed_out(_: Any, objective: str) -> dict[str, Any]:
        objectives.append(objective)
        return {
            "objective": objective,
            "success": False,
            "steps": 0,
            "elapsed_seconds": 120.0,
            "timed_out": True,
            "errored": False,
            "error_message": "timeout",
            "answer_preview": "",
        }

    monkeypatch.setattr(benchmark, "run_task", timed_out)

    benchmark.run_benchmark(application, tasks=("primeira", "não executar"))

    assert objectives == ["primeira"]


def test_main_forwards_standalone_options_and_closes_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _Application(tmp_path / "result.json")
    captured: dict[str, Any] = {}

    def create(args: Any) -> _Application:
        captured.update(vars(args))
        return application

    monkeypatch.setattr(benchmark, "_create_application", create)
    monkeypatch.setattr(benchmark, "run_benchmark", lambda app: tmp_path / "result.json")

    code = benchmark.main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--home",
            str(tmp_path / "home"),
            "--config",
            str(tmp_path / "config.json"),
            "--profile",
            "local",
        ]
    )

    assert code == 0
    assert captured == {
        "workspace": str(tmp_path / "workspace"),
        "home": str(tmp_path / "home"),
        "config": str(tmp_path / "config.json"),
        "profile": "local",
    }
    assert application.closed is True


def test_main_maps_missing_configuration_to_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_: Any) -> Any:
        raise FileNotFoundError("configuração ausente")

    monkeypatch.setattr(benchmark, "_create_application", fail)

    assert benchmark.main([]) == 2
    assert "configuração ausente" in capsys.readouterr().err
