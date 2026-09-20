from agent.evaluation.feedback import FEEDBACK_SCHEMA_VERSION, FeedbackVerdict


def test_feedback_has_independent_schema_and_verdicts():
    assert FEEDBACK_SCHEMA_VERSION == 1
    assert FeedbackVerdict.CORRECT.value == "correct"
