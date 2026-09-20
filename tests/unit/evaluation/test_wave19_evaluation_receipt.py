from agent.evaluation.receipt import EVALUATION_RECEIPT_SCHEMA_VERSION


def test_receipt_schema_is_frozen():
    assert EVALUATION_RECEIPT_SCHEMA_VERSION == 1
