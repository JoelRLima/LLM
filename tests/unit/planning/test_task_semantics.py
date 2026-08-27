from types import SimpleNamespace

import pytest

from agent.planning.effect_intent import effect_intent_error, effect_intent_matches
from agent.planning.task_completion import initialize_task_progression
from agent.planning.task_semantics import (
    ObligationStatus,
    PredicateResolutionState,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
    TaskSemanticsError,
    infer_effect_semantics,
)
from agent.resources.contracts import ResourceAccess, ResourceMode
from agent.state import AgentState


def test_effect_semantics_preserves_requested_and_prohibited_without_direct_text_effect() -> None:
    mixed = infer_effect_semantics(
        "Se X for verdadeiro, escreva Y; caso contrario, nao altere nada."
    )
    assert mixed.requested == ("write",)
    assert mixed.prohibited == ("write",)

    direct = infer_effect_semantics("Escreva exatamente o texto abaixo.")
    assert direct.requested == ()
    assert direct.prohibited == ()


@pytest.mark.parametrize(
    "objective",
    [
        "gere um resumo do foo.py",
        "crie uma explica\u00e7\u00e3o para foo.py",
        "produza uma an\u00e1lise sobre foo.py",
        "produce a summary of foo.py",
        "write an explanation about foo.py",
    ],
)
def test_output_generation_source_or_topic_never_grants_filesystem_write(
    objective: str,
) -> None:
    semantics = infer_effect_semantics(objective)

    assert semantics.requested_intents == ()
    assert semantics.requested == ()


@pytest.mark.parametrize(
    ("objective", "target"),
    [
        ("edite foo.py", "foo.py"),
        ("modifique foo.py", "foo.py"),
        ("salve o resumo em resumo.md", "resumo.md"),
        ("write the summary to summary.md", "summary.md"),
    ],
)
def test_explicit_mutation_or_durable_destination_has_one_concrete_target(
    objective: str,
    target: str,
) -> None:
    semantics = infer_effect_semantics(objective)

    assert [(item.effect, item.target) for item in semantics.requested_intents] == [
        ("write", target)
    ]
    assert semantics.prohibited_intents == ()


def test_mixed_source_and_durable_destination_only_authorizes_destination() -> None:
    semantics = infer_effect_semantics("resuma foo.py e salve em resumo.md")

    assert [(item.effect, item.target) for item in semantics.requested_intents] == [
        ("write", "resumo.md")
    ]
    assert all(item.target != "foo.py" for item in semantics.requested_intents)


@pytest.mark.parametrize(
    "objective",
    [
        "n\u00e3o quero que voc\u00ea modifique foo.py",
        "n\u00e3o quero que o agente modifique foo.py",
        "n\u00e3o deve editar foo.py",
        "voc\u00ea n\u00e3o deve editar foo.py",
        "n\u00e3o edite foo.py",
        "I don't want you to edit foo.py",
        "you must not edit foo.py",
        "the agent should not modify foo.py",
        "n\u00e3o \u00e9 para modificar foo.py",
        "n\u00e3o \u00e9 permitido modificar foo.py",
        "n\u00e3o \u00e9 para editar foo.py",
        "\u00e9 proibido modificar foo.py",
        "proibido modificar foo.py",
        "evite modificar foo.py",
        "it is forbidden to modify foo.py",
        "avoid changing foo.py",
    ],
)
def test_prohibition_scope_never_creates_requested_write_to_the_governed_target(
    objective: str,
) -> None:
    semantics = infer_effect_semantics(objective)

    assert [(item.effect, item.target) for item in semantics.prohibited_intents] == [
        ("write", "foo.py")
    ]
    assert all(item.target != "foo.py" for item in semantics.requested_intents)


