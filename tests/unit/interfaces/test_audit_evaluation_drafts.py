import json

import pytest

from llm_agent.application.evaluation_case_draft import prepare_case_draft, write_case_draft
from llm_agent.application.test_queue_manifest import discover_cases
from llm_agent.interfaces.test_queue import main


def inputs(tmp_path):
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"checks": [{"kind": "text_equals", "path": "result.txt", "expected": "approved"}]}))
    evidence = tmp_path / "trace.jsonl"
    evidence.write_text('{"prompt":"private source content","answer":"model claim"}\n')
    return checks, evidence


def test_draft_is_previewed_and_keeps_only_evidence_digest(tmp_path, capsys):
    checks, evidence = inputs(tmp_path)
    output = tmp_path / "tasks" / "regression"
    args = ["draft", str(output), "--objective", "Write result.txt", "--checks", str(checks), "--evidence", str(evidence), "--json"]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["written"] is False
    assert not output.exists()
    assert "model claim" not in json.dumps(preview)
    assert "private source content" not in json.dumps(preview)
    assert main([*args, "--write-reviewed"]) == 0
    case = discover_cases(output.parent)[0]
    assert case.prompt.strip() == "Write result.txt"
    assert case.checks[0]["expected"] == "approved"
    assert "private source content" not in (output / "source.json").read_text()
    assert main([*args, "--write-reviewed"]) == 2


def test_draft_requires_external_checks_and_rejects_credentials(tmp_path):
    checks, evidence = inputs(tmp_path)
    with pytest.raises(ValueError, match="credenciais"):
        prepare_case_draft(case_id="case", objective="password=private", checks_path=checks, evidence=evidence)
    checks.write_text('{"checks": []}')
    with pytest.raises(ValueError, match="critério"):
        prepare_case_draft(case_id="case", objective="write result", checks_path=checks, evidence=evidence)


def test_draft_never_overwrites_an_existing_directory(tmp_path):
    checks, evidence = inputs(tmp_path)
    preview = prepare_case_draft(case_id="case", objective="write result", checks_path=checks, evidence=evidence)
    with pytest.raises(FileExistsError):
        write_case_draft(tmp_path, preview)
    assert evidence.exists()
