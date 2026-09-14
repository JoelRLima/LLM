from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace

import pytest

from agent.approval import ApprovalDecision, ApprovalRequest, ApprovalWaitCancelled
from agent.code.change_models import ChangePreview
from agent.code.policy import ProposalAssessment
from agent.interfaces.cli import interactive_commands
from agent.interfaces.cli.attention import ApprovalBroker


def _request(name: str = "write") -> ApprovalRequest:
    return ApprovalRequest(
        action=name,
        resource="workspace/file.txt",
        prompt="apply concrete operation",
        metadata={"task_id": "task-1", "invocation_id": "inv-1", "concrete_args_sha256": "abc"},
    )


def _start_request(broker: ApprovalBroker) -> tuple[Thread, list[object], Event]:
    values: list[object] = []
    started = Event()

    def run() -> None:
        started.set()
        try:
            values.append(broker.request(_request()))
        except BaseException as exc:
            values.append(exc)

    thread = Thread(target=run)
    thread.start()
    assert started.wait(2)
    for _ in range(100):
        if broker.current() is not None:
            break
        Event().wait(0.01)
    return thread, values, started


def test_matching_approval_resolves_once_and_wrong_identity_cannot_release_waiter() -> None:
    broker = ApprovalBroker()
    broker.bind_generation(7)
    thread, values, _ = _start_request(broker)
    current = broker.current()
    assert current is not None
    assert current.identity.run_generation == 7
    assert broker.resolve(current.identity.attention_id + 1, ApprovalDecision.APPROVED) == "IGNORED_STALE"
    assert broker.resolve(
        current.identity.attention_id,
        ApprovalDecision.APPROVED,
        run_generation=8,
    ) == "IGNORED_STALE"
    assert broker.resolve(
        current.identity.attention_id,
        ApprovalDecision.APPROVED,
        run_generation=7,
        request_fingerprint=current.identity.request_fingerprint,
    ) == "RESOLVED"
    assert broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED) == "IGNORED_DUPLICATE"
    thread.join(2)
    assert values == [ApprovalDecision.APPROVED]


def test_cancel_and_shutdown_raise_dedicated_wait_cancellation() -> None:
    broker = ApprovalBroker()
    broker.bind_generation(3)
    thread, values, _ = _start_request(broker)
    current = broker.current()
    assert current is not None
    assert broker.invalidate(attention_id=current.identity.attention_id, generation=3)
    assert broker.resolve(current.identity.attention_id, ApprovalDecision.APPROVED) == "IGNORED_STALE"
    thread.join(2)
    assert len(values) == 1 and isinstance(values[0], ApprovalWaitCancelled)

    broker2 = ApprovalBroker()
    broker2.bind_generation(4)
    thread2, values2, _ = _start_request(broker2)
    broker2.shutdown()
    thread2.join(2)
    assert len(values2) == 1 and isinstance(values2[0], ApprovalWaitCancelled)


def test_second_unresolved_request_fails_closed_without_overwrite() -> None:
    broker = ApprovalBroker()
    broker.bind_generation(1)
    thread, values, _ = _start_request(broker)
    with pytest.raises(RuntimeError, match="second unresolved"):
        broker.request(_request("second"))
    current = broker.current()
    assert current is not None
    broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED)
    thread.join(2)
    assert values == [ApprovalDecision.REJECTED]


def test_approval_wins_when_resolution_linearizes_before_invalidation() -> None:
    broker = ApprovalBroker()
    broker.bind_generation(2)
    thread, values, _ = _start_request(broker)
    current = broker.current()
    assert current is not None
    assert broker.resolve(current.identity.attention_id, ApprovalDecision.APPROVED) == "RESOLVED"
    assert broker.invalidate(attention_id=current.identity.attention_id, generation=2) is False
    thread.join(2)
    assert values == [ApprovalDecision.APPROVED]


def test_attention_details_exposes_the_exact_bounded_proposed_diff_without_new_request() -> None:
    broker = ApprovalBroker()
    broker.bind_generation(9)
    diff = "--- a/file.py\n+++ b/file.py\n@@\n+return 42\n"
    preview = ChangePreview("change-1", ("file.py",), diff)
    assessment = ProposalAssessment(0.8, True, ("needs confirmation",))
    values: list[object] = []
    thread = Thread(target=lambda: values.append(broker.approve_change(preview, assessment)))
    thread.start()
    for _ in range(100):
        if broker.current() is not None:
            break
        Event().wait(0.01)
    current = broker.current()
    assert current is not None
    assert current.request.metadata["proposed_diff"] == diff
    assert current.identity.metadata["proposed_diff"] == diff
    assert current.request.metadata["proposed_diff_sha256"]

    output: list[str] = []
    context = SimpleNamespace(
        approval_broker=broker,
        shell=SimpleNamespace(print_background=lambda value: output.append(str(value))),
    )
    interactive_commands.attention("/attention details", context)
    assert diff in "\n".join(output)
    assert broker.current() is not None
    broker.resolve(
        current.identity.attention_id,
        ApprovalDecision.REJECTED,
        run_generation=current.identity.run_generation,
        request_fingerprint=current.identity.request_fingerprint,
    )
    thread.join(2)
    assert values == [False]


def test_attention_details_marks_diff_truncation_and_binds_original_digest() -> None:
    from agent.interfaces.cli.attention import MAX_REVIEW_DIFF_CHARS

    broker = ApprovalBroker()
    broker.bind_generation(10)
    diff = "+" + ("x" * (MAX_REVIEW_DIFF_CHARS + 50))
    preview = ChangePreview("change-2", ("large.py",), diff)
    assessment = ProposalAssessment(0.5, True)
    values: list[object] = []

    def wait_for_approval() -> None:
        try:
            values.append(broker.approve_change(preview, assessment))
        except ApprovalWaitCancelled as exc:
            values.append(exc)

    thread = Thread(target=wait_for_approval)
    thread.start()
    for _ in range(100):
        if broker.current() is not None:
            break
        Event().wait(0.01)
    current = broker.current()
    assert current is not None
    assert current.request.metadata["proposed_diff_truncated"] is True
    assert len(current.request.metadata["proposed_diff"]) == MAX_REVIEW_DIFF_CHARS
    output: list[str] = []
    interactive_commands.attention(
        "/attention details",
        SimpleNamespace(approval_broker=broker, shell=SimpleNamespace(print_background=lambda value: output.append(str(value)))),
    )
    rendered = "\n".join(output)
    assert "diff truncated" in rendered
    assert current.request.metadata["proposed_diff_sha256"] in current.identity.request_fingerprint or current.request.metadata["proposed_diff"] in rendered
    broker.invalidate(attention_id=current.identity.attention_id, generation=10)
    thread.join(2)
    assert values and isinstance(values[0], ApprovalWaitCancelled)