def test_conjunction_boundaries_keep_requested_and_prohibited_targets_separate() -> None:
    first = infer_effect_semantics("n\u00e3o edite foo.py; edite bar.py")
    second = infer_effect_semantics("edite foo.py, mas n\u00e3o modifique bar.py")

    assert [(item.target, item.polarity) for item in first.intents] == [
        ("foo.py", "prohibited"),
        ("bar.py", "requested"),
    ]
    assert [(item.target, item.polarity) for item in second.intents] == [
        ("foo.py", "requested"),
        ("bar.py", "prohibited"),
    ]


@pytest.mark.parametrize(
    ("objective", "expected"),
    [
        (
            "n\u00e3o modifique foo.py, mas edite bar.py",
            (("foo.py", "prohibited"), ("bar.py", "requested")),
        ),
        (
            "edite bar.py e n\u00e3o modifique foo.py",
            (("bar.py", "requested"), ("foo.py", "prohibited")),
        ),
        (
            "\u00e9 proibido modificar foo.py; gere um resumo de bar.py",
            (("foo.py", "prohibited"),),
        ),
        (
            "n\u00e3o \u00e9 para editar foo.py, mas crie out.md com um resumo",
            (("foo.py", "prohibited"), ("out.md", "requested")),
        ),
    ],
)
def test_governed_prohibitions_preserve_unrelated_clause_authority(
    objective: str,
    expected: tuple[tuple[str, str], ...],
) -> None:
    semantics = infer_effect_semantics(objective)

    assert tuple((item.target, item.polarity) for item in semantics.intents) == expected


@pytest.mark.parametrize(
    ("objective", "expected"),
    [
        ("fa\u00e7a um resumo de src/main.py", ()),
        ("explique o conte\u00fado de settings.yaml", ()),
        ("save these findings into notes.md", (("notes.md", "requested"),)),
        (
            "write the report in report.md",
            (("report.md", "requested"),),
        ),
        (
            "save a copy of source.py to backup.md",
            (("backup.md", "requested"),),
        ),
        (
            "n\u00e3o permita que o rob\u00f4 altere config.yaml",
            (("config.yaml", "prohibited"),),
        ),
        (
            "I would prefer that you do not change config.yaml",
            (("config.yaml", "prohibited"),),
        ),
        ("please don't touch secrets.env", (("secrets.env", "prohibited"),)),
        (
            "edite src/a.py e n\u00e3o edite src/b.py",
            (("src/a.py", "requested"), ("src/b.py", "prohibited")),
        ),
        (
            "n\u00e3o modifique a.py e modifique b.py",
            (("a.py", "prohibited"), ("b.py", "requested")),
        ),
        ("generate output using config.yml", ()),
    ],
)
def test_novel_bounded_output_and_negation_paraphrases(
    objective: str,
    expected: tuple[tuple[str, str], ...],
) -> None:
    semantics = infer_effect_semantics(objective)

    assert [(item.target, item.polarity) for item in semantics.intents] == list(expected)


def test_proposal_only_effect_semantics_preserves_preview_boundary() -> None:
    proposal = infer_effect_semantics("Proponha uma modificacao sem aplicar.")

    assert proposal.proposal_only is True
    assert proposal.requested == ("write",)
    assert proposal.prohibited == ("write",)


def test_scenario_label_does_not_hide_conditional_effect_intent() -> None:
    mixed = infer_effect_semantics(
        "H10: se h10_condition.txt contiver H10_TRUE, crie h10_effect.txt; "
        "caso contrário, não altere nada."
    )

    assert mixed.requested == ("write",)
    assert mixed.prohibited == ("write",)
    assert all(item.condition for item in mixed.requested_intents)
    assert all(item.condition for item in mixed.prohibited_intents)


