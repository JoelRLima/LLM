"""Schema and recovery policy for W18 transaction evidence.

The durable writer/recovery executor lives in ``install.ps1`` because the
normative installed product must work without Python.  Keeping the state
contract here makes it testable on every CI cell without invoking Windows
Registry or a real installer.
"""

from __future__ import annotations

import ntpath
import re
from collections.abc import Mapping

JOURNAL_SCHEMA_VERSION = 1
JOURNAL_STATES = (
    "PREPARED",
    "STAGED",
    "LAUNCHER_PROMOTED",
    "PATH_MUTATED",
    "FRESH_SHELL_VERIFIED",
    "COMMITTED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "FAILED_RECOVERY",
)
RECOVERABLE_STATES = frozenset(JOURNAL_STATES[0:5])
TERMINAL_STATES = frozenset(("COMMITTED", "ROLLED_BACK"))

REQUIRED_JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "operation",
        "transaction_id",
        "state",
        "install_root",
        "prior_install_root_present",
        "stable_launcher",
        "owned_path",
        "candidate_id",
        "candidate_path",
        "candidate_stage",
        "candidate_backup",
        "transaction_root",
        "payload_sha256",
        "payload_inventory_sha256",
        "prior_path",
        "prior_launcher_present",
        "prior_launcher_sha256",
        "prior_launcher_backup",
        "launcher_promoted",
        "path_mutated",
        "prior_receipt_present",
        "prior_receipt_backup",
        "prior_candidate_id",
        "prior_previous_candidate_id",
        "created_at",
        "updated_at",
    }
)
LEGACY_JOURNAL_KEYS = frozenset()

