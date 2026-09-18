"""Create and validate the W18 native-ConPTY evidence authority.

The authority is deliberately separate from the ConPTY transport runner.  It
binds the exact serialized bytes emitted by the installed-product verifier and
the layer probe, so a path that is later replaced cannot silently retain an
old evidence hash.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from scripts.conpty_layer_matrix import (
    FIRST_RUN_MARKER,
    LAYER_1_LABEL,
    LAYER_2_LABEL,
    LAYER_3_LABEL,
    LAYER_4_LABEL,
    LAYER_5_LABEL,
)
from scripts.w18_conpty import normalize_terminal_text

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


def _require_layer_markers(
    layer: Mapping[str, Any],
    *,
    layer_name: str,
    marker: str,
) -> None:
    _canonical_sha256(layer.get("raw_transcript_sha256"), f"{layer_name}.raw_transcript_sha256")
    _canonical_sha256(
        layer.get("normalized_transcript_sha256"),
        f"{layer_name}.normalized_transcript_sha256",
    )
    markers = layer.get("normalized_transcript_markers")
    if not isinstance(markers, list) or marker not in markers:
        raise ConPtyAuthorityError(f"{layer_name} does not contain normalized marker {marker!r}")


def _read_runner_local_raw_capture(sidecar: Path, identity: Mapping[str, str]) -> Mapping[str, Any]:
    try:
        capture = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConPtyAuthorityError(f"runner-local raw ConPTY capture is unreadable: {sidecar}") from exc
    if (
        not isinstance(capture, Mapping)
        or capture.get("schema_version") != "W18-CONPTY-RAW-CAPTURE-V1"
        or capture.get("candidate_id") != identity["candidate_id"]
    ):
        raise ConPtyAuthorityError("runner-local raw ConPTY capture identity is invalid")
    capture_identity = capture.get("evidence_identity")
    if capture_identity is not None and capture_identity != dict(identity):
        raise ConPtyAuthorityError("runner-local raw ConPTY capture identity does not match installed evidence")
    return capture


def _raw_capture_specs(matrix: Mapping[str, Any]) -> list[tuple[str, str, str, Mapping[str, Any]]]:
    layer_3 = matrix.get("layer_3")
    if not isinstance(layer_3, list):
        raise ConPtyAuthorityError("layer_3 raw capture is missing")
    by_invocation = {
        str(layer.get("invocation")): layer
        for layer in layer_3
        if isinstance(layer, Mapping) and isinstance(layer.get("invocation"), str)
    }
    if set(by_invocation) != {"candidate_path", "via_cmd"}:
        raise ConPtyAuthorityError("layer_3 raw capture invocations are incomplete")
    specs: list[tuple[str, str, str, Mapping[str, Any]]] = []
    for capture_name, layer_name, marker, matrix_name in (
        ("layer_1", "layer_1", "W18_CONPTY_SMOKE", "layer_1"),
        ("layer_2", "layer_2", "W18_CONPTY_PYTHON", "layer_2"),
        ("layer_4", "layer_4", "llm-agent ", "layer_4"),
        ("layer_5", "layer_5", FIRST_RUN_MARKER, "layer_5"),
    ):
        layer = matrix.get(matrix_name)
        if not isinstance(layer, Mapping):
            raise ConPtyAuthorityError(f"{layer_name} raw capture is missing")
        specs.append((capture_name, layer_name, marker, layer))
    for invocation, capture_name in (
        ("candidate_path", "layer_3_candidate_path"),
        ("via_cmd", "layer_3_via_cmd"),
    ):
        layer = by_invocation[invocation]
        specs.append((capture_name, f"layer_3[{invocation}]", "llm-agent ", layer))
    return specs


def _decode_raw_capture(item: Mapping[str, Any], layer_name: str) -> tuple[bytes, str]:
    encoded = item.get("raw_transcript_b64")
    decoded = item.get("decoded_transcript")
    if not isinstance(encoded, str) or not isinstance(decoded, str):
        raise ConPtyAuthorityError(f"{layer_name} raw capture is incomplete")
    try:
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConPtyAuthorityError(f"{layer_name} raw capture bytes are invalid") from exc
    if not raw_bytes:
        raise ConPtyAuthorityError(f"{layer_name} raw capture is empty")
    try:
        decoded_from_bytes = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded_from_bytes = raw_bytes.decode("cp850")
    if decoded != decoded_from_bytes:
        raise ConPtyAuthorityError(f"{layer_name} decoded transcript does not match raw bytes")
    declared_hash = item.get("raw_transcript_sha256")
    if declared_hash is not None and _canonical_sha256(declared_hash, f"{layer_name}.raw_transcript_sha256") != hashlib.sha256(raw_bytes).hexdigest():
        raise ConPtyAuthorityError(f"{layer_name} raw capture SHA-256 does not match raw bytes")
    return raw_bytes, decoded


def _validate_raw_capture_item(
    item: Any,
    layer: Mapping[str, Any],
    *,
    layer_name: str,
    marker: str,
) -> None:
    if not isinstance(item, Mapping):
        raise ConPtyAuthorityError(f"{layer_name} raw capture is missing")
    raw_bytes, decoded = _decode_raw_capture(item, layer_name)
    raw_hash = _canonical_sha256(layer.get("raw_transcript_sha256"), f"{layer_name}.raw_transcript_sha256")
    if hashlib.sha256(raw_bytes).hexdigest() != raw_hash:
        raise ConPtyAuthorityError(f"{layer_name} raw transcript SHA-256 does not match matrix")
    normalized = normalize_terminal_text(decoded)
    normalized_hash = _canonical_sha256(
        layer.get("normalized_transcript_sha256"),
        f"{layer_name}.normalized_transcript_sha256",
    )
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != normalized_hash:
        raise ConPtyAuthorityError(f"{layer_name} normalized transcript hash does not match matrix")
    if marker not in normalized:
        raise ConPtyAuthorityError(f"{layer_name} raw capture does not prove normalized marker {marker!r}")


def _validate_runner_local_raw_capture(
    layer_matrix_path: Path,
    matrix: Mapping[str, Any],
    identity: Mapping[str, str],
    *,
    required: bool,
) -> None:
    """Recompute the matrix projection from a runner-local, non-uploaded sidecar."""

    sidecar = layer_matrix_path.with_suffix(".raw.json")
    if not sidecar.is_file():
        if required:
            raise ConPtyAuthorityError(
                "runner-local raw ConPTY capture is required during authority creation"
            )
        return
    capture = _read_runner_local_raw_capture(sidecar, identity)
    layers = capture.get("layers")
    if not isinstance(layers, Mapping):
        raise ConPtyAuthorityError("runner-local raw ConPTY capture has no layers")
    for capture_name, layer_name, marker, layer in _raw_capture_specs(matrix):
        _validate_raw_capture_item(layers.get(capture_name), layer, layer_name=layer_name, marker=marker)


def _require_completed_layer(matrix: Mapping[str, Any], name: str, label: str, marker: str) -> None:
    layer = matrix.get(name)
    if (
        not isinstance(layer, Mapping)
        or layer.get("label") != label
        or layer.get("status") != "completed"
        or layer.get("returncode") != 0
    ):
        raise ConPtyAuthorityError(f"{name} does not prove a successful ConPTY layer")
    _require_layer_markers(layer, layer_name=name, marker=marker)


def _require_layer_3(matrix: Mapping[str, Any]) -> None:
    layer_3 = matrix.get("layer_3")
    if not isinstance(layer_3, list) or len(layer_3) != 2:
        raise ConPtyAuthorityError("layer_3 does not prove both candidate launcher paths")
    for index, layer in enumerate(layer_3):
        if (
            not isinstance(layer, Mapping)
            or layer.get("label") != LAYER_3_LABEL
            or layer.get("status") != "completed"
            or layer.get("returncode") != 0
            or layer.get("invocation") not in {"candidate_path", "via_cmd"}
        ):
            raise ConPtyAuthorityError("layer_3 does not prove both candidate launcher paths")
        _require_layer_markers(layer, layer_name=f"layer_3[{index}]", marker="llm-agent ")
    if {layer.get("invocation") for layer in layer_3 if isinstance(layer, Mapping)} != {"candidate_path", "via_cmd"}:
        raise ConPtyAuthorityError("layer_3 does not prove both candidate launcher paths")


def _require_layer_5(matrix: Mapping[str, Any]) -> None:
    layer_5 = matrix.get("layer_5")
    if (
        not isinstance(layer_5, Mapping)
        or layer_5.get("label") != LAYER_5_LABEL
        or layer_5.get("status") not in {"error", "bounded_prompt_observed_and_terminated"}
        or layer_5.get("outcome") != "bounded_prompt_observed_and_terminated"
        or layer_5.get("termination") not in {"after_marker", "bounded_timeout"}
        or layer_5.get("marker") != FIRST_RUN_MARKER
    ):
        raise ConPtyAuthorityError("layer_5 does not record the bounded first-run prompt probe")
    _require_layer_markers(layer_5, layer_name="layer_5", marker=FIRST_RUN_MARKER)


def _require_layer_matrix(
    matrix: Mapping[str, Any],
    identity: Mapping[str, str],
    layer_matrix_path: Path | None = None,
    *,
    raw_capture_required: bool = False,
) -> dict[str, Any]:
    if matrix.get("schema_version") != "W18-CONPTY-LAYER-MATRIX-V1":
        raise ConPtyAuthorityError("ConPTY layer matrix schema is invalid")
    if matrix.get("status") != "diagnosed":
        raise ConPtyAuthorityError("ConPTY layer matrix status is not diagnosed")
    if matrix.get("harness") != "native-Windows-ConPTY":
        raise ConPtyAuthorityError("ConPTY layer matrix harness is not native Windows ConPTY")
    if matrix.get("candidate_id") != identity["candidate_id"]:
        raise ConPtyAuthorityError("ConPTY layer matrix candidate_id does not match installed evidence")
    matrix_identity = matrix.get("evidence_identity")
    if matrix_identity is not None and matrix_identity != dict(identity):
        raise ConPtyAuthorityError("ConPTY layer matrix evidence identity does not match installed evidence")
    _require_completed_layer(matrix, "layer_1", LAYER_1_LABEL, "W18_CONPTY_SMOKE")
    _require_completed_layer(matrix, "layer_2", LAYER_2_LABEL, "W18_CONPTY_PYTHON")
    _require_completed_layer(matrix, "layer_4", LAYER_4_LABEL, "llm-agent ")
    _require_layer_3(matrix)
    _require_layer_5(matrix)
    if layer_matrix_path is not None:
        _validate_runner_local_raw_capture(
            layer_matrix_path,
            matrix,
            identity,
            required=raw_capture_required,
        )
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
    layer_status = _require_layer_matrix(
        matrix,
        identity,
        layer_matrix,
        raw_capture_required=True,
    )

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
    layer_status = _require_layer_matrix(matrix, identity, layer_matrix)
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