def test_conditional_effect_authority_requires_and_reuses_trusted_predicate() -> None:
    objective = (
        "se config.txt contiver ENABLE, edite foo.py; "
        "caso contrario, nao altere foo.py"
    )
    semantics = TaskSemantics.from_objective(objective)
    requested, prohibited = semantics.effect_intents
    assert requested.predicate_id == "config.txt|contains|enable"
    assert requested.predicate_expected is True
    assert prohibited.predicate_id == requested.predicate_id
    assert prohibited.predicate_expected is False
    assert requested.predicate_state is PredicateResolutionState.UNRESOLVED
    assert "UNRESOLVED_CONDITIONAL_EFFECT" in (
        effect_intent_error(
            objective,
            "code_task",
            {"action": "modify", "targets": ["foo.py"]},
        )
        or ""
    )
    semantics.observe_tool(
        "file_reader",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": "ENABLE = true",
        },
        evidence_ref=1,
        args={"file_path": "config.txt"},
    )
    assert semantics.predicate_resolution(requested.predicate_id).value is True  # type: ignore[union-attr]
    access = ResourceAccess("foo.py", ResourceMode.WRITE)
    assert effect_intent_matches(
        semantics.effect_intents[0], "write", access,
        predicate_resolutions=semantics.predicate_resolutions,
    )
    assert not effect_intent_matches(
        semantics.effect_intents[1], "write", access,
        predicate_resolutions=semantics.predicate_resolutions,
    )
    assert effect_intent_error(
        objective,
        "code_task",
        {"action": "modify", "targets": ["foo.py"]},
        predicate_resolutions=semantics.predicate_resolutions,
    ) is None


def test_negative_predicate_scope_does_not_prohibit_requested_effect() -> None:
    semantics = TaskSemantics.from_objective(
        "se controle.txt n\u00e3o contiver READY, edite foo.py"
    )

    assert [(item.target, item.polarity) for item in semantics.effect_intents] == [
        ("foo.py", "requested")
    ]
    intent = semantics.effect_intents[0]
    assert intent.predicate_id == "controle.txt|contains|ready"
    assert intent.predicate_expected is False


@pytest.mark.parametrize(
    ("result", "args"),
    [
        ({"executed": False, "status": "succeeded", "data": "READY"}, {"file_path": "controle.txt"}),
        ({"executed": True, "status": "failed", "data": "READY"}, {"file_path": "controle.txt"}),
        ({"executed": True, "status": "succeeded", "data": "READY"}, {"file_path": "other.txt"}),
        (
            {"executed": True, "status": "succeeded", "data": "READY", "artifacts": [{"metadata": {"complete": False}}]},
            {"file_path": "controle.txt"},
        ),
    ],
)
def test_ineligible_predicate_observations_remain_unresolved(
    result: dict[str, object], args: dict[str, object]
) -> None:
    semantics = TaskSemantics.from_objective(
        "se controle.txt contiver READY, edite foo.py"
    )

    semantics.observe_tool("file_reader", result, evidence_ref=1, args=args)

    assert semantics.predicate_resolutions == {}


@pytest.mark.parametrize(("data", "expected"), [("READY", True), ("WAIT", False)])
def test_eligible_predicate_observation_resolves_deterministically(
    data: str, expected: bool
) -> None:
    semantics = TaskSemantics.from_objective(
        "se controle.txt contiver READY, edite foo.py"
    )

    semantics.observe_tool(
        "file_reader",
        {"executed": True, "status": "succeeded", "data": data},
        evidence_ref=1,
        args={"file_path": "controle.txt"},
    )

    evidence = semantics.predicate_resolution("controle.txt|contains|ready")
    assert evidence is not None
    assert evidence.value is expected


def test_false_conditional_branch_has_no_write_authority_and_no_pending_effect() -> None:
    objective = (
        "If config.txt contains ENABLE, edit foo.py; "
        "otherwise, do not alter foo.py"
    )
    semantics = TaskSemantics.from_objective(objective)
    semantics.observe_tool(
        "file_reader",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": "DISABLED = true",
        },
        evidence_ref=1,
        args={"file_path": "config.txt"},
    )
    assert semantics.predicate_resolution("config.txt|contains|enable").value is False  # type: ignore[union-attr]
    assert semantics.pending_effects() == ()
    assert all(item.kind != "effect" for item in semantics.pending_obligations())
    error = effect_intent_error(
        objective,
        "code_task",
        {"action": "modify", "targets": ["foo.py"]},
        predicate_resolutions=semantics.predicate_resolutions,
    )
    assert error is not None and error.startswith("PROHIBITED_EFFECT")


