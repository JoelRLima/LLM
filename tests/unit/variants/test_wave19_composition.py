from agent.variants.models import VariantComposition


def test_production_composition_has_frozen_fingerprint():
    composition = VariantComposition.production_current()
    assert composition.fingerprint == "725ebde0ac00013ffb37552885126cecbbd83bf0f1cf4f9c0963f536779c58e8"
