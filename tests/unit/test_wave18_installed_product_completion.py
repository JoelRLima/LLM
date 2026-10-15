from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import verify_installed_product as verifier

_RAW_CAPTURE_BYTES = (
    b'{"schema_version":"W18-CONPTY-RAW-CAPTURE-V1",'
    b'"layers":{"layer_1":{"raw_transcript_b64":"eA=="}}}\n'
)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    root = Path(__file__).resolve().parents[2] / ".tmp"
    path = root / f"w18-completion-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if path.resolve().parent != root.resolve():
            raise AssertionError(f"refusing cleanup outside .tmp: {path}")
        shutil.rmtree(path.resolve(), ignore_errors=False)


def _snapshot(
    *, present: bool = True, value: str | None = "", kind: int | None = 1,
    kind_name: str | None = "String", key_present: bool = True,
) -> verifier.RegistryPathSnapshot:
    return verifier.RegistryPathSnapshot(present, value, kind, kind_name, key_present)


@pytest.mark.parametrize(
    "snapshot",
    (
        _snapshot(present=False, value=None, kind=None, kind_name=None, key_present=False),
        _snapshot(present=False, value=None, kind=None, kind_name=None, key_present=True),
        _snapshot(present=True, value="", kind=1, kind_name="String"),
        _snapshot(present=True, value="%USER_BIN%;;C:\\odd\\;", kind=2, kind_name="ExpandString"),
    ),
)
def test_path_restoration_accepts_only_exact_raw_registry_snapshot(
    monkeypatch: pytest.MonkeyPatch, snapshot: verifier.RegistryPathSnapshot
) -> None:
    writes: list[verifier.RegistryPathSnapshot] = []
    monkeypatch.setattr(verifier, "_v3_write_user_path_snapshot", writes.append)
    monkeypatch.setattr(verifier, "_registry_path_snapshot", lambda: snapshot)

    verifier._v3_restore_and_verify_user_path(snapshot)

    assert writes == [snapshot]


@pytest.mark.parametrize(
    "observed",
    (
        _snapshot(present=False, value=None, kind=None, kind_name=None, key_present=True),
        _snapshot(present=True, value="", kind=1, kind_name="String"),
        _snapshot(value="%USER_BIN%;;C:\\odd\\;", kind=1, kind_name="String"),
    ),
)
def test_path_restoration_rejects_presence_raw_value_and_type_mismatch(
    monkeypatch: pytest.MonkeyPatch, observed: verifier.RegistryPathSnapshot
) -> None:
    original = _snapshot(present=False, value=None, kind=None, kind_name=None, key_present=False)
    monkeypatch.setattr(verifier, "_v3_write_user_path_snapshot", lambda _snapshot: None)
    monkeypatch.setattr(verifier, "_registry_path_snapshot", lambda: observed)

    with pytest.raises(verifier.ProductVerificationError, match="read-back did not match"):
        verifier._v3_restore_and_verify_user_path(original)


def test_path_restoration_exception_is_sanitized_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot(value="PRIVATE_USER_PATH_SHOULD_NOT_ESCAPE")

    def fail(_snapshot: verifier.RegistryPathSnapshot) -> None:
        raise OSError("PRIVATE_USER_PATH_SHOULD_NOT_ESCAPE")

    monkeypatch.setattr(verifier, "_v3_write_user_path_snapshot", fail)

    with pytest.raises(verifier.ProductVerificationError, match=r"restoration failed \(OSError\)") as error:
        verifier._v3_restore_and_verify_user_path(snapshot)
    assert "PRIVATE_USER_PATH_SHOULD_NOT_ESCAPE" not in str(error.value)


