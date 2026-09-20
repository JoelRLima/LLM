from agent.evaluation.feedback import FeedbackService


def test_feedback_service_is_application_evidence_owner():
    assert FeedbackService is not None
