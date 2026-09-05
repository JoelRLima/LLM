"""C1 preservation and immutability checks for the shared lexical owner."""

from dataclasses import FrozenInstanceError

import pytest

from agent.evaluation.practical_scenarios import PRACTICAL_V1
from agent.planning.task_semantics_inference import infer_effect_semantics
from agent.planning.task_semantics_lexicon import (
    CANONICAL_SEMANTIC_LEXICON,
    LexemeClass,
    LexiconRoute,
)
from agent.planning.task_semantics_positive_proof import parse_objective_authority


@pytest.mark.parametrize(
    ("objective", "requested", "prohibited", "intents"),
    (
        (
            "edit foo.py",
            ("write",),
            (),
            (("write", "foo.py", "requested"),),
        ),
        (
            "read source.md; edit target.py",
            ("write",),
            (),
            (("write", "target.py", "requested"),),
        ),
        (
            "n\u00e3o edite foo.py; edite bar.py",
            ("write",),
            ("write",),
            (
                ("write", "foo.py", "prohibited"),
                ("write", "bar.py", "requested"),
            ),
        ),
        (
            "resuma foo.py e salve em resumo.md",
            ("write",),
            (),
            (("write", "resumo.md", "requested"),),
        ),
        ("generate output using config.yml", (), (), ()),
        (
            "remova da mem\u00f3ria a prefer\u00eancia antiga",
            ("memory_write",),
            (),
            (("memory_write", "memory", "requested"),),
        ),
    ),
)
def test_c1_candidate_projection_preserves_existing_effects(
    objective: str,
    requested: tuple[str, ...],
    prohibited: tuple[str, ...],
    intents: tuple[tuple[str, str, str], ...],
) -> None:
    semantics = infer_effect_semantics(objective)

    assert semantics.requested == requested
    assert semantics.prohibited == prohibited
    assert tuple(
        (item.effect, item.target, item.polarity) for item in semantics.intents
    ) == intents


@pytest.mark.parametrize(
    ("objective", "complete", "proofs", "constraints"),
    (
        ("edit foo.py", True, (("write", "foo.py"),), ()),
        (
            "read source.md; edit target.py",
            True,
            (("write", "target.py"),),
            (),
        ),
        (
            "analyze source.md, note; edit target.py",
            False,
            (),
            (),
        ),
        (
            "edit foo.py; do not write",
            True,
            (("write", "foo.py"),),
            (("write", "*"),),
        ),
        (
            "manager asked me to edit foo.py",
            False,
            (),
            (),
        ),
        (
            "save a summary to report.md from source.md",
            True,
            (("write", "report.md"),),
            (),
        ),
        ("remember this", True, (("memory_write", "memory"),), ()),
    ),
)
def test_c1_positive_authority_preserves_existing_proof_boundary(
    objective: str,
    complete: bool,
    proofs: tuple[tuple[str, str], ...],
    constraints: tuple[tuple[str, str], ...],
) -> None:
    result = parse_objective_authority(objective)

    assert result.complete is complete
    assert tuple((item.effect, item.target) for item in result.positive_proofs) == proofs
    assert tuple((item.effect, item.target) for item in result.constraints) == constraints


def test_c1_unknown_and_non_command_forms_remain_fail_closed() -> None:
    for objective in (
        "alter rho.py when convenient",
        "the release captain asked me to update gamma.py",
        "edit foo.py after a future approval",
        "maybe update eta.py after review",
    ):
        result = parse_objective_authority(objective)
        assert result.complete is False
        assert result.positive_proofs == ()


def test_canonical_lexicon_is_typed_route_specific_and_immutable() -> None:
    entry = CANONICAL_SEMANTIC_LEXICON.classify("edit")
    assert entry is not None
    assert entry.value == "edit"
    assert entry.classes(LexiconRoute.CANDIDATE_INFERENCE) == frozenset(
        {LexemeClass.MUTATION}
    )
    assert entry.classes(LexiconRoute.POSITIVE_AUTHORITY) == frozenset(
        {LexemeClass.MUTATION}
    )
    assert isinstance(CANONICAL_SEMANTIC_LEXICON.tokens(
        LexiconRoute.CANDIDATE_INFERENCE, LexemeClass.MUTATION
    ), frozenset)

    with pytest.raises(TypeError):
        CANONICAL_SEMANTIC_LEXICON.entries["edit"] = entry  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        entry.value = "write"  # type: ignore[misc]


@pytest.mark.parametrize(
    "scenario_id",
    ("PV1-01", "PV1-02", "PV1-03", "PV1-04", "PV1-06", "PV1-07", "PV1-08"),
)
def test_c2_exact_practical_mutation_forms_admit_bounded_targets(
    scenario_id: str,
) -> None:
    scenario = next(item for item in PRACTICAL_V1 if item.scenario_id == scenario_id)
    result = parse_objective_authority(scenario.objective)

    assert result.complete is True
    assert len(result.positive_proofs) == 1
    assert result.positive_proofs[0].effect == "write"
    assert result.positive_proofs[0].target in {
        "calculator.py",
        "parser.py",
        "config.py",
        "default_timeout",
        "src/module.py",
        "app.py",
        "src/math_ops.py",
    }


def test_c2_new_lexemes_are_candidate_signals_not_authority_by_themselves() -> None:
    for token in ("atualizar", "atualize", "configurar", "configure", "mudar", "mude"):
        entry = CANONICAL_SEMANTIC_LEXICON.classify(token)
        assert entry is not None
        assert entry.classes(LexiconRoute.CANDIDATE_INFERENCE) == frozenset(
            {LexemeClass.MUTATION}
        )
        assert entry.classes(LexiconRoute.POSITIVE_AUTHORITY) == frozenset(
            {LexemeClass.MUTATION}
        )

    assert infer_effect_semantics("atualize DEFAULT_TIMEOUT de 10 para 15").requested == (
        "write",
    )
    assert parse_objective_authority("atualize DEFAULT_TIMEOUT de 10 para 15").positive_proofs == ()


@pytest.mark.parametrize(
    "objective",
    (
        "n\u00e3o atualize `config.py` para memory",
        "a documenta\u00e7\u00e3o menciona atualizar `config.py`",
        "atualize DEFAULT_TIMEOUT",
        "atualize DEFAULT_TIMEOUT quando conveniente",
        "talvez mude VALUE de 1 para 2",
        "mude VALUE de 1 para 2 depois de uma aprova\u00e7\u00e3o futura",
        "configure CACHE_BACKEND sem uma prefer\u00eancia definida",
    ),
)
def test_c2_known_mutation_lexeme_does_not_bypass_structural_authority(
    objective: str,
) -> None:
    result = parse_objective_authority(objective)

    assert result.complete is False
    assert result.positive_proofs == ()
