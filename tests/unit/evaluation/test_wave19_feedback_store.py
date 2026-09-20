from agent.evaluation.feedback_store import FeedbackStore


def test_feedback_store_owner_exists():
    assert FeedbackStore is not None
