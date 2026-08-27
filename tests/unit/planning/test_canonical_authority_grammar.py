"""Focused Corrective-7 coverage for the unified authority grammar ledger."""

import ast
from pathlib import Path

import pytest

import agent.planning.task_semantics_inference as inference_module
from agent.planning.task_semantics import (
    EffectIntent,
    EffectSemantics,
    TaskSemantics,
    TaskSemanticsError,
    admit_effect_authority,
)
from agent.planning.task_semantics_positive_proof import (
    AuthorityConstraint,
    ObjectiveAuthorityGrammarResult,
    PositiveAuthorityProof,
    parse_objective_authority,
)
from agent.planning.task_semantics_positive_proof_controls import (
    _parse_neutral_fragment,
)
from agent.planning.task_semantics_positive_proof_lexing import _lexemes


@pytest.mark.parametrize(
    "objective",
    (
        "edit foo.py, but do not write",
        "edit foo.py; do not write",
        "edit foo.py, but never write",
        "edit foo.py; do not save",
        "edite foo.py, mas nao escreva",
    ),
)
def test_one_grammar_pass_materializes_global_write_constraint(objective: str) -> None:
    result = parse_objective_authority(objective)

    assert result.complete is True
    assert [(item.effect, item.target) for item in result.positive_proofs] == [
        ("write", "foo.py")
    ]
    assert [(item.effect, item.target) for item in result.constraints] == [
        ("write", "*")
    ]
    constraint = result.constraints[0]
    assert isinstance(constraint, AuthorityConstraint)
    assert constraint.production_id == "WRITE_CONSTRAINT_GLOBAL_V1"
    assert constraint.target_role == "WORKSPACE"
    assert constraint.objective_fingerprint == result.objective_fingerprint


@pytest.mark.parametrize(
    ("objective", "target"),
    (
        ("edit foo.py; nao e para edit foo.py", "foo.py"),
        ("edite foo.py; nao e para editar foo.py", "foo.py"),
        ("edit foo.py; do not edit foo.py", "foo.py"),
        ("edit allowed.txt; do not edit forbidden.txt", "forbidden.txt"),
    ),
)
def test_exact_negative_fragment_is_a_canonical_constraint(
    objective: str, target: str
) -> None:
    result = parse_objective_authority(objective)

    assert result.complete is True
    assert [(item.effect, item.target) for item in result.constraints] == [
        ("write", target)
    ]
    assert result.constraints[0].production_id == "WRITE_CONSTRAINT_EXACT_V1"


def test_memory_negative_fragment_uses_the_same_canonical_ledger() -> None:
    result = parse_objective_authority("remember this; do not remember this")

    assert result.complete is True
    assert [(item.effect, item.target) for item in result.positive_proofs] == [
        ("memory_write", "memory")
    ]
    assert [(item.effect, item.target) for item in result.constraints] == [
        ("memory_write", "memory")
    ]
    assert result.constraints[0].target_role == "MEMORY"


@pytest.mark.parametrize(
    "objective",
    (
        "remember this; do not write to memory",
        "lembre que tema e azul; nao salve na memoria",
    ),
)
def test_global_memory_constraint_dominates_memory_proof(objective: str) -> None:
    from agent.planning.task_semantics import admit_effect_authority

    authority = admit_effect_authority(objective)

    assert [(item.effect, item.target) for item in authority.constraints] == [
        ("memory_write", "memory")
    ]
    assert authority.authorized_effects == ()


def test_memory_constraint_survives_seeded_advisory_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisory = EffectSemantics(
        requested=("memory_write",),
        intents=(EffectIntent("memory_write", "memory"),),
    )
    monkeypatch.setattr(inference_module, "infer_effect_semantics", lambda _objective: advisory)

    authority = admit_effect_authority("remember this; do not write to memory")

    assert [(item.effect, item.target) for item in authority.constraints] == [
        ("memory_write", "memory")
    ]
    assert authority.authorized_effects == ()


def test_conditional_constraint_keeps_predicate_identity_and_complement() -> None:
    result = parse_objective_authority(
        "se config.txt contiver ENABLE, edite foo.py; "
        "caso contrario, nao altere foo.py"
    )

    assert result.complete is True
    assert len(result.positive_proofs) == 1
    assert len(result.constraints) == 1
    assert result.positive_proofs[0].predicate_id == result.constraints[0].predicate_id
    assert result.positive_proofs[0].predicate_expected is True
    assert result.constraints[0].predicate_expected is False


