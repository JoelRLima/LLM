"""Focused tests for the single source-tree version owner."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest

import agent
from agent._version import VERSION
from scripts.verify_installed_package import CommandResult, VerificationError, _verify_version

ROOT = Path(__file__).resolve().parents[2]


def test_source_version_owner_is_name_independent_and_project_is_dynamic() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert VERSION == "0.2.0rc1"
    assert agent.__version__ == VERSION
    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "agent._version.VERSION"}' in project
    assert 'version = "0.1.0"' not in project


def test_installed_metadata_matches_owner_outside_source_checkout() -> None:
    package_file = Path(agent.__file__).resolve()
    if ROOT in package_file.parents:
        pytest.skip("source-checkout import; run this assertion from the installed wheel")
    try:
        installed_version = metadata.version("local-llm-agent")
    except metadata.PackageNotFoundError:
        pytest.fail("installed local-llm-agent metadata is missing")
    assert installed_version == agent.__version__ == VERSION == "0.2.0rc1"


def test_installed_acceptance_version_uses_canonical_owner_and_rejects_invalid_output() -> None:
    _verify_version(CommandResult("version", f"llm-agent {VERSION}\n", ""))

    with pytest.raises(VerificationError):
        _verify_version(CommandResult("version", "llm-agent 0.2.0\n", ""))
