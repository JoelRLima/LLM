import json
import os
import subprocess
from pathlib import Path

import pytest

import agent.skills.repository_state as repository_state_module
from agent.approval import AutoApprove
from agent.llm.context_projection import UNTRUSTED_REPOSITORY_STATE
from agent.llm.context_view_support import repository_state_records
from agent.resources.contracts import WORKSPACE_RESOURCE
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.skills.repository_state import (
    MAX_METADATA_ENTRIES,
    RepositoryStateSkill,
    RepositoryStateSnapshot,
    _fixed_status_argv,
    _parse_porcelain_v2,
    _RepositoryStateError,
)
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import CancellationSafetyMode, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.invocation_semantics import resolve_invocation_semantics
from agent.tools.tool_registry import ToolRegistry

_OID = "a" * 40


def _minimal_git(root: Path, *, config: str | None = None) -> Path:
    git_dir = root / ".git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs").mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        config
        or "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
        encoding="utf-8",
    )
    return git_dir


def _status_bytes(*records: bytes) -> bytes:
    return b"\x00".join(records) + b"\x00"


def _completed(stdout: bytes, argv: list[str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv or _fixed_status_argv(), 0, stdout, b"")


def test_repository_state_owner_is_canonical_and_gateway_invocable(tmp_path: Path) -> None:
    descriptors = BuiltinToolAdapter(
        load_skill_registry(base_dir=tmp_path)
    ).descriptors()
    descriptor = next(item for item in descriptors if item.name == "repository_state")

    assert descriptor.capabilities == {"read", "vcs_read", "process"}
    assert descriptor.schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert descriptor.cancellation_safety is CancellationSafetyMode.PROCESS_KILLABLE
    semantics = resolve_invocation_semantics(descriptor, {})
    assert semantics.read_only is True
    assert semantics.workspace_mutation is False
    assert semantics.resource_access[0].name == WORKSPACE_RESOURCE

    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path)))
    result = ToolInvocationGateway(registry, approval_port=AutoApprove()).run(
        "repository_state", {}, record_result=False
    )

    assert result.status is ToolStatus.SUCCEEDED
    assert result.data["available"] is False
    assert result.data["reason_code"] == "NOT_GIT_REPOSITORY"


def test_porcelain_v2_parses_tracked_unmerged_untracked_and_ignored_bytes(
    tmp_path: Path,
) -> None:
    snapshot = _parse_porcelain_v2(
        tmp_path,
        _status_bytes(
            f"# branch.oid {_OID}".encode(),
            b"# branch.head main",
            b"# branch.ab +1 -2",
            f"1 .M N... 100644 100644 100644 {_OID} {_OID} src/main.py".encode(),
            f"u UU N... 100644 100644 100644 100644 {_OID} {_OID} {_OID} merge.py".encode(),
            b"? new-directory/",
            b"! ignored.tmp",
        ),
    )

    assert snapshot.available is True
    assert snapshot.branch == "main"
    assert snapshot.head == _OID
    assert snapshot.total_entries_observed == 4
    assert [(entry.path, entry.index_status, entry.worktree_status, entry.untracked) for entry in snapshot.entries] == [
        ("src/main.py", ".", "M", False),
        ("merge.py", "U", "U", False),
        ("new-directory", "?", "?", True),
        ("ignored.tmp", "!", "!", False),
    ]


@pytest.mark.parametrize(
    "record",
    [
        b"2 R. N... 100644 100644 " + _OID.encode() + b" old.py new.py",
        b"1 ZZ N... 100644 100644 100644 " + _OID.encode() + b" " + _OID.encode() + b" file.py",
    ],
)
def test_porcelain_v2_unexpected_or_malformed_records_fail_closed(
    tmp_path: Path, record: bytes
) -> None:
    with pytest.raises(_RepositoryStateError, match="MALFORMED_PORCELAIN"):
        _parse_porcelain_v2(
            tmp_path,
            _status_bytes(
                f"# branch.oid {_OID}".encode(),
                b"# branch.head main",
                record,
            ),
        )


def test_porcelain_v2_detached_and_unborn_heads_are_deterministic(tmp_path: Path) -> None:
    detached = _parse_porcelain_v2(
        tmp_path,
        _status_bytes(f"# branch.oid {_OID}".encode(), b"# branch.head (detached)"),
    )
    unborn = _parse_porcelain_v2(
        tmp_path,
        _status_bytes(b"# branch.oid (initial)", b"# branch.head topic"),
    )

    assert (detached.branch, detached.head) == (None, _OID)
    assert (unborn.branch, unborn.head) == ("topic", None)


def test_porcelain_v2_invalid_utf8_path_invalidates_entire_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_git(tmp_path)
    raw = _status_bytes(
        f"# branch.oid {_OID}".encode(),
        b"# branch.head main",
        b"? invalid-\xff.py",
    )
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: _completed(raw),
    )

    result = RepositoryStateSkill(tmp_path).execute({})

    assert result["data"]["available"] is False
    assert result["data"]["reason_code"] == "MALFORMED_PORCELAIN"
    assert "�" not in json.dumps(result, ensure_ascii=False)


