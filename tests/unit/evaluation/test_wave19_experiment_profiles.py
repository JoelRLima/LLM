from agent.evaluation.experiment import evaluation_context


def test_builtin_profiles_are_explicit_experiments():
    context = evaluation_context("current", experiment_id="exp", trial_id="trial")
    assert context.profile.profile_id == "current"
    assert context.profile.composition.experiment_id == "exp"