def test_unrecognized_authority_material_is_fail_closed() -> None:
    result = parse_objective_authority("edit foo.py after a future approval")

    assert result.complete is False
    assert result.positive_proofs == ()
    assert result.constraints == ()
    assert result.fail_closed is True


def test_canonical_grammar_types_are_not_caller_constructible() -> None:
    with pytest.raises(TypeError, match="canonical grammar owner"):
        AuthorityConstraint(
            effect="write",
            target="foo.py",
            authority_source="objective_authority_grammar",
            production_id="forged",
            governing_clause="do not edit foo.py",
            governing_span=(0, 17),
            consumed_spans=((0, 17),),
            consumed_tokens=("do", "not", "edit", "foo.py"),
            target_role="MUTATION_TARGET",
            objective_fingerprint="forged",
        )

    with pytest.raises(TypeError, match="canonical grammar owner"):
        ObjectiveAuthorityGrammarResult("forged")

    assert isinstance(parse_objective_authority("edit foo.py").positive_proofs[0], PositiveAuthorityProof)


@pytest.mark.parametrize("constructor_name", ("AuthorityConstraint", "ObjectiveAuthorityGrammarResult"))
def test_canonical_grammar_constructors_have_one_production_owner(
    constructor_name: str,
) -> None:
    root = Path(__file__).resolve().parents[3]
    planning = root / "agent" / "planning"
    assert planning.is_dir()
    production_modules = sorted(planning.glob("*.py"))
    assert production_modules
    expected_sentinels = {
        "task_semantics_positive_proof_engine.py",
        "task_semantics_authority_model.py",
    }
    assert expected_sentinels.issubset({path.name for path in production_modules})

    constructor_calls: list[str] = []
    for path in production_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == constructor_name:
                constructor_calls.append(path.name)

    assert constructor_calls
    assert set(constructor_calls) == {"task_semantics_positive_proof_engine.py"}


def test_constraint_dominance_does_not_use_advisory_prohibited_polarity() -> None:
    root = Path(__file__).resolve().parents[3]
    helper = root / "agent" / "planning" / "task_semantics_authority_helpers.py"
    assert helper.is_file()
    source = helper.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(helper))
    dominance = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "apply_constraint_dominance"
    )
    function_source = ast.get_source_segment(source, dominance) or ""

    assert "AuthorityConstraint" in function_source
    assert "canonical constraint" in function_source
    assert "candidate.polarity" not in function_source


@pytest.mark.parametrize(
    "advisory",
    (
        EffectSemantics(
            requested=("write",),
            intents=(EffectIntent("write", "foo.py"),),
        ),
        EffectSemantics(),
        EffectSemantics(
            prohibited=("write",),
            intents=(EffectIntent("write", "foo.py", polarity="prohibited"),),
        ),
        EffectSemantics(
            requested=("write",),
            intents=(EffectIntent("write", "unrelated.py"),),
        ),
    ),
)
def test_advisory_disagreement_cannot_restore_a_canonical_denied_write(
    monkeypatch: pytest.MonkeyPatch, advisory: EffectSemantics
) -> None:
    monkeypatch.setattr(inference_module, "infer_effect_semantics", lambda _objective: advisory)

    authority = admit_effect_authority("edit foo.py; do not write")

    assert authority.constraints
    assert authority.authorized_effects == ()
    assert authority.authorized_intents == ()


def test_advisory_omission_preserves_unrelated_exact_target_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inference_module, "infer_effect_semantics", lambda _objective: EffectSemantics())

    authority = admit_effect_authority("edit allowed.txt; do not edit forbidden.txt")

    assert [(item.effect, item.target) for item in authority.authorized_effects] == [
        ("write", "allowed.txt")
    ]
    assert [(item.effect, item.target) for item in authority.constraints] == [
        ("write", "forbidden.txt")
    ]


def test_checkpoint_rederives_and_validates_canonical_constraints() -> None:
    from agent.planning.task_semantics import TaskSemantics

    semantics = TaskSemantics.from_objective("edit foo.py; do not write")
    checkpoint = semantics.to_checkpoint_dict()
    manifest = checkpoint["effect_authority"]

    assert manifest["constraints"][0]["target"] == "*"
    restored = TaskSemantics.from_checkpoint_dict(checkpoint)
    assert restored.effect_authority.constraints == semantics.effect_authority.constraints

    checkpoint["effect_authority"]["constraints"] = []
    with pytest.raises(ValueError, match="restricoes de autoridade"):
        TaskSemantics.from_checkpoint_dict(checkpoint)


