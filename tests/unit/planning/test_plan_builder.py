import pytest

from agent.planning.plan_builder import build_planner_tools_description


def test_planner_renderer_type_error_is_not_reinterpreted_as_legacy_signature() -> None:
    class _Orchestrator:
        planning_context = None

        def _build_tools_description(self, *, compact=False, planner_kind=None):
            del compact, planner_kind
            raise TypeError("erro interno do renderer")

    with pytest.raises(TypeError, match="erro interno do renderer"):
        build_planner_tools_description(_Orchestrator(), planner_kind="linear", compact=True)


def test_legacy_signature_is_used_only_without_canonical_context() -> None:
    class _Orchestrator:
        planning_context = None

        def _build_tools_description(self, *, compact=False):
            return "legacy" if compact else "full"

    assert build_planner_tools_description(
        _Orchestrator(), planner_kind="linear", compact=True
    ) == "legacy"
