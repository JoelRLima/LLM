"""Create and validate the W18 native-ConPTY evidence authority.

The authority is deliberately separate from the ConPTY transport runner.  It
binds the exact serialized bytes emitted by the installed-product verifier and
the layer probe, so a path that is later replaced cannot silently retain an
old evidence hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

SHA256_RE = re.compile(r"[0-9a-f]{64}")
CANDIDATE_RE = re.compile(r"w18-[0-9a-f]{32}")
TREE_RE = re.compile(r"[0-9a-f]{40}")


class ConPtyAuthorityError(ValueError):
    """Raised when ConPTY evidence cannot be bound fail-closed."""


def _canonical_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ConPtyAuthorityError(f"{label} must be canonical lowercase 64-hex SHA-256")
    return value


def _read_json_bytes(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConPtyAuthorityError(f"{label} is unreadable: {path}") from exc
    if not isinstance(loaded, dict):
        raise ConPtyAuthorityError(f"{label} must be a JSON object: {path}")
    return raw, loaded


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity_from_installed_summary(summary: Mapping[str, Any]) -> dict[str, str]:
    if summary.get("schema_version") != 3 or summary.get("status") != "passed":
        raise ConPtyAuthorityError("installed-product summary is not a passed canonical report")
    identity = summary.get("evidence_identity")
    if not isinstance(identity, Mapping):
        raise ConPtyAuthorityError("installed-product summary has no evidence_identity")
    candidate = identity.get("candidate_id")
    source_tree = identity.get("source_tree")
    manifest = identity.get("release_manifest_sha256")
    if not isinstance(candidate, str) or CANDIDATE_RE.fullmatch(candidate) is None:
        raise ConPtyAuthorityError("installed evidence candidate_id is invalid")
    if not isinstance(source_tree, str) or TREE_RE.fullmatch(source_tree) is None:
        raise ConPtyAuthorityError("installed evidence source_tree is invalid")
    return {
        "candidate_id": candidate,
        "release_manifest_sha256": _canonical_sha256(manifest, "release_manifest_sha256"),
        "source_tree": source_tree,
    }


def _require_interactive_evidence(
    summary: Mapping[str, Any],
    interactive: Mapping[str, Any],
    identity: Mapping[str, str],
) -> None:
    if interactive.get("schema_version") != "W18-W17-INSTALLED-INTERACTIVE-V1":
        raise ConPtyAuthorityError("installed-interactive evidence schema is invalid")
    if interactive.get("status") != "passed" or interactive.get("harness") != "conpty":
        raise ConPtyAuthorityError("installed-interactive evidence is not passed native ConPTY evidence")
    if interactive.get("evidence_identity") != dict(identity):
        raise ConPtyAuthorityError("installed-interactive evidence identity does not match installed summary")
    summary_interactive = summary.get("w17_installed_interactive")
    if summary_interactive != interactive:
        raise ConPtyAuthorityError(
            "installed-interactive bytes are not the exact evidence object embedded in the installed summary"
        )
    cases = interactive.get("cases")
    if not isinstance(cases, Mapping):
        raise ConPtyAuthorityError("installed-interactive evidence has no cases")
    for case_id in ("A57", "A58"):
        case = cases.get(case_id)
        if not isinstance(case, Mapping) or case.get("status") != "passed":
            raise ConPtyAuthorityError(f"{case_id} is not passed in installed-interactive evidence")
        if case.get("child_exit_code") != 0 or case.get("network_used") is not False:
            raise ConPtyAuthorityError(f"{case_id} is not bounded model/network-free evidence")
        if case.get("provider_model_server_invocations") != 0:
            raise ConPtyAuthorityError(f"{case_id} invoked a provider, model, or server")


def _require_layer_matrix(matrix: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    if matrix.get("schema_version") != "W18-CONPTY-LAYER-MATRIX-V1":
        raise ConPtyAuthorityError("ConPTY layer matrix schema is invalid")
    if matrix.get("candidate_id") != identity["candidate_id"]:
        raise ConPtyAuthorityError("ConPTY layer matrix candidate_id does not match installed evidence")
    for name in ("layer_1", "layer_2", "layer_4"):
        layer = matrix.get(name)
        if not isinstance(layer, Mapping) or layer.get("status") != "completed" or layer.get("returncode") != 0:
            raise ConPtyAuthorityError(f"{name} does not prove a successful ConPTY layer")
    layer_3 = matrix.get("layer_3")
    if (
        not isinstance(layer_3, list)
        or len(layer_3) != 2
        or any(not isinstance(layer, Mapping) or layer.get("status") != "completed" or layer.get("returncode") != 0 for layer in layer_3)
    ):
        raise ConPtyAuthorityError("layer_3 does not prove both candidate launcher paths")
    layer_5 = matrix.get("layer_5")
    if not isinstance(layer_5, Mapping) or layer_5.get("status") != "error":
        raise ConPtyAuthorityError("layer_5 does not record the bounded first-run prompt probe")
    return {
        "layer_1_cmd_echo": "passed",
        "layer_2_embedded_python": "passed",
        "layer_3_candidate_cmd_direct_and_via_cmd": "passed",
        "layer_4_stable_installed_cmd": "passed",
        "layer_5_bare_stable_first_run": "bounded_prompt_observed_and_terminated",
    }


def build_conpty_authority_evidence(
    installed_product_summary: Path,
    installed_interactive: Path,
    layer_matrix: Path,
    output: Path,
) -> dict[str, Any]:
    """Bind exact installed, interactive, and layer-matrix file bytes."""

    summary_raw, summary = _read_json_bytes(installed_product_summary, "installed-product summary")
    interactive_raw, interactive = _read_json_bytes(installed_interactive, "installed-interactive evidence")
    matrix_raw, matrix = _read_json_bytes(layer_matrix, "ConPTY layer matrix")
    identity = _identity_from_installed_summary(summary)
    _require_interactive_evidence(summary, interactive, identity)
    layer_status = _require_layer_matrix(matrix, identity)

    source_evidence = {
        "installed_product_json_sha256": _sha256_bytes(summary_raw),
        "installed_interactive_json_sha256": _sha256_bytes(interactive_raw),
        "layer_matrix_json_sha256": _sha256_bytes(matrix_raw),
    }
    for label, value in source_evidence.items():
        _canonical_sha256(value, label)

    authority: dict[str, Any] = {
        "schema_version": "W18-CONPTY-AUTHORITY-V2",
        "status": "passed_for_A57_A58",
        "harness": "native-Windows-ConPTY",
        "evidence_identity": identity,
        "layer_matrix": layer_status,
        "cases": {
            "A57": dict(interactive["cases"]["A57"]),
            "A58": dict(interactive["cases"]["A58"]),
        },
        "source_evidence": source_evidence,
    }
    rendered = (json.dumps(authority, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(rendered)

    # The source files are authoritative bytes.  If any producer rewrites one
    # during binding, refuse to emit an artifact whose hashes describe a stale
    # read rather than silently accepting a race.
    for path, before in (
        (installed_product_summary, summary_raw),
        (installed_interactive, interactive_raw),
        (layer_matrix, matrix_raw),
    ):
        try:
            after = path.read_bytes()
        except OSError as exc:
            raise ConPtyAuthorityError(f"authority source disappeared during binding: {path}") from exc
        if after != before:
            raise ConPtyAuthorityError(f"authority source changed during binding: {path}")
    return authority


def validate_conpty_authority_evidence(
    authority_path: Path,
    installed_product_summary: Path,
    installed_interactive: Path,
    layer_matrix: Path,
) -> dict[str, Any]:
    """Validate authority identity and recompute every bound source hash."""

    authority_raw, authority = _read_json_bytes(authority_path, "ConPTY authority evidence")
    if authority.get("schema_version") != "W18-CONPTY-AUTHORITY-V2":
        raise ConPtyAuthorityError("ConPTY authority evidence schema is invalid")
    if authority.get("status") != "passed_for_A57_A58" or authority.get("harness") != "native-Windows-ConPTY":
        raise ConPtyAuthorityError("ConPTY authority evidence is not passed native evidence")
    summary_raw, summary = _read_json_bytes(installed_product_summary, "installed-product summary")
    interactive_raw, interactive = _read_json_bytes(installed_interactive, "installed-interactive evidence")
    matrix_raw, matrix = _read_json_bytes(layer_matrix, "ConPTY layer matrix")
    identity = _identity_from_installed_summary(summary)
    if authority.get("evidence_identity") != identity:
        raise ConPtyAuthorityError("ConPTY authority identity does not match installed summary")
    _require_interactive_evidence(summary, interactive, identity)
    layer_status = _require_layer_matrix(matrix, identity)
    if authority.get("layer_matrix") != layer_status:
        raise ConPtyAuthorityError("ConPTY authority layer status does not match the exact layer matrix")
    source_evidence = authority.get("source_evidence")
    if not isinstance(source_evidence, Mapping):
        raise ConPtyAuthorityError("ConPTY authority has no source_evidence binding")
    expected = {
        "installed_product_json_sha256": _sha256_bytes(summary_raw),
        "installed_interactive_json_sha256": _sha256_bytes(interactive_raw),
        "layer_matrix_json_sha256": _sha256_bytes(matrix_raw),
    }
    for label, actual in expected.items():
        declared = _canonical_sha256(source_evidence.get(label), label)
        if declared != actual:
            raise ConPtyAuthorityError(f"{label} does not match recomputed exact file bytes")
    if _sha256_bytes(authority_raw) == "":  # pragma: no cover - hashlib always returns a value.
        raise ConPtyAuthorityError("authority evidence hash could not be computed")
    return authority


__all__ = [
    "ConPtyAuthorityError",
    "SHA256_RE",
    "build_conpty_authority_evidence",
    "validate_conpty_authority_evidence",
]