def test_conditional_predicate_checkpoint_reentry_fails_closed_until_observation_returns() -> None:
    objective = "se config.txt contiver ENABLE, edite foo.py; caso contrario, nao altere foo.py"
    semantics = TaskSemantics.from_objective(objective)
    semantics.observe_tool(
        "file_reader",
        {"ok": True, "status": "succeeded", "executed": True, "data": "ENABLE"},
        evidence_ref=1,
        args={"file_path": "config.txt"},
    )
    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    assert restored.predicate_resolutions == {}
    assert all(
        item.predicate_state is PredicateResolutionState.UNRESOLVED
        for item in restored.effect_intents
    )
    restored.register_observation(
        "file_reader",
        {"ok": True, "status": "succeeded", "executed": True, "data": "ENABLE"},
        evidence_ref=1,
        args={"file_path": "config.txt"},
    )
    restored.revalidate_predicate_resolutions()
    assert restored.predicate_resolution("config.txt|contains|enable").value is True  # type: ignore[union-attr]


def test_incomplete_h2_plan_keeps_search_obligation_pending() -> None:
    state = AgentState()
    initialize_task_progression(
        SimpleNamespace(agent_state=state),
        "Leia fonte_h2.txt e depois procure nos outros arquivos pela palavra que ele contem.",
    )

    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte_h2.txt"},
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": "orion",
        },
    )
    assert [item.kind for item in state.pending_obligations()] == ["search"]

    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": [],
        },
    )
    assert state.pending_obligations() == ()


def test_obligation_review_is_bounded_unique_and_not_model_terminal_authority() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "explique o resultado")
    before = state.task_obligations

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "new", "kind": "custom", "description": "x", "status": "satisfied"}],
            source="initial_plan",
        )
    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "new", "kind": "custom", "description": "x"}],
            source="tool_output",
        )
    assert state.task_obligations == before


def test_equivalent_canonical_review_amendment_is_rejected_as_no_progress() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "explique o resultado")
    state.review_task_obligations(
        [
            {
                "id": "review:first",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt.",
            }
        ],
        source="canonical_review",
    )
    before = state.task_obligations

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [
                {
                    "id": "review:equivalent",
                    "kind": "read",
                    "target": "a.txt",
                    "description": "A mesma leitura com outro id.",
                }
            ],
            source="canonical_review",
        )

    assert state.task_obligations == before

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [
                {"id": "same", "kind": "custom", "description": "a"},
                {"id": "same", "kind": "custom", "description": "b"},
            ],
            source="canonical_review",
        )
    assert state.task_obligations == before


def test_obligation_transitions_require_evidence_and_checkpoint_round_trip() -> None:
    semantics = TaskSemantics(
        TaskIntent("objetivo"),
        [TaskObligation("read", "read", "ler a fonte", target="fonte.txt")],
    )
    with pytest.raises(TaskSemanticsError):
        semantics.satisfy("read", evidence_ref=None)  # type: ignore[arg-type]
    semantics.observe_tool(
        "file_reader",
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": "conteudo",
        },
        evidence_ref=1,
        args={"file_path": "fonte.txt"},
    )
    assert semantics.obligation_status("read") is ObligationStatus.SATISFIED
    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    assert restored.obligation_status("read") is ObligationStatus.PENDING
    assert restored.obligation_evidence("read") == ()
    assert restored.terminal_evidence_complete() is False
    assert restored.to_checkpoint_dict()["statuses"]["read"] == "satisfied"


def test_unrequested_effect_requires_canonical_authority() -> None:
    state = AgentState()
    with pytest.raises(TaskSemanticsError):
        state.record_executed_effect("write", evidence_ref=1)
    assert state.requested_effects == []
    assert state.executed_effects == []
    assert state.pending_effects() == ()