_TRANSACTION_ID = re.compile(r"w18-(?:install|uninstall)-[0-9]{17}-[0-9a-f]{12}")
_CANDIDATE_ID = re.compile(r"w18-[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class JournalValidationError(ValueError):
    """Raised when a journal cannot authorize a recovery action."""


def _validate_journal_keys(document: Mapping[str, object]) -> None:
    keys = set(document)
    if keys != REQUIRED_JOURNAL_KEYS:
        raise JournalValidationError("journal keys do not match the W18 schema")


def _validate_journal_header(document: Mapping[str, object]) -> None:
    if document["schema_version"] != JOURNAL_SCHEMA_VERSION:
        raise JournalValidationError("unknown journal schema")
    if document["operation"] not in {"install", "uninstall"}:
        raise JournalValidationError("unknown journal operation")
    if document["state"] not in JOURNAL_STATES:
        raise JournalValidationError("unknown journal state")


def _validate_journal_ownership(
    document: Mapping[str, object],
    *,
    install_root: str,
    stable_launcher: str,
    owned_path: str,
) -> None:
    for key, expected in (
        ("install_root", install_root),
        ("stable_launcher", stable_launcher),
        ("owned_path", owned_path),
    ):
        if document[key] != expected:
            raise JournalValidationError(f"journal {key} is not owned by W18")


def _validate_prior_path_snapshot(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "key_present",
        "present",
        "value",
        "kind",
    }:
        raise JournalValidationError("journal prior_path snapshot is invalid")
    for key in ("key_present", "present"):
        if not isinstance(value[key], bool):
            raise JournalValidationError(f"journal prior_path.{key} is invalid")
    if value["present"] and not value["key_present"]:
        raise JournalValidationError("journal prior_path presence is inconsistent")
    if value["present"]:
        if not isinstance(value["value"], str) or value["kind"] not in {"String", "ExpandString"}:
            raise JournalValidationError("journal prior_path value or kind is invalid")
    elif value["value"] is not None or value["kind"] is not None:
        raise JournalValidationError("journal prior_path kind is invalid")


def _validate_payload_hashes(document: Mapping[str, object]) -> None:
    for key in ("payload_sha256", "payload_inventory_sha256"):
        value = document[key]
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise JournalValidationError(f"journal {key} is invalid")


def _child(root: str, leaf: str) -> str:
    return ntpath.join(root, leaf)


def _validate_identity_and_canonical_paths(
    document: Mapping[str, object],
    install_root: str,
) -> tuple[object, str]:
    boolean_fields = (
        "prior_install_root_present",
        "prior_launcher_present",
        "launcher_promoted",
        "path_mutated",
        "prior_receipt_present",
    )
    for key in boolean_fields:
        if not isinstance(document[key], bool):
            raise JournalValidationError(f"journal {key} is invalid")

    operation = document["operation"]
    transaction_id = document["transaction_id"]
    if not isinstance(transaction_id, str) or _TRANSACTION_ID.fullmatch(transaction_id) is None:
        raise JournalValidationError("journal transaction_id is invalid")
    if not transaction_id.startswith(f"w18-{operation}-"):
        raise JournalValidationError("journal transaction_id does not match operation")
    transaction_root = _child(_child(install_root, "transactions"), transaction_id)
    if document["transaction_root"] != transaction_root:
        raise JournalValidationError("journal transaction_root is not canonical")

    candidate_id = document["candidate_id"]
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        raise JournalValidationError("journal candidate_id is invalid")
    if document["candidate_path"] != _child(_child(install_root, "versions"), candidate_id):
        raise JournalValidationError("journal candidate_path is not canonical")
    return operation, transaction_root


def _validate_operation_paths(
    document: Mapping[str, object],
    *,
    operation: object,
    transaction_root: str,
) -> None:
    if operation == "install":
        if document["candidate_stage"] != _child(transaction_root, "candidate-stage"):
            raise JournalValidationError("journal candidate_stage is not canonical")
        if document["candidate_backup"] not in {"", _child(transaction_root, "candidate-backup")}:
            raise JournalValidationError("journal candidate_backup is not canonical")
    else:
        if document["candidate_stage"] != transaction_root:
            raise JournalValidationError("journal uninstall candidate_stage is not canonical")
        if document["candidate_backup"] != _child(transaction_root, "uninstall-backup"):
            raise JournalValidationError("journal uninstall candidate_backup is not canonical")


def _validate_prior_state_consistency(
    document: Mapping[str, object],
    transaction_root: str,
) -> None:
    for present_key, hash_key, backup_key, backup_name in (
        ("prior_launcher_present", "prior_launcher_sha256", "prior_launcher_backup", "prior-launcher.cmd"),
        ("prior_receipt_present", None, "prior_receipt_backup", "prior-receipt.json"),
    ):
        present = document[present_key]
        expected_backup = _child(transaction_root, backup_name) if present else ""
        if document[backup_key] != expected_backup:
            raise JournalValidationError(f"journal {backup_key} is inconsistent")
        if hash_key is not None:
            expected_hash = document[hash_key]
            if present:
                if not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None:
                    raise JournalValidationError(f"journal {hash_key} is invalid")
            elif expected_hash != "":
                raise JournalValidationError(f"journal {hash_key} is inconsistent")

    for key in ("prior_candidate_id", "prior_previous_candidate_id"):
        value = document[key]
        if not isinstance(value, str) or (value and _CANDIDATE_ID.fullmatch(value) is None):
            raise JournalValidationError(f"journal {key} is invalid")
    if not document["prior_receipt_present"] and (
        document["prior_candidate_id"] or document["prior_previous_candidate_id"]
    ):
        raise JournalValidationError("journal prior receipt identity is inconsistent")


def _validate_semantics(document: Mapping[str, object], install_root: str) -> None:
    operation, transaction_root = _validate_identity_and_canonical_paths(document, install_root)
    _validate_operation_paths(
        document,
        operation=operation,
        transaction_root=transaction_root,
    )
    _validate_prior_state_consistency(document, transaction_root)


def validate_journal(
    document: Mapping[str, object],
    *,
    install_root: str,
    stable_launcher: str,
    owned_path: str,
) -> Mapping[str, object]:
    """Validate the bounded journal shape before any filesystem mutation."""

    _validate_journal_keys(document)
    _validate_journal_header(document)
    _validate_journal_ownership(
        document,
        install_root=install_root,
        stable_launcher=stable_launcher,
        owned_path=owned_path,
    )
    _validate_prior_path_snapshot(document["prior_path"])
    _validate_semantics(document, install_root)
    _validate_payload_hashes(document)
    return document


def recovery_needed(state: str) -> bool:
    """Whether startup must recover before accepting a new lifecycle request."""

    return state in RECOVERABLE_STATES or state == "ROLLING_BACK"


__all__ = [
    "JOURNAL_SCHEMA_VERSION",
    "JOURNAL_STATES",
    "LEGACY_JOURNAL_KEYS",
    "RECOVERABLE_STATES",
    "REQUIRED_JOURNAL_KEYS",
    "TERMINAL_STATES",
    "JournalValidationError",
    "recovery_needed",
    "validate_journal",
]