def _wire_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    restore: str = "match",
    later_failure: str | None = None,
) -> tuple[Path, Path, verifier.RegistryPathSnapshot]:
    original = _snapshot(value="%USER_BIN%;;C:\\odd\\;", kind=2, kind_name="ExpandString")
    working = tmp_path / "verifier-owned-workspace"
    working.mkdir()
    context = verifier.ProductContext(
        bundle_root=tmp_path,
        install_script=tmp_path / "install.ps1",
        uninstall_script=tmp_path / "uninstall.ps1",
        install_root=tmp_path / "install-root",
        before_user=original,
        before_machine=None,
        working=working,
        outside=working,
        config=tmp_path / "config.json",
        sentinels=(),
        report={"schema_version": 1, "status": "failed", "scenarios": {}},
        application_home=working / "home",
        real_user_config=verifier.FileSnapshot(tmp_path / "real-config.json", False, None),
        original_user=original,
    )
    facts = SimpleNamespace(
        candidate_id="w18-" + "a" * 32,
        stable=tmp_path / "stable.cmd",
        after_install_user=original,
    )
    matrix_path = tmp_path / "matrix.json"
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(verifier, "_v3_prepare_context", lambda _bundle: context)
    monkeypatch.setattr(verifier, "_v3_hostile_environment", lambda _context: {})
    monkeypatch.setattr(verifier, "_v3_clean_install", lambda _context, _environment: facts)
    monkeypatch.setattr(verifier, "_v3_verify_corrupt_journal_no_mutation", lambda *_args: None)
    monkeypatch.setattr(verifier, "_write_preservation_state", lambda *_args: None)
    monkeypatch.setattr(verifier, "_v3_fresh_shell", lambda **_kwargs: {"status": "passed"})
    monkeypatch.setattr(verifier, "_v3_verify_same_candidate", lambda *_args: (b"receipt", original))
    monkeypatch.setattr(verifier, "_v3_verify_invalid_upgrade", lambda *_args: None)
    monkeypatch.setattr(verifier, "_v3_verify_corrective_lifecycle", lambda *_args: None)
    monkeypatch.setattr(verifier, "_v3_verify_receipt_fail_closed", lambda *_args: None)
    monkeypatch.setattr(verifier, "_v3_verify_active_candidate_uninstall", lambda *_args: None)

    def write_matrix(_context: Any, _facts: Any, output: Path) -> dict[str, Any]:
        output.write_bytes(b'{"schema_version":1,"status":"diagnosed"}\n')
        output.with_suffix(".raw.json").write_bytes(_RAW_CAPTURE_BYTES)
        return {"schema_version": 1, "status": "diagnosed"}

    monkeypatch.setattr(verifier, "_v3_verify_conpty_layer_matrix", write_matrix)

    def interactive(*_args: Any) -> dict[str, Any]:
        if later_failure == "interactive":
            raise verifier.ProductVerificationError("injected interactive failure")
        return {"status": "passed"}

    def lifecycle(*_args: Any) -> None:
        if later_failure == "lifecycle":
            raise verifier.ProductVerificationError("injected lifecycle failure")

    monkeypatch.setattr(verifier, "_v3_verify_w17_interactive", interactive)
    monkeypatch.setattr(verifier, "_v3_verify_lifecycle", lifecycle)
    monkeypatch.setattr(verifier, "_assert_file_snapshot_unchanged", lambda *_args: None)
    observed = original if restore == "match" else _snapshot(
        value=original.value if restore == "type" else "different", kind=1, kind_name="String"
    )
    monkeypatch.setattr(verifier, "_registry_path_snapshot", lambda: observed)

    def restore_path(_snapshot: verifier.RegistryPathSnapshot) -> None:
        if restore == "raise":
            raise OSError("synthetic registry write failure")

    monkeypatch.setattr(verifier, "_v3_write_user_path_snapshot", restore_path)
    return matrix_path, summary_path, original


@pytest.mark.parametrize("restore", ("raise", "noop", "type"))
def test_cli_cannot_report_pass_when_path_restore_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], restore: str
) -> None:
    matrix, summary, _original = _wire_completion(monkeypatch, tmp_path, restore=restore)
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--conpty-layer-matrix-json", str(matrix)]
    )

    output = capsys.readouterr()
    assert exit_code != 0
    assert "W18_INSTALLED_PRODUCT=PASS" not in output.out
    assert "verification failed" in output.err
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"
    assert matrix.exists(), output.err
    assert matrix.read_bytes() == b'{"schema_version":1,"status":"diagnosed"}\n'
    raw = matrix.with_suffix(".raw.json")
    assert raw.read_bytes() == _RAW_CAPTURE_BYTES
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == hashlib.sha256(_RAW_CAPTURE_BYTES).hexdigest()