def _complete(data):
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "complete": True,
        "data": data,
    }


def test_read_evidence_is_bound_to_the_requested_target() -> None:
    semantics = TaskSemantics.from_objective("Leia a.txt e b.txt.")

    semantics.observe_tool(
        "file_reader", _complete("A"), evidence_ref=1, args={"file_path": "a.txt"}
    )

    assert semantics.obligation_status("read:1") is ObligationStatus.SATISFIED
    assert semantics.obligation_status("read:2") is ObligationStatus.PENDING

    semantics.observe_tool(
        "file_reader", _complete("B"), evidence_ref=2, args={"file_path": "b.txt"}
    )
    assert semantics.obligation_status("read:2") is ObligationStatus.SATISFIED


def test_search_evidence_requires_the_exact_query() -> None:
    semantics = TaskSemantics(
        TaskIntent("procure X"),
        [TaskObligation("search-x", "search", "buscar X", query="X")],
        _strict_evidence=True,
    )

    assert semantics.observe_tool(
        "grep", _complete([]), evidence_ref=1, args={"path": ".", "pattern": "Y"}
    ) == ()
    assert semantics.obligation_status("search-x") is ObligationStatus.PENDING

    semantics.observe_tool(
        "grep", _complete([]), evidence_ref=2, args={"path": ".", "pattern": "X"}
    )
    assert semantics.obligation_status("search-x") is ObligationStatus.SATISFIED


def test_d4_zero_match_search_is_successful_negative_evidence() -> None:
    semantics = TaskSemantics(
        TaskIntent("procure X"),
        [TaskObligation("search-x", "search", "buscar X", query="X")],
        _strict_evidence=True,
    )

    semantics.observe_tool(
        "grep",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "complete": True,
            "data": [],
            "total_matches": 0,
        },
        evidence_ref=1,
        args={"path": ".", "pattern": "X"},
    )

    assert semantics.obligation_status("search-x") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("search-x") == (1,)


def test_explicit_search_literal_precedes_later_observed_value_language() -> None:
    semantics = TaskSemantics.from_objective(
        "H3: encontre H3_SOURCE_MARKER e use o texto observado para buscar a ocorrencia correspondente."
    )

    obligation = semantics.obligations[0]
    assert obligation.kind == "search"
    assert obligation.query == "h3_source_marker"
    assert obligation.query_source is None


def test_generic_search_language_does_not_create_unprovable_obligation() -> None:
    semantics = TaskSemantics.from_objective(
        "Busque a evidência no workspace e informe o resultado."
    )

    assert all(item.kind != "search" for item in semantics.obligations)


@pytest.mark.parametrize(
    ("objective", "kind", "identity"),
    [
        ("qual licenca esta no pyproject.toml?", "read", "pyproject.toml"),
        ("o que diz pyproject.toml sobre a licenca?", "read", "pyproject.toml"),
        ("qual versao esta em pyproject.toml?", "read", "pyproject.toml"),
        ("qual versao esta em package.json?", "read", "package.json"),
        ("a opcao X esta habilitada em config.yaml?", "read", "config.yaml"),
        ("onde FooBar e definido?", "search", "foobar"),
        ("quantos testes existem para X?", "search", "x"),
        ("o arquivo config.json habilita GBNF?", "read", "config.json"),
    ],
)
def test_implicit_workspace_questions_create_objective_bound_evidence(
    objective: str, kind: str, identity: str
) -> None:
    semantics = TaskSemantics.from_objective(objective)

    matches = [
        item
        for item in semantics.obligations
        if item.kind == kind and (item.target == identity or item.query == identity)
    ]
    assert matches
    assert matches[0].admission_source.value == "OBJECTIVE_DERIVED"


def test_conceptual_question_does_not_create_workspace_evidence_obligation() -> None:
    semantics = TaskSemantics.from_objective("o que e pyproject.toml?")

    assert semantics.obligations == ()

    conceptual = TaskSemantics.from_objective("explique o conceito de pyproject.toml")
    assert conceptual.obligations == ()


