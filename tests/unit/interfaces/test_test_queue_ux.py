"""User-visible queue preparation, progress and independent-result explanations."""
from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import requests

from llm_agent.agent.runtime.config_repository import packaged_config_defaults
from llm_agent.application.test_queue import QueueProgress, TestQueue
from llm_agent.application.test_queue_inspection import case_details, inspect_queue
from llm_agent.application.test_queue_manifest import discover_cases
from llm_agent.application.test_queue_runtime import configuration_preview
from llm_agent.interfaces.test_queue import main, menu
from llm_agent.interfaces.test_queue_output import html_report, safe_text
from tests.unit.interfaces.test_test_queue import make_queue, successful


def test_check_is_read_only_and_cannot_run_model_or_verifier(tmp_path, monkeypatch, capsys):
    queue = make_queue(tmp_path, checks=False)
    before = (queue.root / "state.json").read_bytes()
    def never(*args, **kwargs):
        pytest.fail("local inspection must not execute a process or contact a model")
    monkeypatch.setattr(subprocess, "Popen", never)
    monkeypatch.setattr(requests.sessions.Session, "request", never)
    assert main(["check", str(queue.root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["without_checks"] == ["01-first", "02-second", "03-third"]
    assert document["counts"] == {"pending": 3}
    assert (queue.root / "state.json").read_bytes() == before


def test_import_reports_skipped_directories(tmp_path, capsys):
    queue = make_queue(tmp_path)
    (tmp_path / "tasks" / "unfinished-task").mkdir()
    other = TestQueue(tmp_path / "other")
    other.create(tmp_path / "tasks")
    assert main(["check", str(other.root)]) == 0
    output = capsys.readouterr().out
    assert "unfinished-task" in output
    assert "sem task.md" in output
    assert queue.snapshot()["status"] == "ready"


def test_progress_observes_durable_state_and_cannot_mutate_event(tmp_path):
    queue = make_queue(tmp_path)
    observed = []
    def observer(event: QueueProgress):
        assert queue.snapshot()["results"][event.case_id]["status"] == event.status
        with pytest.raises(FrozenInstanceError):
            event.status = "verified"
        observed.append((event.case_id, event.status))
    state = queue.run(successful, identity="A", on_progress=observer)
    assert state["status"] == "completed"
    assert observed == [(case_id, status) for case_id in state["order"] for status in ("running", "verified")]


def test_broken_progress_sink_cannot_change_test_outcomes(tmp_path, caplog):
    queue = make_queue(tmp_path)
    def broken(event):
        raise OSError("console unavailable")
    state = queue.run(successful, identity="A", on_progress=broken)
    assert all(row["status"] == "verified" for row in state["results"].values())
    assert "observer failed" in caplog.text


def test_details_distinguish_claimed_success_and_external_failure(tmp_path, capsys):
    queue = make_queue(tmp_path)
    queue.run(lambda *args: {"status": "completed", "success": True}, identity="A")
    assert main(["details", str(queue.root), "01-first"]) == 0
    output = capsys.readouterr().out
    assert "Sucesso declarado pelo runtime: sim" in output
    assert "Critério reprovado: text_equals em result.txt" in output
    assert "Reprovado" in output


def test_html_escapes_model_output_and_is_standalone(tmp_path):
    queue = make_queue(tmp_path)
    def execute(*args):
        return {**successful(*args), "answer": '<script>alert("x")</script><img src="https://remote.invalid/x">'}
    queue.run(execute, identity="A")
    html = html_report(inspect_queue(queue, include_details=True))
    assert "<script>" not in html
    assert "<img " not in html
    assert "&lt;script&gt;" in html
    assert "default-src &#" not in html
    assert "default-src 'none'" in html
    assert '<html lang="pt-BR">' in html
    assert "Dados técnicos e evidências" in html


def test_report_export_keeps_queue_inputs_and_blocks_test_artifact_overwrite(tmp_path, capsys):
    queue = make_queue(tmp_path)
    before = (queue.root / "state.json").read_bytes()
    assert main(["report", str(queue.root), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    assert Path(report).is_file()
    assert (queue.root / "state.json").read_bytes() == before
    artifact = queue.root / "cases" / "01-first" / "workspace" / "page.html"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("original")
    assert main(["report", str(queue.root), "--output", str(artifact), "--json"]) == 2
    assert artifact.read_text() == "original"


def test_expanded_evidence_ignores_arbitrary_path_in_state(tmp_path):
    queue = make_queue(tmp_path)
    queue.run(lambda *args: {**successful(*args), "answer": "z" * 40000}, identity="A")
    outside = tmp_path / "private.json"
    outside.write_text('{"secret": "must not be read"}')
    state = queue.snapshot()
    state["results"]["01-first"]["evidence_path"] = str(outside)
    queue.save(state)
    row = case_details(queue, "01-first")
    assert row["execution"]["answer"] == "z" * 40000
    assert "secret" not in row


def test_menu_reuses_queue_path_in_same_session(tmp_path, monkeypatch, capsys):
    queue = make_queue(tmp_path)
    responses = iter(["3", str(queue.root), "7", "", "0"])
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return next(responses)
    monkeypatch.setattr("builtins.input", answer)
    assert menu() == 0
    assert any(str(queue.root) in prompt for prompt in prompts)
    assert "nenhum teste, verificador ou modelo foi executado" in capsys.readouterr().out


def test_configuration_preview_resolves_model_without_gateway(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(packaged_config_defaults()))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("contacted model"))
    result = configuration_preview(config, None, automatic_approval=False, case_seconds=300, previous_identity="other")
    assert result["resume_compatible"] is False
    assert result["model"]
    assert result["task_budgets"]["max_model_calls"] <= 20
    assert "credential" not in json.dumps(result)


def test_invalid_checks_list_has_context_instead_of_attribute_error(tmp_path):
    case = tmp_path / "one"
    case.mkdir()
    (case / "task.md").write_text("do something")
    (case / "checks.json").write_text('{"checks": {}}')
    (case / "verify.py").write_text("assert True")
    with pytest.raises(ValueError, match="one/checks.json"):
        discover_cases(tmp_path)


def test_terminal_control_sequences_are_visible_text():
    assert safe_text("answer\x1b[2J\x00") == "answer\\u001b[2J\\u0000"


def test_schema_validation_does_not_depend_on_working_directory(monkeypatch):
    from llm_agent.application import test_queue_manifest as manifest
    monkeypatch.setattr(Path, "cwd", lambda: pytest.fail("schema must not consult cwd"))
    monkeypatch.setattr(manifest, "assert_no_link_ancestors", lambda *a: pytest.fail("schema must not inspect filesystem"))
    case = manifest.parse_case({"id": "one", "prompt": "do it", "files": {"src/one.py": "x = 1"},
                                "checks": [{"kind": "exists", "path": "src/one.py"}]})
    assert case.case_id == "one"


@pytest.mark.parametrize("name", ["CON", "NUL.txt", "a./x", "a /x", "../x", "\\outside"])
def test_invalid_windows_paths_are_rejected_before_effects(name):
    from llm_agent.application.test_queue_manifest import relative_path
    with pytest.raises(ValueError):
        relative_path(name)


def test_html_prioritizes_failures_without_changing_queue_order(tmp_path):
    queue = make_queue(tmp_path)
    def execute(case, *args):
        return successful(case, *args) if case.case_id == "01-first" else {"status": "completed", "success": True}
    queue.run(execute, identity="A")
    document = inspect_queue(queue, include_details=True)
    html = html_report(document)
    assert html.index("02-second — Reprovado") < html.index("01-first — Verificado")
    assert document["cases"][0]["id"] == "01-first"
    assert queue.snapshot()["order"][0] == "01-first"


@pytest.mark.parametrize("action", ["status", "check"])
def test_json_output_is_one_machine_readable_document(tmp_path, capsys, action):
    queue = make_queue(tmp_path)
    assert main([action, str(queue.root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"pending": 3}


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_run_shows_progress_or_single_json_without_provider(tmp_path, monkeypatch, capsys, json_mode):
    from llm_agent.application import test_queue_runtime as runtime
    queue = make_queue(tmp_path)
    monkeypatch.setattr(runtime, "prepare_config", lambda *a: {})
    monkeypatch.setattr(runtime, "runtime_identity", lambda *a, **k: "scripted-A")
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: successful)
    args = ["run", str(queue.root), "--config", "unused.json", "--live"]
    if json_mode:
        args += ["--json"]
    assert main(args) == 0
    output = capsys.readouterr().out
    if json_mode:
        assert json.loads(output)["counts"] == {"verified": 3}
    else:
        assert "[1/3] 01-first: Em execução" in output
        assert "[3/3] 03-third: Verificado" in output