def test_primary_and_restore_failures_are_both_reported_without_matrix_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix, summary, _original = _wire_completion(
        monkeypatch, tmp_path, restore="raise", later_failure="interactive"
    )
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--conpty-layer-matrix-json", str(matrix)]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "primary acceptance failed" in output.err
    assert "User PATH restoration failed" in output.err
    preserved = matrix.read_bytes()
    assert preserved == b'{"schema_version":1,"status":"diagnosed"}\n'
    assert hashlib.sha256(preserved).hexdigest() == hashlib.sha256(
        b'{"schema_version":1,"status":"diagnosed"}\n'
    ).hexdigest()
    raw = matrix.with_suffix(".raw.json")
    assert raw.exists()
    assert raw.read_bytes() == _RAW_CAPTURE_BYTES
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == hashlib.sha256(_RAW_CAPTURE_BYTES).hexdigest()
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


@pytest.mark.parametrize("stage", ("interactive", "lifecycle"))
def test_late_acceptance_failure_preserves_conpty_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    matrix, summary, _original = _wire_completion(monkeypatch, tmp_path, restore="match", later_failure=stage)
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--conpty-layer-matrix-json", str(matrix)]
    )

    assert exit_code == 1
    assert "PASS" not in capsys.readouterr().out
    assert matrix.read_bytes() == b'{"schema_version":1,"status":"diagnosed"}\n'
    assert matrix.with_suffix(".raw.json").read_bytes() == _RAW_CAPTURE_BYTES
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


def test_failure_before_matrix_does_not_create_matrix_or_overwrite_w17(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix, summary, _original = _wire_completion(monkeypatch, tmp_path, restore="match")
    w17 = tmp_path / "w17.json"
    w17.write_bytes(b'{"schema_version":"W17-VALID"}\n')
    monkeypatch.setattr(verifier, "_v3_verify_conpty_layer_matrix", lambda *_args: (_ for _ in ()).throw(
        verifier.ProductVerificationError("injected pre-matrix failure")
    ))
    monkeypatch.setattr(verifier, "_v3_verify_corrupt_journal_no_mutation", lambda *_args: (_ for _ in ()).throw(
        verifier.ProductVerificationError("injected pre-matrix failure")
    ))
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--w17-summary-json", str(w17),
         "--conpty-layer-matrix-json", str(matrix)]
    )

    assert exit_code == 1
    assert "PASS" not in capsys.readouterr().out
    assert not matrix.exists()
    assert w17.read_bytes() == b'{"schema_version":"W17-VALID"}\n'
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


def test_existing_matrix_path_is_rejected_before_acceptance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    matrix = tmp_path / "stale-matrix.json"
    matrix.write_bytes(b'{"stale":true}\n')
    called = False

    def prepare(_bundle: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(verifier, "_v3_prepare_context", prepare)

    with pytest.raises(verifier.ProductVerificationError, match="existing ConPTY matrix"):
        verifier._v3_verify_installed_product(tmp_path, conpty_layer_matrix_json=matrix)
    assert called is False
    assert matrix.read_bytes() == b'{"stale":true}\n'


def test_existing_raw_capture_path_is_rejected_before_acceptance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    matrix = tmp_path / "stale-matrix.json"
    raw = matrix.with_suffix(".raw.json")
    raw_bytes = b'{"stale":true}\n'
    raw.write_bytes(raw_bytes)
    called = False

    def prepare(_bundle: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(verifier, "_v3_prepare_context", prepare)
    with pytest.raises(verifier.ProductVerificationError, match="existing ConPTY raw-capture"):
        verifier._v3_verify_installed_product(tmp_path, conpty_layer_matrix_json=matrix)
    assert called is False
    assert raw.read_bytes() == raw_bytes


def test_failure_report_does_not_overwrite_conpty_matrix_or_w17(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    w17 = tmp_path / "w17.json"
    summary = tmp_path / "summary.json"
    matrix_bytes = b'{"schema_version":1,"status":"diagnosed"}\n'
    w17_bytes = b'{"schema_version":"W17-VALID"}\n'
    matrix.write_bytes(matrix_bytes)
    w17.write_bytes(w17_bytes)
    args = SimpleNamespace(summary_json=summary, w17_summary_json=w17, conpty_layer_matrix_json=matrix)

    verifier._v3_write_failure_outputs(args, {"schema_version": 3, "status": "failed"})

    assert matrix.read_bytes() == matrix_bytes
    assert w17.read_bytes() == w17_bytes
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


def test_cli_rejects_colliding_summary_and_matrix_paths_without_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = tmp_path / "shared.json"
    original = b'{"schema_version":1,"status":"diagnosed"}\n'
    shared.write_bytes(original)

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(shared), "--conpty-layer-matrix-json", str(shared)]
    )

    assert exit_code == 2
    assert "output paths must be distinct" in capsys.readouterr().err
    assert shared.read_bytes() == original


@pytest.mark.parametrize("collision", ("matrix_w17", "raw_summary", "raw_w17", "relative_absolute", "case_only"))
def test_cli_rejects_all_conpty_output_aliases_before_creating_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], collision: str
) -> None:
    if collision == "case_only" and os.name != "nt":
        pytest.skip("Windows paths are case-insensitive")
    matrix = tmp_path / "conpty-layer-matrix.json"
    raw = matrix.with_suffix(".raw.json")
    summary = tmp_path / "summary.json"
    w17 = tmp_path / "w17.json"
    args = ["--bundle-dir", str(tmp_path), "--conpty-layer-matrix-json", str(matrix)]
    if collision == "matrix_w17":
        args += ["--summary-json", str(summary), "--w17-summary-json", str(matrix)]
    elif collision == "raw_summary":
        args += ["--summary-json", str(raw)]
    elif collision == "raw_w17":
        args += ["--summary-json", str(summary), "--w17-summary-json", str(raw)]
    elif collision == "relative_absolute":
        relative = os.path.relpath(summary, Path.cwd())
        args += ["--summary-json", str(summary), "--w17-summary-json", relative]
    else:
        args += ["--summary-json", str(tmp_path / "W17.json"), "--w17-summary-json", str(tmp_path / "w17.json")]

    exit_code = verifier._v3_main(args)

    assert exit_code == 2
    assert "output paths must be distinct" in capsys.readouterr().err
    assert not summary.exists()
    assert not w17.exists()
    assert not matrix.exists()
    assert not raw.exists()


