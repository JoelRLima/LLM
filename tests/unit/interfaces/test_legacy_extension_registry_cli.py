from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterator, Literal

import pytest

from llm_agent.application.context import AppPaths
from llm_agent.application.legacy_extension_registry import LegacyExtensionRegistryEntry
from llm_agent.interfaces.cli import maintenance


def _entry(
    extension_id: str,
    *,
    enabled: bool = True,
    status: Literal["not_checked", "missing", "ok"] = "not_checked",
) -> LegacyExtensionRegistryEntry:
    return LegacyExtensionRegistryEntry(
        id=extension_id,
        manifest_path=Path(f"C:/extensions/{extension_id}.json"),
        enabled=enabled,
        manifest_status=status,
        manifest_id=extension_id if status == "ok" else None,
        manifest_version="1.2.3" if status == "ok" else None,
    )


def test_legacy_tools_cli_renders_list_add_enable_disable_and_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_paths = AppPaths.discover(tmp_path / "home")
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        maintenance,
        "list_legacy_extensions",
        lambda *args, **kwargs: (_entry("one"), _entry("two", enabled=False)),
    )
    monkeypatch.setattr(
        maintenance,
        "add_legacy_extension",
        lambda *args, **kwargs: calls.append(("add", args, kwargs)),
    )
    monkeypatch.setattr(
        maintenance,
        "set_legacy_extension_enabled",
        lambda *args, **kwargs: calls.append(("set", args, kwargs)),
    )
    monkeypatch.setattr(
        maintenance,
        "doctor_legacy_extensions",
        lambda *args, **kwargs: iter((_entry("one", status="ok"), _entry("two", status="missing"))),
    )

    assert maintenance.run_tools(
        argparse.Namespace(tools_command="list", state=None),
        app_paths=app_paths,
        workspace=tmp_path,
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        "one [enabled] -> C:\\extensions\\one.json",
        "two [disabled] -> C:\\extensions\\two.json",
    ]
    assert maintenance.run_tools(
        argparse.Namespace(tools_command="add", state=None, id="new", manifest="manifest.json", disabled=True),
        app_paths=app_paths,
        workspace=tmp_path,
    ) == 0
    assert capsys.readouterr().out == "Extensão registrada: new\n"
    assert maintenance.run_tools(
        argparse.Namespace(tools_command="enable", state=None, id="one"),
        app_paths=app_paths,
        workspace=tmp_path,
    ) == 0
    assert capsys.readouterr().out == "Extensão habilitada: one\n"
    assert maintenance.run_tools(
        argparse.Namespace(tools_command="disable", state=None, id="one"),
        app_paths=app_paths,
        workspace=tmp_path,
    ) == 0
    assert capsys.readouterr().out == "Extensão desabilitada: one\n"
    assert maintenance.run_tools(
        argparse.Namespace(tools_command="doctor", state=None),
        app_paths=app_paths,
        workspace=tmp_path,
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        "one: OK (one@1.2.3)",
        "two: MISSING MANIFEST",
    ]
    assert calls == [
        ("add", (app_paths,), {"extension_id": "new", "manifest_path": "manifest.json", "enabled": False, "state_path": None}),
        ("set", (app_paths, "one", True, None), {}),
        ("set", (app_paths, "one", False, None), {}),
    ]


def test_legacy_tools_doctor_renders_prior_entries_before_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_paths = AppPaths.discover(tmp_path / "home")

    def diagnostics(*_args: object, **_kwargs: object) -> Iterator[LegacyExtensionRegistryEntry]:
        yield _entry("early", status="ok")
        raise ValueError("late invalid manifest")

    monkeypatch.setattr(maintenance, "doctor_legacy_extensions", diagnostics)

    with pytest.raises(ValueError, match="late invalid manifest"):
        maintenance.run_tools(
            argparse.Namespace(tools_command="doctor", state=None),
            app_paths=app_paths,
            workspace=tmp_path,
        )
    assert capsys.readouterr().out == "early: OK (early@1.2.3)\n"
