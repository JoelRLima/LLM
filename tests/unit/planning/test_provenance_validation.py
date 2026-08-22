import pytest

from agent.planning.provenance_validation import (
    find_unresolved_symbolic_reference,
    validate_unresolved_symbolic_arguments,
)


@pytest.mark.parametrize(
    ("value", "marker"),
    [
        ("${2.file}", "${2.file}"),
        ("$ref:2.file", "$ref"),
        ("{{2.file}}", "{{2.file}}"),
        ("@{2.file}", "@{2.file}"),
        ("<<2.file>>", "<<2.file>>"),
        ("[[2.file]]", "[[2.file]]"),
        ("{2.file}", "{2.file}"),
        ("result(2.file)", "result(2.file)"),
    ],
)
def test_model_reference_families_are_detected(value: str, marker: str) -> None:
    assert find_unresolved_symbolic_reference(value) == marker


def test_unresolved_nested_planner_argument_is_rejected() -> None:
    error = validate_unresolved_symbolic_arguments(
        args={"content": {"items": ["safe", "${2.file}"]}},
        objective="Leia os arquivos.",
        available_observations=(),
    )

    assert error is not None
    assert "args.content.items[1]" in error
    assert "${2.file}" in error


@pytest.mark.parametrize("value", ["${2.file}", "$ref:2.file", "{{2.file}}"])
def test_exact_user_literal_is_not_reinterpreted_as_binding(value: str) -> None:
    assert (
        validate_unresolved_symbolic_arguments(
            args={"file_path": value},
            objective=f'Abra literalmente o nome "{value}".',
            available_observations=(),
        )
        is None
    )
