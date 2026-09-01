import pytest

from scripts.check_wave5_architecture import check_source, run_checks


def _gates(source: str, relative: str) -> set[str]:
    return {finding.split(":", 1)[0] for finding in check_source(source, relative)}


@pytest.mark.parametrize(
    "source",
    (
        """
import os

def review_and_commit(requested, proposed):
    temporary = requested.with_suffix(".tmp")
    temporary.write_text(proposed)
    os.replace(temporary, requested)
""",
        """
import shutil

def review_and_commit(staged, requested):
    shutil.copy2(staged, requested)
""",
        """
def review_and_commit(requested, proposed):
    requested.unlink()
""",
    ),
)
def test_s1_rejects_direct_filewriter_target_commit(source: str) -> None:
    gates = _gates(source, "agent/skills/file_writer_runtime.py")
    assert "W5-S1" in gates
    assert "W5-S4" in gates


@pytest.mark.parametrize(
    "source",
    (
        """
def _save_code(path, code):
    path.write_text(code)
""",
        """
def _save_code(path, code):
    with path.open("w") as stream:
        stream.write(code)
""",
    ),
)
def test_s3_rejects_direct_code_workflow_target_commit(source: str) -> None:
    gates = _gates(source, "agent/code/workflow_application.py")
    assert "W5-S3" in gates
    assert "W5-S4" in gates


def test_s3_requires_the_existing_code_workflow_transaction_boundary() -> None:
    source = """
def apply_changes(changes):
    return changes
"""
    assert "W5-S3" in _gates(source, "agent/code/workflow_application.py")


def test_transaction_owner_and_scratch_helpers_are_allowed() -> None:
    transaction = """
def _atomic_write(target, content):
    target.write_bytes(content)
"""
    scratch = """
def _write(target, args):
    target.write_text(str(args.get("content", "")))
"""
    generated_test = """
import os

def _run_generated_tests(test_file, combined):
    test_file.write(combined)
    os.remove(test_file)
"""
    assert _gates(transaction, "agent/code/change_transaction.py") == set()
    assert _gates(scratch, "agent/skills/file_writer_runtime.py") == set()
    assert _gates(generated_test, "agent/code/validation_process.py") == set()


@pytest.mark.parametrize(
    "relative",
    ("agent/memory.py", "agent/skills/shell_process.py"),
)
def test_s5_keeps_memory_and_bounded_process_owners_separate(relative: str) -> None:
    source = "from agent.code.changes import ChangeSetTransaction"
    assert "W5-S5" in _gates(source, relative)


def test_s6_rejects_a_new_universal_effect_executor() -> None:
    source = """
class EffectExecutor:
    pass
"""
    assert "W5-S6" in _gates(source, "agent/new_runtime.py")


def test_canonical_transaction_boundary_is_accepted() -> None:
    source = """
from agent.code.changes import ChangeSetTransaction

def review_and_commit(requested, proposed):
    transaction = ChangeSetTransaction(requested.parent, [])
    transaction.commit()
"""
    assert _gates(source, "agent/skills/file_writer_runtime.py") == set()


def test_hardened_repository_checker_passes() -> None:
    assert run_checks() == []
