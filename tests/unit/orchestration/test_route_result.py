import pytest

from agent.orchestration.route_result import RouteDisposition, RouteResult


def test_route_result_preserves_typed_disposition_and_answer() -> None:
    result = RouteResult.handled("security", answer="ok", reason_code="ANALYZED")

    assert result.route == "security"
    assert result.disposition is RouteDisposition.HANDLED
    assert result.answer == "ok"
    assert result.reason_code == "ANALYZED"


def test_route_result_distinguishes_not_applicable_from_fallback() -> None:
    not_applicable = RouteResult.not_applicable("hierarchical", reason_code="NOT_HIERARCHICAL")
    fallback = RouteResult.fallback(
        "hierarchical",
        reason_code="HIERARCHICAL_PLANNER_ERROR",
        detail="planner unavailable",
    )

    assert not_applicable.disposition is RouteDisposition.NOT_APPLICABLE
    assert fallback.disposition is RouteDisposition.FALLBACK
    assert fallback.reason_code != not_applicable.reason_code


def test_route_result_requires_a_non_empty_route() -> None:
    with pytest.raises(ValueError):
        RouteResult.not_applicable(" ")


def test_route_result_coerces_wire_disposition_values() -> None:
    result = RouteResult("security", "fallback", reason_code="UNAVAILABLE")

    assert result.disposition is RouteDisposition.FALLBACK
