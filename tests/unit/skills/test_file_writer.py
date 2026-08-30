import pytest

from agent.approval import ApprovalDecision, AutoApprove, RequireExplicitApproval
from agent.code.changes import ChangeKind, ChangeSetTransaction
from agent.skills import file_writer_runtime
from agent.skills.file_writer import FileWriterSkill
from agent.workspace import WorkspaceManager


def test_file_writer_blocks_agent_directory(tmp_path, monkeypatch):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    target = base_dir / "agent" / "orchestrator.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is False
    assert "core do agente" in reason.lower()


def test_file_writer_allows_non_agent_file(tmp_path):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    target = base_dir / "README.md"
    target.write_text("ok", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is True
    assert reason == ""


def test_file_writer_respects_allowlist(tmp_path, monkeypatch):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    monkeypatch.setattr("agent.skills.file_writer.AGENT_EDIT_ALLOWLIST", {"agent/orchestrator.py"})
    target = base_dir / "agent" / "orchestrator.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is True
    assert reason == ""


def test_ast_patch_commits_to_original_file(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    writer = FileWriterSkill(base_dir=str(tmp_path), auto_confirm=True)

    result = writer.execute(
        {
            "action": "ast_patch",
            "file_path": "sample.py",
            "target": "value",
            "new_code": "def value():\n    return 2",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert not list(tmp_path.rglob("*.ast_bak"))


def test_ast_patch_preserves_nested_indentation(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text(
        "class Example:\n    def value(self):\n        return 1\n",
        encoding="utf-8",
    )
    writer = FileWriterSkill(base_dir=str(tmp_path), config={"auto_confirm": True})

    result = writer.execute(
        {
            "action": "ast_patch",
            "file_path": "sample.py",
            "target": "value",
            "new_code": "def value(self):\n    if True:\n        return 2",
        }
    )

    assert result["ok"] is True
    compile(target.read_text(encoding="utf-8"), str(target), "exec")


def test_file_writer_uses_injected_scratch_directory(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "state" / "scratch"
    workspace.mkdir()
    target = workspace / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    writer = FileWriterSkill(
        base_dir=workspace,
        scratch_dir=scratch,
        auto_confirm=True,
    )

    result = writer.execute(
        {
            "action": "write",
            "file_path": "sample.py",
            "content": "value = 2\n",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert scratch.is_dir()
    assert not (workspace / ".temp_analysis").exists()


def test_explicit_auto_confirm_overrides_config(tmp_path):
    writer = FileWriterSkill(
        base_dir=tmp_path,
        config={"auto_confirm": True},
        auto_confirm=False,
    )

    assert writer._is_auto_confirm() is False


def test_file_writer_requires_approval_without_reading_stdin(tmp_path, monkeypatch):
    target = tmp_path / "pending.txt"
    writer = FileWriterSkill(
        base_dir=tmp_path,
        approval_policy=RequireExplicitApproval(),
    )

    def forbidden_input(*args, **kwargs):
        raise AssertionError(f"input() não pode ser usado: {args!r} {kwargs!r}")

    monkeypatch.setattr("builtins.input", forbidden_input)

    result = writer.execute(
        {
            "action": "write",
            "file_path": "pending.txt",
            "content": "não aplicar\n",
        }
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"
    assert not target.exists()


def test_file_writer_final_commit_uses_changeset_transaction(tmp_path, monkeypatch):
    target = tmp_path / 'sample.txt'
    target.write_text('antes\n', encoding='utf-8')
    writer = FileWriterSkill(base_dir=tmp_path, auto_confirm=True)
    commits = []
    original_commit = ChangeSetTransaction.commit

    def record_commit(transaction):
        commits.append(transaction)
        return original_commit(transaction)

    monkeypatch.setattr(file_writer_runtime, 'ChangeSetTransaction', ChangeSetTransaction, raising=False)
    monkeypatch.setattr(ChangeSetTransaction, 'commit', record_commit)

    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'sample.txt',
            'content': 'depois\n',
        }
    )

    assert result['ok'] is True
    assert len(commits) == 1
    change = commits[0].change_set.changes[0]
    assert change.kind is ChangeKind.MODIFY
    assert change.base_hash is not None
    assert target.read_text(encoding='utf-8') == 'depois\n'


def test_file_writer_registers_transaction_for_task_rollback(tmp_path):
    target = tmp_path / 'sample.txt'
    target.write_text('antes\n', encoding='utf-8')
    manager = WorkspaceManager(
        workspace_root=tmp_path,
        restore_points_dir=tmp_path / 'restore',
    )
    writer = FileWriterSkill(base_dir=tmp_path, auto_confirm=True)
    writer.workspace_manager = manager

    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'sample.txt',
            'content': 'depois\n',
        }
    )

    assert result['ok'] is True
    assert len(manager._task_transactions) == 1
    assert manager.rollback() is True
    assert target.read_text(encoding='utf-8') == 'antes\n'

def test_file_writer_create_uses_create_transaction_and_rollback(tmp_path, monkeypatch):
    manager = WorkspaceManager(
        workspace_root=tmp_path,
        restore_points_dir=tmp_path / 'restore',
    )
    writer = FileWriterSkill(
        base_dir=tmp_path,
        auto_confirm=True,
        workspace_manager=manager,
    )
    commits = []
    original_commit = ChangeSetTransaction.commit

    def record_commit(transaction):
        commits.append(transaction)
        return original_commit(transaction)

    monkeypatch.setattr(ChangeSetTransaction, 'commit', record_commit)

    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'created.txt',
            'content': 'created\n',
        }
    )

    assert result['ok'] is True
    assert commits[0].change_set.changes[0].kind is ChangeKind.CREATE
    assert (tmp_path / 'created.txt').read_text(encoding='utf-8') == 'created\n'
    assert manager.rollback() is True
    assert not (tmp_path / 'created.txt').exists()


@pytest.mark.parametrize(
    ('action', 'initial', 'arguments', 'expected'),
    (
        ('append', 'one\n', {'content': 'two\n'}, 'one\ntwo\n'),
        ('patch', 'one two\n', {'old_content': 'two', 'new_content': 'three'}, 'one three\n'),
        ('delete_lines', 'one\ntwo\nthree\n', {'start_line': 2, 'end_line': 2}, 'one\nthree\n'),
        (
            'ast_patch',
            'def value():\n    return 1\n',
            {'target': 'value', 'new_code': 'def value():\n    return 2'},
            'def value():\n    return 2\n',
        ),
    ),
)
def test_file_writer_edit_actions_commit_exact_content(
    tmp_path, action, initial, arguments, expected
):
    target = tmp_path / 'sample.py'
    target.write_text(initial, encoding='utf-8')
    writer = FileWriterSkill(base_dir=tmp_path, auto_confirm=True)

    result = writer.execute(
        {
            'action': action,
            'file_path': 'sample.py',
            **arguments,
        }
    )

    assert result['ok'] is True
    assert target.read_text(encoding='utf-8') == expected


def test_file_writer_noop_does_not_commit_or_claim_mutation(tmp_path, monkeypatch):
    target = tmp_path / 'same.txt'
    target.write_text('same\n', encoding='utf-8')
    writer = FileWriterSkill(base_dir=tmp_path, auto_confirm=True)

    def fail_commit(_transaction):
        raise AssertionError('no-op must not commit')

    monkeypatch.setattr(ChangeSetTransaction, 'commit', fail_commit)
    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'same.txt',
            'content': 'same\n',
        }
    )

    assert result['ok'] is True
    assert result['mutation_occurred'] is False
    assert result['persisted_mutation'] is False
    assert target.read_text(encoding='utf-8') == 'same\n'


def test_file_writer_conflict_preserves_external_bytes(tmp_path):
    target = tmp_path / 'conflict.txt'
    target.write_text('original\n', encoding='utf-8')

    class MutatingApproval:
        def request(self, _request):
            target.write_text('external\n', encoding='utf-8')
            return ApprovalDecision.APPROVED

    writer = FileWriterSkill(
        base_dir=tmp_path,
        approval_policy=MutatingApproval(),
    )
    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'conflict.txt',
            'content': 'proposal\n',
        }
    )

    assert result['ok'] is False
    assert target.read_text(encoding='utf-8') == 'external\n'
    assert result['persisted_mutation'] is False


def test_file_writer_commit_failure_reports_restored_without_success(tmp_path, monkeypatch):
    target = tmp_path / 'failure.txt'
    target.write_text('original\n', encoding='utf-8')
    writer = FileWriterSkill(base_dir=tmp_path, auto_confirm=True)

    def fail_atomic(_transaction, _path, _content):
        raise OSError('injected commit failure')

    monkeypatch.setattr(ChangeSetTransaction, '_atomic_write', fail_atomic)
    result = writer.execute(
        {
            'action': 'write',
            'file_path': 'failure.txt',
            'content': 'proposal\n',
        }
    )

    assert result['ok'] is False
    assert result['rollback_occurred'] is True
    assert result['final_state'] == 'restored'
    assert result['persisted_mutation'] is False
    assert target.read_text(encoding='utf-8') == 'original\n'

def test_injected_auto_approval_applies_without_reading_stdin(tmp_path, monkeypatch):
    target = tmp_path / "approved.txt"
    writer = FileWriterSkill(
        base_dir=tmp_path,
        approval_policy=AutoApprove(),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("input() não pode ser usado")
        ),
    )

    result = writer.execute(
        {
            "action": "write",
            "file_path": "approved.txt",
            "content": "aplicado\n",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "aplicado\n"