def test_success_summary_is_written_only_after_w17_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    w17 = tmp_path / "w17.json"
    args = SimpleNamespace(summary_json=summary, w17_summary_json=w17, conpty_layer_matrix_json=None)
    real_write_json = verifier._v3_write_json

    def fail_w17(path: Path | None, payload: Any) -> None:
        if path == w17:
            raise OSError("injected W17 evidence write failure")
        real_write_json(path, payload)

    monkeypatch.setattr(verifier, "_v3_write_json", fail_w17)

    with pytest.raises(OSError, match="W17 evidence write failure"):
        verifier._v3_write_success_outputs(
            args,
            {"status": "passed", "w17_installed_interactive": {"status": "passed"}},
        )
    assert not summary.exists()


def test_successful_cli_preserves_matrix_and_writes_pass_only_after_final_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix, summary, _original = _wire_completion(monkeypatch, tmp_path)
    w17 = tmp_path / "w17.json"
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--w17-summary-json", str(w17),
         "--conpty-layer-matrix-json", str(matrix)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "W18_INSTALLED_PRODUCT=PASS"
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(w17.read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(matrix.read_text(encoding="utf-8")) == {"schema_version": 1, "status": "diagnosed"}
    assert matrix.with_suffix(".raw.json").read_bytes() == _RAW_CAPTURE_BYTES


def test_final_config_guard_failure_prevents_pass_after_path_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix, summary, original = _wire_completion(monkeypatch, tmp_path)
    monkeypatch.setattr(
        verifier,
        "_assert_file_snapshot_unchanged",
        lambda *_args: (_ for _ in ()).throw(verifier.ProductVerificationError("injected config drift")),
    )
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt", environ=os.environ, fsync=os.fsync))

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--conpty-layer-matrix-json", str(matrix)]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "W18_INSTALLED_PRODUCT=PASS" not in output.out
    assert "final default-config integrity guard failed" in output.err
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"
    assert matrix.is_file()
    assert original.value == "%USER_BIN%;;C:\\odd\\;"


def test_matrix_writer_failure_leaves_no_partial_matrix_or_staging_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts import conpty_layer_matrix

    output = tmp_path / "matrix.json"
    context = SimpleNamespace(
        application_home=tmp_path / "home",
        install_root=tmp_path / "install-root",
        before_machine=None,
        before_user=_snapshot(),
        working=tmp_path,
        outside=tmp_path,
        report={"evidence_identity": {}},
    )
    facts = SimpleNamespace(candidate_id="w18-" + "a" * 32, stable=tmp_path / "stable.cmd")
    monkeypatch.setattr(verifier, "_registry_path_snapshot", lambda: _snapshot())
    monkeypatch.setattr(verifier, "_v3_clean_child_environment", lambda *_args: {})
    monkeypatch.setattr(verifier, "_v3_assert_no_checkout", lambda *_args: None)
    def build_with_capture(**kwargs: Any) -> dict[str, Any]:
        kwargs["raw_capture_path"].write_text(
            '{"schema_version":"W18-CONPTY-RAW-CAPTURE-V1","layers":{}}\n', encoding="utf-8"
        )
        return {"schema_version": 1, "status": "diagnosed"}

    monkeypatch.setattr(conpty_layer_matrix, "build_conpty_layer_matrix", build_with_capture)

    def fail_write(_matrix: Any, staged: Path) -> None:
        staged.write_bytes(b'{"partial":')
        raise OSError("injected matrix write failure")

    monkeypatch.setattr(conpty_layer_matrix, "write_conpty_layer_matrix", fail_write)

    with pytest.raises(verifier.ProductVerificationError, match="matrix production failed"):
        verifier._v3_verify_conpty_layer_matrix(context, facts, output)

    assert not output.exists()
    assert not output.with_suffix(".raw.json").exists()
    assert list(tmp_path.glob(".matrix.json.*.tmp")) == []
    assert list(tmp_path.glob(".*raw.json.*.tmp")) == []


def test_conpty_matrix_and_raw_capture_publish_with_distinct_schemas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts import conpty_layer_matrix

    output = tmp_path / "matrix.json"
    raw_document = {
        "schema_version": "W18-CONPTY-RAW-CAPTURE-V1",
        "candidate_id": "w18-" + "a" * 32,
        "layers": {"layer_1": {"raw_transcript_b64": "eA==", "raw_transcript_sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"}},
    }
    context = SimpleNamespace(
        application_home=tmp_path / "home",
        install_root=tmp_path / "install-root",
        before_machine=None,
        before_user=_snapshot(),
        working=tmp_path,
        outside=tmp_path,
        report={"evidence_identity": {}, "scenarios": {}},
    )
    facts = SimpleNamespace(candidate_id=raw_document["candidate_id"], stable=tmp_path / "stable.cmd")
    monkeypatch.setattr(verifier, "_registry_path_snapshot", lambda: _snapshot())
    monkeypatch.setattr(verifier, "_v3_clean_child_environment", lambda *_args: {})
    monkeypatch.setattr(verifier, "_v3_assert_no_checkout", lambda *_args: None)

    def build(**kwargs: Any) -> dict[str, Any]:
        kwargs["raw_capture_path"].write_text(json.dumps(raw_document), encoding="utf-8")
        return {
            "schema_version": conpty_layer_matrix.SCHEMA_VERSION,
            "status": "diagnosed",
            "candidate_id": facts.candidate_id,
        }

    monkeypatch.setattr(conpty_layer_matrix, "build_conpty_layer_matrix", build)

    matrix = verifier._v3_verify_conpty_layer_matrix(context, facts, output)

    assert matrix["schema_version"] == conpty_layer_matrix.SCHEMA_VERSION
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "diagnosed"
    assert json.loads(output.with_suffix(".raw.json").read_text(encoding="utf-8")) == raw_document
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_refuses_preexisting_raw_capture_without_overwriting_or_false_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix = tmp_path / "matrix.json"
    raw = matrix.with_suffix(".raw.json")
    raw_bytes = b'{"preexisting":"raw-capture"}\n'
    raw.write_bytes(raw_bytes)
    summary = tmp_path / "summary.json"

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--conpty-layer-matrix-json", str(matrix)]
    )

    assert exit_code == 2
    assert "existing ConPTY raw-capture" in capsys.readouterr().err
    assert raw.read_bytes() == raw_bytes
    assert not summary.exists()
    assert not matrix.exists()


def test_output_path_reparse_ancestor_fails_closed_when_symlink_creation_is_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "real-output"
    alias = tmp_path / "linked-output"
    target.mkdir()
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {type(exc).__name__}")
    matrix = tmp_path / "matrix.json"
    summary = target / "w17.json"
    w17 = alias / "w17.json"

    exit_code = verifier._v3_main(
        ["--bundle-dir", str(tmp_path), "--summary-json", str(summary), "--w17-summary-json", str(w17),
         "--conpty-layer-matrix-json", str(matrix)]
    )

    assert exit_code == 2
    assert "reparse-point evidence destination" in capsys.readouterr().err
    assert not summary.exists()
    assert not matrix.exists()
    assert not matrix.with_suffix(".raw.json").exists()
    alias.unlink()
