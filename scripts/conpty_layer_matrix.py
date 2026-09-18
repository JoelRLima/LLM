"""Produce the reproducible W18 native-ConPTY layer matrix.

This owner is deliberately outside the product runtime.  It probes the
installed candidate while its stable launcher still exists.  The published
matrix contains only hash-bound, bounded semantic evidence; an optional raw
capture sidecar remains runner-local for authority recomputation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w18_conpty import (  # noqa: E402
    ConPtyError,
    ConPtyResult,
    bounded_conpty_diagnostics,
    normalize_terminal_text,
    run_conpty,
)

SCHEMA_VERSION = "W18-CONPTY-LAYER-MATRIX-V1"
HARNESS = "native-Windows-ConPTY"
CANDIDATE_RE = re.compile(r"w18-[0-9a-f]{32}")
FIRST_RUN_MARKER = "Parece ser o primeiro uso"
LAYER_1_LABEL = "layer_1_cmd_echo"
LAYER_2_LABEL = "layer_2_embedded_python"
LAYER_3_LABEL = "layer_3_candidate_cmd_direct_and_via_cmd"
LAYER_4_LABEL = "layer_4_stable_installed_cmd"
LAYER_5_LABEL = "layer_5_bare_stable_first_run"
LAYER_LABELS = {
    "layer_1": LAYER_1_LABEL,
    "layer_2": LAYER_2_LABEL,
    "layer_3": LAYER_3_LABEL,
    "layer_4": LAYER_4_LABEL,
    "layer_5": LAYER_5_LABEL,
}
DEFAULT_PROBE_TIMEOUT_SECONDS = 15.0
DEFAULT_FIRST_RUN_TIMEOUT_SECONDS = 8.0


class ConPtyLayerMatrixError(RuntimeError):
    """Raised when a required reproducible ConPTY layer cannot be proven."""


def _command_result(
    label: str,
    result: ConPtyResult,
    *,
    marker: str,
) -> dict[str, Any]:
    normalized = normalize_terminal_text(result.transcript)
    if result.returncode != 0:
        raise ConPtyLayerMatrixError(
            f"{label} exited {result.returncode}; "
            f"bounded evidence={bounded_conpty_diagnostics(result, expected_marker=marker)}"
        )
    if marker not in normalized:
        raise ConPtyLayerMatrixError(
            f"{label} did not prove marker {marker!r}; "
            f"bounded evidence={bounded_conpty_diagnostics(result, expected_marker=marker)}"
        )
    return {
        "label": label,
        "status": "completed",
        "returncode": result.returncode,
        "marker": marker,
        "normalized_transcript_markers": [marker],
        "normalized_transcript_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "raw_transcript_sha256": result.raw_transcript_sha256,
        "output_bytes": result.output_bytes,
        "diagnostics": dict(result.diagnostics),
        "_raw_capture": {
            "raw_transcript_b64": base64.b64encode(result.raw_transcript_bytes).decode("ascii"),
            "raw_transcript_sha256": result.raw_transcript_sha256,
            "decoded_transcript": result.transcript,
        },
    }


def _run_probe(
    label: str,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    marker: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        result = run_conpty(
            command,
            cwd=cwd,
            environment=environment,
            interactions=(),
            timeout_seconds=timeout_seconds,
        )
    except ConPtyError as exc:
        raise ConPtyLayerMatrixError(f"{label} ConPTY probe failed: {exc}") from exc
    return _command_result(label, result, marker=marker)


def _cmd_command(comspec: str, command_line: str) -> tuple[str, ...]:
    return (f'"{comspec}"', "/d", "/s", "/c", command_line)


def _probe_environment(environment: Mapping[str, str], application_home: Path) -> dict[str, str]:
    result = {str(key): str(value) for key, value in environment.items()}
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "LLM_AGENT_PROVIDER",
        "LLM_AGENT_MODEL",
        "LLM_AGENT_ENDPOINT",
    ):
        result.pop(name, None)
    result["LLM_AGENT_HOME"] = str(application_home.resolve())
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def _prepare_first_run_home(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if not path.is_dir() or path.is_symlink() or any(path.iterdir()):
            raise ConPtyLayerMatrixError(f"layer 5 application home is not new and isolated: {path}")
        return
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise ConPtyLayerMatrixError(f"could not create layer 5 application home: {path}") from exc


def _first_run_probe(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        result = run_conpty(
            command,
            cwd=cwd,
            environment=environment,
            interactions=(),
            timeout_seconds=timeout_seconds,
            terminate_after_marker=FIRST_RUN_MARKER,
        )
    except ConPtyError as exc:
        raise ConPtyLayerMatrixError(f"{LAYER_5_LABEL} ConPTY probe failed: {exc}") from exc
    normalized = normalize_terminal_text(result.transcript)
    if FIRST_RUN_MARKER not in normalized:
        raise ConPtyLayerMatrixError(
            f"{LAYER_5_LABEL} did not prove {FIRST_RUN_MARKER!r}; "
            f"bounded evidence={bounded_conpty_diagnostics(result, expected_marker=FIRST_RUN_MARKER)}"
        )
    return {
        "label": LAYER_5_LABEL,
        # The child is deliberately terminated after the prompt.  Keeping the
        # transport outcome as an error preserves the historical matrix shape;
        # authority accepts it only after recomputing the visible marker.
        "status": "error",
        "outcome": "bounded_prompt_observed_and_terminated",
        "error": "bounded first-run prompt observed; child deliberately terminated",
        "termination": "after_marker",
        "bounded_timeout_seconds": timeout_seconds,
        "returncode": result.returncode,
        "marker": FIRST_RUN_MARKER,
        "normalized_transcript_markers": [FIRST_RUN_MARKER],
        "normalized_transcript_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "raw_transcript_sha256": result.raw_transcript_sha256,
        "output_bytes": result.output_bytes,
        "diagnostics": dict(result.diagnostics),
        "network_used": False,
        "provider_model_server_invocations": 0,
        "_raw_capture": {
            "raw_transcript_b64": base64.b64encode(result.raw_transcript_bytes).decode("ascii"),
            "raw_transcript_sha256": result.raw_transcript_sha256,
            "decoded_transcript": result.transcript,
        },
    }


def _write_raw_capture(
    output: Path,
    *,
    candidate_id: str,
    evidence_identity: Mapping[str, str] | None,
    captures: Mapping[str, Any],
) -> None:
    if any(not str(item.get("raw_transcript_b64", "")) for item in captures.values()):
        raise ConPtyLayerMatrixError("raw ConPTY capture bytes were not retained for authority validation")
    document: dict[str, Any] = {
        "schema_version": "W18-CONPTY-RAW-CAPTURE-V1",
        "candidate_id": candidate_id,
        "layers": dict(captures),
    }
    if evidence_identity is not None:
        document["evidence_identity"] = dict(evidence_identity)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_conpty_layer_matrix(
    *,
    candidate_id: str,
    candidate_root: Path,
    stable_launcher: Path,
    cwd: Path,
    environment: Mapping[str, str],
    application_home: Path,
    first_run_home: Path,
    evidence_identity: Mapping[str, str] | None = None,
    probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    first_run_timeout_seconds: float = DEFAULT_FIRST_RUN_TIMEOUT_SECONDS,
    comspec: str | None = None,
    raw_capture_path: Path | None = None,
) -> dict[str, Any]:
    """Run all five historical layers against one installed candidate."""

    if CANDIDATE_RE.fullmatch(candidate_id) is None:
        raise ConPtyLayerMatrixError("candidate_id is not canonical")
    candidate_root = candidate_root.resolve()
    stable_launcher = stable_launcher.resolve()
    cwd = cwd.resolve()
    application_home = application_home.resolve()
    first_run_home = first_run_home.resolve()
    candidate_runtime = candidate_root / "runtime" / "python.exe"
    candidate_launcher = candidate_root / "bin" / "llm-agent.cmd"
    for path, label in (
        (candidate_runtime, "candidate embedded runtime"),
        (candidate_launcher, "candidate launcher"),
        (stable_launcher, "stable launcher"),
    ):
        if not path.is_file():
            raise ConPtyLayerMatrixError(f"{label} is missing: {path}")
    if not cwd.is_dir():
        raise ConPtyLayerMatrixError(f"ConPTY probe cwd is missing: {cwd}")
    _prepare_first_run_home(first_run_home)
    if first_run_home == application_home or first_run_home.is_relative_to(candidate_root):
        raise ConPtyLayerMatrixError("layer 5 application home collides with installed candidate state")

    selected_comspec = comspec or os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    probe_environment = _probe_environment(environment, application_home)
    first_run_environment = _probe_environment(environment, first_run_home)

    layer_1_command = _cmd_command(selected_comspec, "echo W18_CONPTY_SMOKE")
    layer_1 = _run_probe(
        LAYER_1_LABEL,
        layer_1_command,
        cwd=cwd,
        environment=probe_environment,
        marker="W18_CONPTY_SMOKE",
        timeout_seconds=probe_timeout_seconds,
    )

    layer_2_command = (f'"{candidate_runtime}"', "-c", "print('W18_CONPTY_PYTHON')")
    layer_2 = _run_probe(
        LAYER_2_LABEL,
        layer_2_command,
        cwd=cwd,
        environment=probe_environment,
        marker="W18_CONPTY_PYTHON",
        timeout_seconds=probe_timeout_seconds,
    )

    candidate_direct_command = _cmd_command(
        selected_comspec,
        f'call "{candidate_launcher}" --version',
    )
    candidate_via_cmd_command = _cmd_command(
        selected_comspec,
        f'"{candidate_launcher}" --version',
    )
    layer_3 = [
        _run_probe(
            LAYER_3_LABEL,
            candidate_direct_command,
            cwd=cwd,
            environment=probe_environment,
            marker="llm-agent ",
            timeout_seconds=probe_timeout_seconds,
        )
        | {"invocation": "candidate_path"},
        _run_probe(
            LAYER_3_LABEL,
            candidate_via_cmd_command,
            cwd=cwd,
            environment=probe_environment,
            marker="llm-agent ",
            timeout_seconds=probe_timeout_seconds,
        )
        | {"invocation": "via_cmd"},
    ]

    stable_command = _cmd_command(selected_comspec, f'call "{stable_launcher}" --version')
    layer_4 = _run_probe(
        LAYER_4_LABEL,
        stable_command,
        cwd=cwd,
        environment=probe_environment,
        marker="llm-agent ",
        timeout_seconds=probe_timeout_seconds,
    )

    layer_5_command = _cmd_command(selected_comspec, f'call "{stable_launcher}"')
    layer_5 = _first_run_probe(
        layer_5_command,
        cwd=cwd,
        environment=first_run_environment,
        timeout_seconds=first_run_timeout_seconds,
    )

    raw_captures: dict[str, Any] = {
        "layer_1": layer_1.pop("_raw_capture"),
        "layer_2": layer_2.pop("_raw_capture"),
        "layer_3_candidate_path": layer_3[0].pop("_raw_capture"),
        "layer_3_via_cmd": layer_3[1].pop("_raw_capture"),
        "layer_4": layer_4.pop("_raw_capture"),
        "layer_5": layer_5.pop("_raw_capture"),
    }
    matrix: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "diagnosed",
        "harness": HARNESS,
        "candidate_id": candidate_id,
        "probe_timeout_seconds": probe_timeout_seconds,
        "first_run_timeout_seconds": first_run_timeout_seconds,
        "layer_1": layer_1,
        "layer_2": layer_2,
        "layer_3": layer_3,
        "layer_4": layer_4,
        "layer_5": layer_5,
    }
    if evidence_identity is not None:
        matrix["evidence_identity"] = dict(evidence_identity)
    if raw_capture_path is not None:
        _write_raw_capture(
            raw_capture_path,
            candidate_id=candidate_id,
            evidence_identity=evidence_identity,
            captures=raw_captures,
        )
    return matrix


def write_conpty_layer_matrix(matrix: Mapping[str, Any], output: Path) -> None:
    """Write one deterministic, separately-bindable matrix JSON file."""

    if matrix.get("schema_version") != SCHEMA_VERSION or matrix.get("status") != "diagnosed":
        raise ConPtyLayerMatrixError("cannot write a non-diagnosed W18 ConPTY layer matrix")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dict(matrix), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def produce_conpty_layer_matrix(**kwargs: Any) -> dict[str, Any]:
    """Compatibility-named producer entry point for verifier callers."""

    return build_conpty_layer_matrix(**kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--stable-launcher", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--application-home", type=Path, required=True)
    parser.add_argument("--first-run-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-tree")
    parser.add_argument("--release-manifest-sha256")
    args = parser.parse_args(argv)
    identity = None
    if args.source_tree is not None or args.release_manifest_sha256 is not None:
        if args.source_tree is None or args.release_manifest_sha256 is None:
            parser.error("--source-tree and --release-manifest-sha256 must be supplied together")
        identity = {
            "candidate_id": args.candidate_id,
            "source_tree": args.source_tree,
            "release_manifest_sha256": args.release_manifest_sha256,
        }
    try:
        matrix = build_conpty_layer_matrix(
            candidate_id=args.candidate_id,
            candidate_root=args.candidate_root,
            stable_launcher=args.stable_launcher,
            cwd=args.cwd,
            environment=os.environ,
            application_home=args.application_home,
            first_run_home=args.first_run_home,
            evidence_identity=identity,
            raw_capture_path=args.output.with_suffix(".raw.json"),
        )
        write_conpty_layer_matrix(matrix, args.output)
    except ConPtyLayerMatrixError as exc:
        print(f"CONPTY_LAYER_MATRIX=FAIL ({exc})", file=sys.stderr)
        return 1
    print(json.dumps({"status": "diagnosed", "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_RE",
    "ConPtyLayerMatrixError",
    "FIRST_RUN_MARKER",
    "HARNESS",
    "LAYER_LABELS",
    "SCHEMA_VERSION",
    "build_conpty_layer_matrix",
    "main",
    "produce_conpty_layer_matrix",
    "write_conpty_layer_matrix",
]
