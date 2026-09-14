from scripts.run_wave17_adversarial import run_campaign


def test_wave17_adversarial_campaign_is_complete_and_green() -> None:
    results = run_campaign()
    assert len(results) == 64
    assert [item.scenario_id for item in results] == [f"W17-A{index:02d}" for index in range(1, 65)]
    assert all(item.passed for item in results), [item for item in results if not item.passed]