def test_checkpoint_advisory_intent_cannot_remove_canonical_constraint() -> None:
    from agent.planning.task_semantics import TaskSemantics

    checkpoint = TaskSemantics.from_objective("edit foo.py; do not write").to_checkpoint_dict()
    checkpoint["effect_intents"] = []

    restored = TaskSemantics.from_checkpoint_dict(checkpoint)
    assert restored.effect_authority.constraints
    assert restored.effect_intents[0].polarity == "prohibited"


def test_denied_durable_effect_does_not_become_a_requested_obligation() -> None:
    from agent.planning.task_semantics import TaskSemantics

    semantics = TaskSemantics.from_objective("edit foo.py; do not write")

    assert semantics.requested_effects == ()
    assert not any(item.kind == "effect" for item in semantics.obligations)
    assert semantics.prohibited_effects == ("write",)


@pytest.mark.parametrize(
    ("objective", "production_id"),
    (
        ("read source.md", "NEUTRAL_READ_EXACT_PATH_V1"),
        ("inspect source.md", "NEUTRAL_READ_EXACT_PATH_V1"),
        ("examine source.md", "NEUTRAL_READ_EXACT_PATH_V1"),
        ("analyze source.md", "NEUTRAL_RESPONSE_EXACT_PATH_V1"),
        ("summarize source.md", "NEUTRAL_RESPONSE_EXACT_PATH_V1"),
        ("describe source.md", "NEUTRAL_RESPONSE_EXACT_PATH_V1"),
        ("first read source.md", "NEUTRAL_FIRST_READ_EXACT_PATH_V1"),
        ("primeiro leia source.md", "NEUTRAL_FIRST_READ_EXACT_PATH_V1"),
        ("use source.md as source", "NEUTRAL_SOURCE_EXACT_PATH_V1"),
        ("use source.md como fonte", "NEUTRAL_SOURCE_EXACT_PATH_V1"),
        (
            "generate a summary from source.md",
            "NEUTRAL_SOURCE_ONLY_OUTPUT_V1",
        ),
    ),
)
def test_neutral_context_is_a_named_bounded_production(
    objective: str, production_id: str
) -> None:
    spec = _parse_neutral_fragment(_lexemes(objective))

    assert spec is not None
    assert spec.production_id == production_id
    assert spec.span == (0, len(objective))
    assert spec.arguments == (("source", "source.md"),)
    assert spec.consumed_tokens == tuple(objective.split())


@pytest.mark.parametrize(
    "objective",
    (
        "read source.md, note; edit target.py",
        "analyze source.md with note; edit target.py",
        "first read source.md before continuing; edit target.py",
        "primeiro leia source.md evitando mudancas; edite target.py",
        "use source.md as source for review; edit target.py",
        "use source.md como fonte e responda; edite target.py",
    ),
)
def test_unknown_neutral_tail_invalidates_the_complete_authority_parse(
    objective: str,
) -> None:
    result = parse_objective_authority(objective)
    authority = admit_effect_authority(objective)

    assert result.complete is False
    assert result.positive_proofs == ()
    assert result.constraints == ()
    assert authority.authorized_effects == ()


def test_advisory_write_cannot_rescue_unknown_neutral_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisory = EffectSemantics(
        requested=("write",),
        intents=(EffectIntent("write", "target.py"),),
    )
    monkeypatch.setattr(inference_module, "infer_effect_semantics", lambda _objective: advisory)

    objective = "analyze source.md, an unmodeled review note; edit target.py"
    authority = admit_effect_authority(objective)

    assert parse_objective_authority(objective).complete is False
    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()


@pytest.mark.parametrize(
    "objective",
    (
        "analyze foo.py, no changes; edit foo.py",
        "read foo.py, no edits; edit foo.py",
        "analyze foo.py while avoiding changes; edit foo.py",
        "analise foo.py evitando mudancas; edite foo.py",
        "summarize foo.py, readonly; edit foo.py",
        "describe foo.py with zero modifications; edit foo.py",
        "inspect foo.py under a no-change policy; edit foo.py",
    ),
)
def test_required_unknown_neutral_probes_have_zero_authorized_write(
    objective: str,
) -> None:
    result = parse_objective_authority(objective)
    authority = admit_effect_authority(objective)

    assert result.complete is False
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()


