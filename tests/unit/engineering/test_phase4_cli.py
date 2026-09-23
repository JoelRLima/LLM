from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.interfaces.cli.app import main
from agent.interfaces.cli.parser import build_parser


def test_parser_exposes_only_w20a_test_commands() -> None:
    parser = build_parser()
    for command in ("list", "describe", "history", "result", "run"):
        args = ["test", command]
        if command == "describe":
            args.append("acceptance.installed-package")
        if command == "result":
            args.append("engr-" + "1" * 32)
        parsed = parser.parse_args(args)
        assert parsed.command == "test" and parsed.test_command == command


def test_list_json_is_one_document_and_does_not_create_home(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "absent"
    assert main(["test", "list", "--json", "--home", str(home)]) == 0
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["operations"][0]["operation_id"] == "acceptance.installed-package"
    assert not home.exists() and output.out.count("\n") == 1


def test_describe_known_unavailable_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("agent.engineering.cli.discover_source_repository_context", lambda: None)
    assert main(["test", "describe", "acceptance.installed-package", "--json", "--home", str(tmp_path / "home")]) == 0
    assert json.loads(capsys.readouterr().out)["unavailable_reason"] == "ENGINEERING_SOURCE_REPOSITORY_REQUIRED"


def test_history_absent_store_is_empty_without_creation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert main(["test", "history", "--json", "--home", str(home)]) == 0
    assert json.loads(capsys.readouterr().out) == {"runs": [], "schema_version": 1}
    assert not home.exists()


def test_result_absent_store_is_blocked_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "result", "engr-" + "1" * 32, "--json", "--home", str(tmp_path / "home")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENGINEERING_RUN_NOT_FOUND"


@pytest.mark.parametrize("raw", ["[]", "{", '{"a":1,"a":2}', '"x"', '{"x":"' + "a" * 16_384 + '"}'])
def test_invalid_params_json_is_usage_error_without_home(
    raw: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    assert (
        main(["test", "run", "acceptance.installed-package", "--params-json", raw, "--json", "--home", str(home)]) == 2
    )
    capsys.readouterr()
    assert not home.exists()


def test_headless_missing_operation_is_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "run", "--json", "--home", str(tmp_path / "home")]) == 2
    capsys.readouterr()


def test_source_workspace_is_usage_error_without_creation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert (
        main(
            ["test", "run", "acceptance.installed-package", "--workspace", str(tmp_path), "--json", "--home", str(home)]
        )
        == 2
    )
    capsys.readouterr()
    assert not home.exists()


def test_permission_precedence_allocates_no_home(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert main(["test", "run", "acceptance.installed-package", "--json", "--home", str(home)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENGINEERING_EXTERNAL_NOT_AUTHORIZED"
    assert not home.exists()
    assert main(["test", "run", "acceptance.installed-package", "--allow-external", "--json", "--home", str(home)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENGINEERING_NETWORK_NOT_AUTHORIZED"
    assert not home.exists()
