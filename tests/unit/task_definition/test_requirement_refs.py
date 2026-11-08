from __future__ import annotations

from dataclasses import replace

from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from tests.support.task_definition import make_contract, make_phase, make_spec


def _refs(materialization):
    return {
        (item["source"], item["phase_id"], item["index"]): item["requirement_id"]
        for item in materialization.to_dict()["requirement_refs"]
    }


def test_requirement_ids_survive_repository_reload_and_phase_selection(repository) -> None:
    contract = make_contract("stable-requirements", "Fix and validate the defect")
    spec = make_spec(contract, phases=(make_phase("inspect"), make_phase("repair", depends_on=("inspect",))))
    repository.save_contract(contract)
    reference = repository.save_spec(spec)
    resolver = TaskContextResolver(repository)

    whole = resolver.resolve(reference)
    restored = TaskDefinitionRef.from_dict(reference.to_dict())
    selected = resolver.resolve(restored, phase_id="repair")

    assert _refs(whole) == _refs(resolver.resolve(restored))
    assert _refs(selected)
    assert all(_refs(whole)[address] == identity for address, identity in _refs(selected).items())
    assert contract.to_dict() == repository.resolve(restored).contract.to_dict()


def test_requirement_ids_do_not_reuse_evidence_addresses_after_spec_change() -> None:
    contract = make_contract("changed-definition", "Fix and validate the defect")
    original = make_spec(contract)
    revised = replace(original, version=2, global_acceptance=("Tests pass after the final modification",))
    resolver = TaskContextResolver()

    before = _refs(resolver.materialize(contract, original))
    after = _refs(resolver.materialize(contract, revised))

    for address, identity in before.items():
        if address[0].startswith("contract."):
            assert after[address] == identity
        else:
            assert after[address] != identity


def test_duplicate_requirements_have_distinct_addresses_and_no_completion_claims() -> None:
    contract = make_contract("duplicate-requirements", "Repair", requirements=("validate", "validate"))
    materialization = TaskContextResolver().materialize(contract, make_spec(contract))
    references = materialization.to_dict()["requirement_refs"]
    identities = [item["requirement_id"] for item in references]

    assert len(set(identities)) == len(identities)
    assert all("status" not in item and "evidence" not in item for item in references)
    assert _refs(materialization)[("contract.requirements", None, 0)] != _refs(materialization)[
        ("contract.requirements", None, 1)
    ]


def test_model_context_preserves_assumptions_and_unresolved_questions(repository) -> None:
    contract = make_contract(
        "preserved-context", "Repair the bug", assumptions=("Keep the public API",),
        open_questions=("Which runtime should receive the fix?",),
    )
    repository.save_contract(contract)
    reference = repository.save_spec(make_spec(contract))

    materialization = TaskContextResolver(repository).resolve(reference)

    assert materialization.structured["contract"]["assumptions"] == contract.assumptions
    assert materialization.structured["contract"]["open_questions"] == contract.open_questions
    assert "Which runtime should receive the fix?" in materialization.trusted_text
    assert "Keep the public API" in materialization.trusted_text


def test_requirement_catalogue_does_not_duplicate_text_in_model_context() -> None:
    contract = make_contract("bounded-catalogue", "Repair")
    materialization = TaskContextResolver().materialize(contract, make_spec(contract))

    assert materialization.to_dict()["requirement_refs"]
    assert "requirement_refs" not in materialization.trusted_text
    assert all(item.requirement_id not in materialization.trusted_text for item in materialization.requirement_references)


def test_selected_phase_preserves_dependency_invariants_and_evidence(repository) -> None:
    contract = make_contract("complete-dependency", "Repair without changing the public interface")
    inspect = replace(
        make_phase("inspect"),
        invariants=("Preserve the public interface",),
        evidence_requirements=("Capture the failing test before repair",),
    )
    repair = make_phase("repair", depends_on=("inspect",))
    spec = make_spec(contract, phases=(inspect, repair))
    repository.save_contract(contract)
    reference = repository.save_spec(spec)
    resolver = TaskContextResolver(repository)

    whole = resolver.resolve(reference)
    selected = resolver.resolve(reference, phase_id="repair")
    dependency = selected.structured["dependency_facts"][0]

    assert dependency["invariants"] == inspect.invariants
    assert dependency["evidence_requirements"] == inspect.evidence_requirements
    assert "Capture the failing test before repair" in selected.trusted_text
    assert "Preserve the public interface" in selected.trusted_text
    for field in ("phase.invariants", "phase.evidence_requirements"):
        address = (field, "inspect", 0)
        assert _refs(selected)[address] == _refs(whole)[address]
    assert repository.resolve(reference).spec == spec
