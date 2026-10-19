from __future__ import annotations

import pytest

from scripts.w23_diagnostics.entry import (
    _installed_workflow_path,
    _require_only_installed_workflow_change,
)
from scripts.w23_diagnostics.process import StageError


def test_installed_workflow_is_derived_from_contract_path_keys() -> None:
    files = {
        "README.md": "base-readme",
        ".github/workflows/ci.yml": "base-ci",
        ".github/workflows/candidate-installed-product.yml": "base-workflow",
    }

    assert _installed_workflow_path(files) == ".github/workflows/candidate-installed-product.yml"


@pytest.mark.parametrize(
    "files",
    [
        {"README.md": "base", "scripts/installed-product.yml": "outside"},
        {"README.md": "base", ".github/workflows/nested/installed-product.yml": "nested"},
        {"README.md": "base"},
    ],
)
def test_installed_workflow_rejects_zero_eligible_contract_paths(files: dict[str, str]) -> None:
    with pytest.raises(StageError, match="FAIL"):
        _installed_workflow_path(files)


def test_installed_workflow_rejects_multiple_eligible_contract_paths() -> None:
    files = {
        ".github/workflows/first-installed-product.yml": "one",
        ".github/workflows/second-installed-product.yml": "two",
    }

    with pytest.raises(StageError, match="FAIL"):
        _installed_workflow_path(files)


def test_exact_workflow_change_is_allowed_but_additional_drift_is_rejected() -> None:
    expected = {
        "README.md": "base-readme",
        ".github/workflows/candidate-installed-product.yml": "base-workflow",
    }
    actual = {
        "README.md": "base-readme",
        ".github/workflows/candidate-installed-product.yml": "patched-workflow",
    }

    _require_only_installed_workflow_change(expected, actual)

    actual["README.md"] = "unexpected-change"
    with pytest.raises(StageError, match="FAIL"):
        _require_only_installed_workflow_change(expected, actual)

    actual["unexpected.bin"] = "uncontracted-file"
    with pytest.raises(StageError, match="FAIL"):
        _require_only_installed_workflow_change(expected, actual)