@pytest.mark.parametrize(
    "objective",
    (
        "analyzing source.md, leaving files untouched; edit target.py",
        "analyses source.md with a reviewer note; edite target.py",
        "read source.md for context; edit target.py",
        "resuma source.md com observacao extra; edite target.py",
        "use source.md as an unbounded source; edit target.py",
        "edit target.py; inspect source.md for context",
        "edit target.py; leia source.md e confirme o contexto",
        "after reading source.md, edit target.py",
    ),
)
def test_novel_unknown_neutral_material_never_expands_durable_scope(
    objective: str,
) -> None:
    result = parse_objective_authority(objective)
    authority = admit_effect_authority(objective)

    assert result.complete is False
    assert result.positive_proofs == ()
    assert authority.authorized_effects == ()


@pytest.mark.parametrize(
    ("objective", "expected"),
    (
        ("read source.md; edit target.py", [("write", "target.py")]),
        ("first read source.md; edit target.py", [("write", "target.py")]),
        (
            "analyze source.md; write summary to report.md",
            [("write", "report.md")],
        ),
        ("edit target.py; inspect source.md", [("write", "target.py")]),
    ),
)
def test_bounded_neutral_controls_preserve_exact_write_scope(
    objective: str, expected: list[tuple[str, str]]
) -> None:
    result = parse_objective_authority(objective)
    authority = admit_effect_authority(objective)

    assert result.complete is True
    assert [(item.effect, item.target) for item in authority.authorized_effects] == expected


def test_source_only_output_neutrality_is_bounded_and_non_durable() -> None:
    objective = "generate a summary from source.md"
    result = parse_objective_authority(objective)
    authority = admit_effect_authority(objective)

    assert result.complete is True
    assert result.positive_proofs == ()
    assert authority.authorized_effects == ()


def test_neutral_owner_is_not_a_negative_space_fallback() -> None:
    root = Path(__file__).resolve().parents[3]
    controls = (root / "agent" / "planning" / "task_semantics_positive_proof_controls.py").read_text(
        encoding="utf-8"
    )
    commands = (root / "agent" / "planning" / "task_semantics_positive_proof_commands.py").read_text(
        encoding="utf-8"
    )

    assert "_safe_inert_fragment" not in controls
    assert "_safe_inert_fragment" not in commands
    assert "_parse_neutral_fragment" in controls
    assert "_parse_neutral_fragment" in commands


def test_neutral_owner_is_single_and_eval_independent() -> None:
    root = Path(__file__).resolve().parents[3]
    planning = root / "agent" / "planning"
    controls = planning / "task_semantics_positive_proof_controls.py"
    commands = planning / "task_semantics_positive_proof_commands.py"
    condition = planning / "task_semantics_positive_proof_condition.py"

    control_source = controls.read_text(encoding="utf-8")
    command_source = commands.read_text(encoding="utf-8")
    condition_source = condition.read_text(encoding="utf-8")
    assert control_source.count("def _parse_neutral_fragment") == 1
    assert command_source.count("_parse_neutral_fragment") == 3
    assert condition_source.count("_parse_neutral_fragment") == 2
    for source in (control_source, command_source, condition_source):
        lowered = source.casefold()
        assert "h13_" not in lowered
        assert "h16_" not in lowered
        assert "cap_" not in lowered
        assert "slice_" not in lowered


@pytest.mark.parametrize(
    "objective",
    (
        "edit foo.py",
        "do not write",
        "edit foo.py; do not write",
    ),
)
def test_checkpoint_mode_downgrade_cannot_erase_canonical_authority(
    objective: str,
) -> None:
    checkpoint = TaskSemantics.from_objective(objective).to_checkpoint_dict()
    checkpoint["effect_authority"] = {"mode": "structured"}

    with pytest.raises(
        TaskSemanticsError,
        match="apagaria fatos canonicos",
    ):
        TaskSemantics.from_checkpoint_dict(checkpoint)


def test_legacy_structured_checkpoint_has_an_explicit_compatibility_boundary() -> None:
    semantics = TaskSemantics.from_legacy("legacy replay state", ["write"])
    checkpoint = semantics.to_checkpoint_dict()

    assert checkpoint["effect_authority"] == {"mode": "legacy_structured"}
    restored = TaskSemantics.from_checkpoint_dict(checkpoint)

    assert restored.effect_authority is None
    assert restored.requested_effects == ("write",)


def test_legacy_marker_cannot_bypass_canonical_objective_facts() -> None:
    checkpoint = TaskSemantics.from_legacy("edit foo.py", ["write"]).to_checkpoint_dict()
    assert checkpoint["effect_authority"] == {"mode": "legacy_structured"}

    with pytest.raises(TaskSemanticsError, match="apagaria fatos canonicos"):
        TaskSemantics.from_checkpoint_dict(checkpoint)
