"""Deterministic W18 journal schema and recovery tests."""

from __future__ import annotations

from copy import deepcopy

import pytest

from installer.journal import JournalValidationError, recovery_needed, validate_journal

INSTALL_ROOT = r"C:\Users\Test\AppData\Local\local-llm-agent\install"
STABLE = INSTALL_ROOT + r"\bin\llm-agent.cmd"
OWNED = INSTALL_ROOT + r"\bin"
TRANSACTION_ID = "w18-install-20260101000000000-123456789abc"
TRANSACTION_ROOT = INSTALL_ROOT + "\\transactions\\" + TRANSACTION_ID


def _journal(state: str = "PREPARED") -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "install",
        "transaction_id": TRANSACTION_ID,
        "state": state,
        "install_root": INSTALL_ROOT,
        "prior_install_root_present": False,
        "stable_launcher": STABLE,
        "owned_path": OWNED,
        "candidate_id": "w18-" + "a" * 32,
        "candidate_path": INSTALL_ROOT + r"\versions\w18-" + "a" * 32,
        "candidate_stage": TRANSACTION_ROOT + r"\candidate-stage",
        "candidate_backup": "",
        "transaction_root": TRANSACTION_ROOT,
        "payload_sha256": "1" * 64,
        "payload_inventory_sha256": "2" * 64,
        "prior_path": {"key_present": True, "present": True, "value": "A;B", "kind": "ExpandString"},
        "prior_launcher_present": False,
        "prior_launcher_sha256": "",
        "prior_launcher_backup": "",
        "launcher_promoted": False,
        "path_mutated": False,
        "prior_receipt_present": False,
        "prior_receipt_backup": "",
        "prior_candidate_id": "",
        "prior_previous_candidate_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.parametrize("state", ["PREPARED", "STAGED", "LAUNCHER_PROMOTED", "PATH_MUTATED"])
def test_recoverable_states_are_explicit(state: str) -> None:
    assert recovery_needed(state)
    validate_journal(_journal(state), install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


def test_corrupt_journal_fails_closed() -> None:
    document = _journal()
    document.pop("candidate_backup")

    with pytest.raises(JournalValidationError):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


def test_journal_rejects_wrong_owned_paths_and_registry_kind() -> None:
    wrong_path = deepcopy(_journal())
    wrong_path["owned_path"] = r"C:\foreign\bin"
    wrong_kind = deepcopy(_journal())
    wrong_kind["prior_path"] = {"key_present": True, "present": True, "value": "A", "kind": "MultiString"}

    with pytest.raises(JournalValidationError):
        validate_journal(wrong_path, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)
    with pytest.raises(JournalValidationError):
        validate_journal(wrong_kind, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


def test_journal_requires_a_boolean_prior_install_root_snapshot() -> None:
    document = _journal()
    document["prior_install_root_present"] = True
    validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)

    document["prior_install_root_present"] = "false"
    with pytest.raises(JournalValidationError, match="prior_install_root_present"):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_root", r"C:\external\transaction"),
        ("candidate_path", r"C:\external\candidate"),
        ("candidate_stage", r"C:\external\stage"),
        ("candidate_backup", r"C:\external\backup"),
        ("prior_launcher_backup", r"C:\external\launcher.cmd"),
        ("prior_receipt_backup", r"C:\external\receipt.json"),
    ],
)
def test_journal_rejects_independently_tampered_authority_paths(field: str, value: str) -> None:
    document = _journal()
    document[field] = value

    with pytest.raises(JournalValidationError, match=field):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_id", "w18-install-20260101000000000-aaaaaaaaaaaa"),
        ("candidate_id", "w18-" + "b" * 32),
        ("transaction_root", TRANSACTION_ROOT + r"\..\outside"),
        ("candidate_path", INSTALL_ROOT + r"\versions\.\w18-" + "a" * 32),
        ("candidate_stage", TRANSACTION_ROOT + r"\child\..\candidate-stage"),
    ],
)
def test_journal_rejects_id_path_mismatches_and_aliases(field: str, value: str) -> None:
    document = _journal()
    document[field] = value

    with pytest.raises(JournalValidationError):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize(
    "field",
    [
        "prior_install_root_present",
        "prior_launcher_present",
        "launcher_promoted",
        "path_mutated",
        "prior_receipt_present",
    ],
)
@pytest.mark.parametrize("substitute", ["false", 0, 1])
def test_journal_rejects_non_boolean_top_level_flags(field: str, substitute: object) -> None:
    document = _journal()
    document[field] = substitute

    with pytest.raises(JournalValidationError, match=field):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize("field", ["key_present", "present"])
@pytest.mark.parametrize("substitute", ["false", 0, 1])
def test_journal_rejects_non_boolean_path_flags(field: str, substitute: object) -> None:
    document = _journal()
    document["prior_path"][field] = substitute  # type: ignore[index]

    with pytest.raises(JournalValidationError, match=field):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


def test_journal_requires_presence_backup_and_hash_consistency() -> None:
    launcher = _journal()
    launcher["prior_launcher_present"] = True
    receipt = _journal()
    receipt["prior_receipt_present"] = True
    absent_path = _journal()
    absent_path["prior_path"] = {"key_present": False, "present": True, "value": "", "kind": "String"}

    for document in (launcher, receipt, absent_path):
        with pytest.raises(JournalValidationError):
            validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize("field", ["prior_candidate_id", "prior_previous_candidate_id"])
def test_journal_rejects_malformed_prior_receipt_candidate_ids(field: str) -> None:
    document = _journal()
    document[field] = "not-a-candidate"

    with pytest.raises(JournalValidationError, match=field):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


@pytest.mark.parametrize("field", ["prior_candidate_id", "prior_previous_candidate_id"])
def test_journal_rejects_prior_receipt_identity_without_receipt(field: str) -> None:
    document = _journal()
    document[field] = document["candidate_id"]

    with pytest.raises(JournalValidationError, match="prior receipt identity is inconsistent"):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)


def test_uninstall_requires_operation_specific_backup_shape() -> None:
    document = _journal()
    document.update(
        operation="uninstall",
        transaction_id="w18-uninstall-20260101000000000-123456789abc",
    )
    root = INSTALL_ROOT + "\\transactions\\" + str(document["transaction_id"])
    document["transaction_root"] = root
    document["candidate_stage"] = root
    document["candidate_backup"] = root + r"\uninstall-backup"
    document["prior_receipt_present"] = True
    document["prior_receipt_backup"] = root + r"\prior-receipt.json"
    document["prior_candidate_id"] = document["candidate_id"]
    validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)

    document["candidate_backup"] = root + r"\candidate-backup"
    with pytest.raises(JournalValidationError, match="uninstall candidate_backup"):
        validate_journal(document, install_root=INSTALL_ROOT, stable_launcher=STABLE, owned_path=OWNED)