def test_repository_state_bounds_entries_and_context_without_false_cleanliness(
    tmp_path: Path,
) -> None:
    records = [
        f"# branch.oid {_OID}".encode(),
        b"# branch.head main",
        *[f"? file-{index:03}.py".encode() for index in range(257)],
    ]
    snapshot = _parse_porcelain_v2(tmp_path, _status_bytes(*records))
    context = snapshot.to_context_dict()

    assert len(snapshot.entries) == 256
    assert snapshot.total_entries_observed == 257
    assert snapshot.truncated is True
    assert snapshot.complete is False
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 4096
    assert context["complete"] is False
    assert "file-256.py" not in {item["path"] for item in context["entries"]}


def test_unsafe_repository_config_and_metadata_are_rejected_before_git_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_git(
        tmp_path,
        config="[core]\n\trepositoryformatversion = 0\n[include]\n\tpath = outside.ini\n",
    )
    calls: list[object] = []
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = RepositoryStateSkill(tmp_path).execute({})

    assert result["data"]["reason_code"] == "UNSAFE_REPOSITORY_CONFIG"
    assert calls == []

    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    _minimal_git(
        safe_root,
        config=(
            "[remote \"origin\"]\n"
            "\turl = https://example.test/include-filter-hooksPath\n"
            "[branch \"Feature\"]\n"
            "\tmerge = refs/heads/main\n"
        ),
    )
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: _completed(
            _status_bytes(f"# branch.oid {_OID}".encode(), b"# branch.head Feature")
        ),
    )
    safe_result = RepositoryStateSkill(safe_root).execute({})
    assert safe_result["data"]["available"] is True


def test_metadata_walk_overflow_and_linked_descendant_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "overflow"
    root.mkdir()
    git_dir = _minimal_git(root)
    (git_dir / "objects" / "one").write_bytes(b"")
    (git_dir / "objects" / "two").write_bytes(b"")
    monkeypatch.setattr(repository_state_module, "MAX_METADATA_ENTRIES", 1)
    calls: list[object] = []
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    overflow_result = RepositoryStateSkill(root).execute({})
    assert overflow_result["data"]["reason_code"] == "METADATA_LIMIT"
    assert calls == []
    assert MAX_METADATA_ENTRIES == 50_000

    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    linked_git = _minimal_git(linked_root)
    outside = tmp_path / "outside-objects"
    outside.mkdir()
    try:
        (linked_git / "objects" / "aa").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    linked_result = RepositoryStateSkill(linked_root).execute({})
    assert linked_result["data"]["reason_code"] in {"UNSAFE_PATH", "GITDIR_OUTSIDE_WORKSPACE"}


def test_fixed_status_uses_one_confined_call_and_runtime_repository_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_git(tmp_path)
    captured: dict[str, object] = {}
    raw = _status_bytes(f"# branch.oid {_OID}".encode(), b"# branch.head main")

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return _completed(raw, argv)

    monkeypatch.setenv("GIT_DIR", "outside")
    monkeypatch.setenv("GIT_TRACE", "outside-trace")
    monkeypatch.setattr(repository_state_module, "run_bounded_process", fake_run)

    result = RepositoryStateSkill(tmp_path).execute({})

    assert result["data"]["available"] is True
    assert captured["argv"] == _fixed_status_argv()
    assert captured["binary_output"] is True
    environment = captured["environment"]
    assert environment["GIT_DIR"] == str((tmp_path / ".git").resolve())
    assert environment["GIT_WORK_TREE"] == str(tmp_path.resolve())
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "GIT_TRACE" not in environment


def test_context_consumes_only_fresh_observation_and_never_spawns_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = RepositoryStateSnapshot(
        True,
        "main",
        _OID,
        (),
        False,
        True,
        0,
        None,
    )
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("context projection must not spawn Git")
        ),
    )
    history = [
        {
            "tool": "repository_state",
            "invocation_id": "repo-1",
            "result": {"status": "succeeded", "data": snapshot.to_dict()},
        }
    ]

    records = repository_state_records(history)
    assert len(records) == 1
    assert records[0].trust_class == UNTRUSTED_REPOSITORY_STATE
    assert records[0].data["entries"] == []
    assert "diff" not in json.dumps(records[0].data, ensure_ascii=False).casefold()

    history.append(
        {
            "tool": "file_writer",
            "result": {
                "status": "succeeded",
                "metadata": {"mutation_occurred": True},
            },
        }
    )
    assert repository_state_records(history) == ()


def test_non_git_parent_is_not_discovered_or_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "nested"
    workspace.mkdir()
    calls: list[object] = []
    monkeypatch.setattr(
        repository_state_module,
        "run_bounded_process",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = RepositoryStateSkill(
        workspace=WorkspaceContext.create(workspace)
    ).execute({})

    assert result["data"] == {
        "available": False,
        "branch": None,
        "head": None,
        "entries": [],
        "truncated": False,
        "complete": False,
        "total_entries_observed": None,
        "reason_code": "NOT_GIT_REPOSITORY",
    }
    assert calls == []
