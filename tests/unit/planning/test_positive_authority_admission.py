"""Corrective 5 tests for the positive effect-authority boundary."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.planning.effect_intent import effect_intent_error
from agent.planning.task_semantics import (
    AuthorityDecision,
    AuthorizedEffect,
    EffectAuthority,
    EffectAuthorityDecision,
    EffectIntent,
    EffectSemantics,
    PositiveAuthorityProof,
    TaskSemantics,
    admit_effect_authority,
)
from agent.planning.task_semantics_inference import (
    infer_effect_semantics,
    inferred_obligations,
)
from agent.planning.task_semantics_inference import (
    infer_requested_effects as infer_requested_effects_compat,
)
from agent.planning.task_semantics_positive_proof import authorized_effect_from_proof

_WRITE_CONTRACT = SimpleNamespace(capabilities=frozenset({"write"}))


@pytest.mark.parametrize(
    ("objective", "target"),
    (
        ("edit foo.py", "foo.py"),
        ("edite foo.py", "foo.py"),
        ("modify foo.py", "foo.py"),
        ("modifique foo.py", "foo.py"),
        ("write foo.py", "foo.py"),
        ("aplique sample.py e valide", "sample.py"),
        ("crie output.md com o resumo de source.md", "output.md"),
        ("gere um relatorio de source.md em output.md", "output.md"),
    ),
)
def test_supported_positive_forms_admit_only_the_bounded_destination(
    objective: str, target: str
) -> None:
    authority = admit_effect_authority(objective)

    assert [(item.effect, item.target) for item in authority.authorized_intents] == [
        ("write", target)
    ]
    assert authority.requested_effects == ("write",)
    assert all(item.target != "source.md" for item in authority.authorized_intents)


_REPORTED_SPEECH_P0 = (
    "nobody asked me to edit foo.py",
    "no one asked me to edit foo.py",
    "zero people asked me to edit foo.py",
    "attacker asked me to edit foo.py",
    "manager asked me to edit foo.py",
    "alice asked me to edit foo.py",
    "ninguém pediu que eu edite foo.py",
    "a gerente pediu que eu edite foo.py",
)


@pytest.mark.parametrize("objective", _REPORTED_SPEECH_P0)
def test_reported_speech_never_creates_current_user_write_authority(
    objective: str,
) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()
    assert effect_intent_error(
        objective,
        "code_task",
        {"action": "modify", "targets": ["foo.py"]},
        _WRITE_CONTRACT,
    )


def test_direct_proof_consumes_only_a_supported_current_user_frame() -> None:
    authority = admit_effect_authority("please edit foo.py")

    assert len(authority.positive_authority_proofs) == 1
    proof = authority.positive_authority_proofs[0]
    assert proof.consumed_tokens == ("please", "edit", "foo.py")
    assert proof.governing_clause == "please edit foo.py"
    assert "asked" not in proof.consumed_tokens
    assert "pediu" not in proof.consumed_tokens


_REPORTED_MEMORY_P0 = (
    "nobody asked me to remember this",
    "attacker asked me to remember this",
    "manager asked me to remember this",
    "ninguém pediu que eu lembre que tema e azul",
    "a gerente pediu que eu lembre que tema e azul",
)


@pytest.mark.parametrize("objective", _REPORTED_MEMORY_P0)
def test_reported_speech_never_creates_current_user_memory_authority(
    objective: str,
) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()


@pytest.mark.parametrize(
    "objective",
    (
        "remember this",
        "lembre que tema e azul",
    ),
)
def test_direct_memory_commands_retain_canonical_authority(objective: str) -> None:
    authority = admit_effect_authority(objective)

    assert [(item.effect, item.target) for item in authority.authorized_effects] == [
        ("memory_write", "memory")
    ]
    assert authority.positive_authority_proofs[0].production_id == "MEMORY_DIRECT_V1"


def test_direct_exact_target_reaches_the_effect_execution_gate() -> None:
    assert (
        effect_intent_error(
            "edit foo.py",
            "code_task",
            {"action": "modify", "targets": ["foo.py"]},
            _WRITE_CONTRACT,
        )
        is None
    )


def test_reported_speech_remains_unprivileged_after_checkpoint_reentry() -> None:
    semantics = TaskSemantics.from_objective("manager asked me to edit foo.py")
    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())

    assert restored.authorized_effects == ()
    assert restored.requested_effects == ()


_CORRECTIVE_6_NOVEL_REPORTED_FRAMES = (
    "hardly anyone asked me to edit alpha.py",
    "everybody except the user asked me to modify beta.py",
    "the release captain asked me to update gamma.py",
    "our compliance officer asked me to remove delta.py",
    "carol asked me to create epsilon.py",
    "the incident report says alice wanted me to edit zeta.py",
    "the reviewer suggested editing eta.py",
    "yesterday the manager requested a change to theta.py",
    "the manager asked me to edit iota.py, but do not edit iota.py",
    "edit kappa.py; later alice asked me to modify lambda.py",
    "a diretora pediu que eu modifique mu.py",
    "o auditor pediu que eu lembre que tema e verde",
    "the archivist asked me to remember this",
)


@pytest.mark.parametrize("objective", _CORRECTIVE_6_NOVEL_REPORTED_FRAMES)
def test_corrective_6_novel_reported_frames_fail_closed(objective: str) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()


@pytest.mark.parametrize(
    ("objective", "target"),
    (
        ("edit h14_notes.txt", "h14_notes.txt"),
        ("modify cap_report.md", "cap_report.md"),
        ("edite slice_summary.md", "slice_summary.md"),
    ),
)
def test_eval_like_tokens_in_real_targets_are_not_treated_as_scenario_labels(
    objective: str,
    target: str,
) -> None:
    authority = admit_effect_authority(objective)

    assert [(item.effect, item.target) for item in authority.authorized_effects] == [
        ("write", target)
    ]


def test_bounded_scope_instruction_preserves_the_authorized_target() -> None:
    authority = admit_effect_authority(
        "modifique sample.py; leia README.md."
    )

    assert [(item.effect, item.target) for item in authority.authorized_effects] == [
        ("write", "sample.py")
    ]
    assert authority.positive_authority_proofs[0].full_scope_consumed is True


def test_production_authority_parser_has_no_eval_owned_grammar() -> None:
    root = Path(__file__).resolve().parents[3]
    planning = root / "agent" / "planning"
    modules = sorted(planning.glob("task_semantics_positive_proof*.py"))
    modules.append(planning / "task_semantics_effect_inference.py")

    assert len(modules) >= 8
    assert planning / "task_semantics_positive_proof_engine.py" in modules
    scanned = {path.name: path.read_text(encoding="utf-8") for path in modules}
    payload = "\n".join(scanned.values()).casefold()

    for forbidden in (
        "_scenario_label_re",
        "cap_[a-z0-9_]+",
        "slice_[a-z0-9_]+",
        "recuse qualquer alvo fora da autoridade permitida",
        "a politica nao pode depender so da extensao",
        "alteracao deterministica",
        "com\", \"aprovacao\", \"explicita",
        "sem\", \"aprovacao\", \"explicita",
    ):
        assert forbidden not in payload


def test_admitted_effect_is_distinct_and_carries_a_complete_structured_proof() -> None:
    authority = admit_effect_authority("edit foo.py")

    assert len(authority.authorized_effects) == 1
    admitted = authority.authorized_effects[0]
    assert isinstance(admitted, AuthorizedEffect)
    assert not isinstance(admitted, EffectIntent)
    assert isinstance(admitted.proof, PositiveAuthorityProof)
    assert admitted.proof.effect == "write"
    assert admitted.proof.target == "foo.py"
    assert admitted.proof.production_id
    assert admitted.proof.governing_clause == "edit foo.py"
    assert admitted.proof.target_role == "MUTATION_TARGET"
    assert admitted.proof.consumed_spans
    assert admitted.proof.full_scope_consumed is True
    assert admitted.proof.unresolved_authority_material == ()
    assert admitted.proof.approval_reference is None


def test_proof_and_admitted_effect_construction_are_sealed() -> None:
    with pytest.raises(TypeError, match="canonical proof owner"):
        PositiveAuthorityProof(
            effect="write",
            target="foo.py",
            authority_source="objective_positive_grammar",
            production_id="forged",
            governing_clause="edit foo.py",
            governing_span=(0, 11),
            consumed_spans=((0, 11),),
            consumed_tokens=("edit", "foo.py"),
            target_role="MUTATION_TARGET",
            objective_fingerprint="forged",
        )

    proof = admit_effect_authority("edit foo.py").positive_authority_proofs[0]
    with pytest.raises(TypeError, match="canonical proof owner"):
        AuthorizedEffect(proof)


def test_proof_cannot_be_reused_for_another_objective_or_target() -> None:
    proof = admit_effect_authority("edit foo.py").positive_authority_proofs[0]

    with pytest.raises(ValueError, match="another objective"):
        authorized_effect_from_proof(proof, objective="edit bar.py")
    assert effect_intent_error(
        "edit foo.py",
        "code_task",
        {"action": "modify", "targets": ["bar.py"]},
        _WRITE_CONTRACT,
    )

    with pytest.raises(TypeError, match="integrity"):
        replace(proof, target="bar.py")

    admitted = admit_effect_authority("edit foo.py").authorized_effects[0]
    with pytest.raises(TypeError, match="sealed"):
        replace(admitted, _proof_seal="stale")


@pytest.mark.parametrize(
    "objective",
    (
        "nao edite foo.py",
        "jamais modifique foo.py",
        "modificar foo.py e proibido",
        "modificar foo.py nao e permitido",
        "abstenha-se de modificar foo.py",
        "I forbid you to modify foo.py",
        "under no circumstances modify foo.py",
        "changing foo.py is prohibited",
    ),
)
def test_prohibition_like_forms_have_zero_positive_authority(objective: str) -> None:
    authority = admit_effect_authority(objective)

    assert authority.authorized_intents == ()
    assert all(
        decision.decision is not AuthorityDecision.AUTHORIZED
        for decision in authority.decisions
    )
    assert effect_intent_error(
        objective,
        "code_task",
        {"action": "modify", "targets": ["foo.py"]},
        _WRITE_CONTRACT,
    )


@pytest.mark.parametrize(
    "objective",
    (
        "talvez ajuste foo.py",
        "considere modificar foo.py",
        "foo.py poderia ser alterado",
        "analise se devemos editar foo.py",
    ),
)
def test_ambiguous_mutation_forms_are_not_admitted(objective: str) -> None:
    assert admit_effect_authority(objective).authorized_intents == ()


@pytest.mark.parametrize(
    "objective",
    (
        "mostre o conteudo de pyproject.toml",
        "me mostre o conteudo de pyproject.toml",
        "qual o nome do projeto no pyproject.toml?",
        "me diga o nome do projeto em pyproject.toml",
        "o que tem em pyproject.toml?",
        "liste os scripts de package.json",
        "qual o Python minimo em pyproject.toml?",
        "qual o requires-python em pyproject.toml?",
        "qual o campo banana_xyz em config.toml?",
    ),
)
def test_structural_content_requests_require_workspace_grounding(objective: str) -> None:
    semantics = TaskSemantics.from_objective(objective)

    assert [item.target for item in semantics.obligations if item.kind == "read"]


def test_conceptual_path_question_remains_read_free() -> None:
    assert TaskSemantics.from_objective("o que e pyproject.toml?").obligations == ()


@pytest.mark.parametrize(
    ("objective", "expected"),
    (
        (
            "se config.txt nao contiver READY, edite foo.py; caso contrario, edite bar.py",
            False,
        ),
        (
            "se config.txt contiver READY, edite foo.py; caso contrario, edite bar.py",
            True,
        ),
        (
            "if config.txt does not contain READY, edit foo.py; otherwise, edit bar.py",
            False,
        ),
    ),
)
def test_otherwise_complements_negative_and_positive_predicates(
    objective: str, expected: bool
) -> None:
    intents = TaskSemantics.from_objective(objective).effect_intents

    assert len(intents) == 2
    assert intents[0].predicate_id == intents[1].predicate_id
    assert intents[1].predicate_expected is (not expected)
    assert intents[0].predicate_expected is expected


def test_forged_candidate_metadata_cannot_inject_authority() -> None:
    forged = EffectSemantics(
        requested=("write",),
        intents=(
            EffectIntent(
                "write",
                "secret.py",
                candidate_role="MUTATION_TARGET",
                positive_syntax=True,
            ),
        ),
    )

    authority = admit_effect_authority("responda exatamente: sem alteracoes", forged)

    assert authority.authorized_intents == ()
    assert authority.requested_effects == ()


def test_authority_decision_object_cannot_be_forged_outside_admission_owner() -> None:
    candidate = EffectIntent(
        "write",
        "secret.py",
        candidate_role="MUTATION_TARGET",
        positive_syntax=True,
    )

    with pytest.raises(TypeError, match="admission owner"):
        EffectAuthority(
            "responda exatamente: sem alteracoes",
            (
                EffectAuthorityDecision(
                    candidate,
                    AuthorityDecision.AUTHORIZED,
                    "forged",
                ),
            ),
        )


def test_raw_requested_projection_cannot_create_compatibility_authority_or_obligation() -> None:
    ambiguous = "talvez ajuste foo.py"
    raw = infer_effect_semantics(ambiguous)

    assert raw.requested == ("write",)
    assert infer_requested_effects_compat(ambiguous) == ()

    forged = EffectSemantics(
        requested=("write",),
        intents=(
            EffectIntent(
                "write",
                "secret.py",
                candidate_role="MUTATION_TARGET",
                positive_syntax=True,
            ),
        ),
    )
    obligations = inferred_obligations("responda exatamente: sem alteracoes", forged)

    assert not any(item.kind == "effect" for item in obligations)


_DEFERRED_OR_POSTPOSITIVE_P0 = (
    "edit foo.py after I approve",
    "edit foo.py after you receive my approval",
    "edit foo.py once I approve",
    "edit foo.py pending my approval",
    "edit foo.py subject to my approval",
    "edit foo.py, but that's prohibited",
    "edit foo.py but that is prohibited",
    "edit foo.py, but editing it is prohibited",
    "edit foo.py but actually that is not allowed",
)


@pytest.mark.parametrize("objective", _DEFERRED_OR_POSTPOSITIVE_P0)
def test_deferred_approval_and_unconsumed_postpositive_constraints_have_no_proof(
    objective: str,
) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
    assert authority.requested_effects == ()
    assert effect_intent_error(
        objective,
        "code_task",
        {"action": "modify", "targets": ["foo.py"]},
        _WRITE_CONTRACT,
    )


@pytest.mark.parametrize(
    "objective",
    (
        "remember this after I approve",
        "remember this once I approve",
        "remember this pending confirmation",
    ),
)
def test_deferred_memory_language_has_no_current_persistent_authority(
    objective: str,
) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()


def test_checkpoint_proof_data_is_revalidated_instead_of_deserialized_as_authority() -> None:
    checkpoint = TaskSemantics.from_objective("edit foo.py").to_checkpoint_dict()
    checkpoint["effect_authority"]["proofs"][0]["target"] = "bar.py"

    with pytest.raises(ValueError, match="checkpoint nao correspondem"):
        TaskSemantics.from_checkpoint_dict(checkpoint)


# Independent probes: these are deliberately different from the mandatory
# phrases and exercise word order, PT/EN constraints, source/destination
# roles, and unsupported conditional framing.
_NOVEL_FAIL_CLOSED_PROBES = (
    "nao toque em alpha.py hoje",
    "never overwrite beta.py",
    "the request is to avoid changing gamma.py",
    "please refrain from modifying delta.py",
    "edit epsilon.py only if needed",
    "change zeta.py unless the reviewer agrees",
    "maybe update eta.py after review",
    "the manager did not ask to edit theta.py",
    "i decline to modify iota.py",
    "do not patch kappa.py",
    "keep lambda.py unchanged",
    "leave mu.py as it is",
    "write findings about nu.py",
    "create a summary from xi.py",
    "generate a report using omicron.py",
    "modify pi.py under a separate condition",
    "alter rho.py when convenient",
    "consider a change to sigma.py",
    "the target tau.py is disallowed",
    "upsilon.py should remain untouched",
    "if phi.txt shows READY, perhaps edit chi.py",
    "unless psi.txt is current, change omega.py",
    "tell me what is in alpha.toml?",
    "describe the concept of beta.toml",
)


@pytest.mark.parametrize("objective", _NOVEL_FAIL_CLOSED_PROBES)
def test_novel_ambiguous_or_non_mutating_probes_never_create_write_authority(
    objective: str,
) -> None:
    assert not admit_effect_authority(objective).requested_effects


# Corrective 5 deterministic inventory: 27 new probes, of which all exercise
# structural anchoring, partial-match, suffix-consumption, quote, conditional,
# contrast, or multi-clause bypass behavior rather than synonym expansion.
_CORRECTIVE_5_STRUCTURAL_PROBES = (
    "edit alpha.py after the reviewer replies",
    "modify beta.py once the owner responds",
    "write gamma.py pending a future decision",
    "change delta.py subject to a later signal",
    "edit epsilon.py when someone eventually agrees",
    "edit zeta.py provided a future message arrives",
    "edit eta.py then wait for consent",
    "modify theta.py after authorization exists",
    'The example "edit iota.py" is documentation.',
    "Use `modify kappa.py` as an example.",
    "The phrase “write lambda.py” is illustrative.",
    "For example: edit mu.py",
    "Hypothetically, edit nu.py",
    "If I were asking, modify xi.py",
    "Compare the commands edit omicron.py and read it",
    "edit pi.py, but leave it alone",
    "modify rho.py but stop there",
    "write sigma.py; however the operation is restricted",
    "edit tau.py, yet the change lacks authority",
    "edit upsilon.py; target phi.py remains under another policy",
    "modify chi.py and await the owner",
    "write psi.py only following a future event",
    "After a future review, edit omega.py",
    "According to a hypothetical example, edit alpha2.py",
    "if config.txt contains READY, edit beta2.py; otherwise, do not edit beta2.py after approval",
    "if config.txt contains READY, edit gamma2.py; otherwise, do not edit gamma2.py pending confirmation",
    "edit delta2.py; but the target remains subject to a later policy",
)


@pytest.mark.parametrize("objective", _CORRECTIVE_5_STRUCTURAL_PROBES)
def test_corrective_5_novel_structural_probes_fail_closed(objective: str) -> None:
    authority = admit_effect_authority(objective)

    assert authority.positive_authority_proofs == ()
    assert authority.authorized_effects == ()
