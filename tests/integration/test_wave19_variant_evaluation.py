from agent.evaluation.experiment import evaluation_context


def test_current_and_reference_contexts_have_distinct_compositions():
    current = evaluation_context("current", experiment_id="exp", trial_id="current")
    reference = evaluation_context("persona-reference-w18", experiment_id="exp", trial_id="reference")
    assert current.profile.composition.fingerprint != reference.profile.composition.fingerprint