@pytest.mark.parametrize(
    "objective",
    [
        "quais depend\u00eancias est\u00e3o no pyproject.toml?",
        "resuma pyproject.toml",
        "me diga as depend\u00eancias de pyproject.toml",
        "liste as depend\u00eancias de pyproject.toml",
        "what dependencies are in pyproject.toml?",
        "summarize pyproject.toml",
        "tell me the dependencies in pyproject.toml",
        "relate a vers\u00e3o de package.json",
    ],
)
def test_concrete_file_property_and_transformation_requests_require_read_evidence(
    objective: str,
) -> None:
    semantics = TaskSemantics.from_objective(objective)

    reads = [item.target for item in semantics.obligations if item.kind == "read"]
    expected = "package.json" if "package.json" in objective else "pyproject.toml"
    assert reads == [expected]


@pytest.mark.parametrize(
    "objective",
    [
        "o que \u00e9 pyproject.toml?",
        "para que serve um pyproject.toml?",
        "what is pyproject.toml?",
        "explique o conceito de TOML usando pyproject.toml como exemplo",
    ],
)
def test_conceptual_concrete_filename_mentions_do_not_force_reads(objective: str) -> None:
    semantics = TaskSemantics.from_objective(objective)

    assert [item for item in semantics.obligations if item.kind == "read"] == []


def test_explicit_truncated_search_request_accepts_truncation_evidence() -> None:
    semantics = TaskSemantics.from_objective(
        "Busque H9_TRUNCATED_SENTINEL, limite a observacao e informe se ela foi truncada."
    )

    semantics.observe_tool(
        "grep",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": [{"file": "one.txt"}],
            "artifacts": [{"metadata": {"complete": False, "truncated": True}}],
        },
        evidence_ref=1,
        args={"path": ".", "pattern": "H9_TRUNCATED_SENTINEL"},
    )

    assert semantics.obligation_status("requirement:search") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("requirement:search") == (1,)


def test_compare_requires_both_complete_reads_and_accepts_empty_values() -> None:
    semantics = TaskSemantics.from_objective(
        "Compare a.txt e b.txt e diga se o conteudo e igual."
    )

    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=1, args={"file_path": "a.txt"}
    )
    assert semantics.obligation_status("requirement:compare") is ObligationStatus.PENDING

    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=2, args={"file_path": "b.txt"}
    )
    assert semantics.obligation_status("requirement:compare") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("requirement:compare") == (1, 2)


def test_model_obligation_forms_and_evidence_provenance_fail_closed() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "Leia a.txt e b.txt.")

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "unsupported", "kind": "report", "description": "gerar relatorio"}],
            source="initial_plan",
        )

    state.record_tool_result("file_reader", {"file_path": "a.txt"}, _complete("A"))
    with pytest.raises(TaskSemanticsError):
        state.satisfy_obligation("read:2", evidence_ref=1)


def test_structured_obligation_checkpoint_round_trip_is_exact() -> None:
    semantics = TaskSemantics.from_objective("Compare a.txt e b.txt.")
    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=1, args={"file_path": "a.txt"}
    )
    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=2, args={"file_path": "b.txt"}
    )

    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    comparison = next(item for item in restored.obligations if item.kind == "compare")
    assert comparison.operands == ("a.txt", "b.txt")
    assert restored.obligation_status(comparison.id) is ObligationStatus.PENDING
    assert restored.obligation_evidence(comparison.id) == ()
    assert restored.terminal_evidence_complete() is False


def test_checkpoint_without_closed_semantics_version_fails_closed() -> None:
    semantics = TaskSemantics.from_objective("Leia a.txt.")
    checkpoint = semantics.to_checkpoint_dict()
    checkpoint.pop("schema_version")

    with pytest.raises(TaskSemanticsError):
        TaskSemantics.from_checkpoint_dict(checkpoint)
