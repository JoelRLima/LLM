from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from agent.planning.step_policies import StepPolicies
from agent.planning.task_semantics import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
)
from agent.tools.result_completeness import (
    EvidenceProvenance,
    canonical_completeness,
    exact_source_covers_whole_result,
)


def _source_result(
    data: str,
    *,
    provenance: EvidenceProvenance = EvidenceProvenance.EXACT_SOURCE,
    extent: dict[str, object] | None = None,
    source: str = "a.txt",
) -> dict[str, object]:
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "data": data,
        "complete": provenance is not EvidenceProvenance.DERIVED_LOSSY,
        "truncated": False,
        "evidence_provenance": provenance.value,
        "source_identity": source,
        "source_hash": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        "source_extent": extent or {"kind": "whole"},
    }


def test_exact_full_source_read_satisfies_read_obligation() -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt."),
        [TaskObligation("read:a", "read", "Ler a.txt.", target="a.txt")],
        _strict_evidence=True,
    )

    semantics.observe_tool(
        "file_reader",
        _source_result("conteudo"),
        evidence_ref=1,
        args={"file_path": "a.txt"},
    )

    assert semantics.obligation_status("read:a") is ObligationStatus.SATISFIED
    assert exact_source_covers_whole_result(_source_result("conteudo")) is True


def test_stale_source_invalidates_a_freshness_based_cache_hit(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("old", encoding="utf-8")
    old_hash = hashlib.sha256(b"old").hexdigest()
    state = SimpleNamespace(
        memory=SimpleNamespace(
            state={
                "file_hashes": {"a.txt": old_hash},
                "file_summaries": {"a.txt": "old summary"},
                "file_cache_entries": {},
            }
        ),
        tool_history=[],
    )
    context = SimpleNamespace(
        agent_state=state,
        resolve_user_path=lambda _path: source,
        _emit=lambda *_args: None,
    )
    source.write_text("new", encoding="utf-8")

    hit, result = StepPolicies(context).try_cache(
        "file_reader",
        {"file_path": "a.txt"},
        "a.txt",
        record_result=False,
    )

    assert hit is False
    assert result is None


def test_fresh_lossy_derived_content_cannot_satisfy_full_read() -> None:
    result = _source_result(
        "summary only",
        provenance=EvidenceProvenance.DERIVED_LOSSY,
        extent={"kind": "derived_summary"},
    )
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt."),
        [TaskObligation("read:a", "read", "Ler a.txt.", target="a.txt")],
        _strict_evidence=True,
    )

    semantics.observe_tool("file_reader", result, evidence_ref=1, args={"file_path": "a.txt"})

    assert canonical_completeness(result) == (False, False)
    assert semantics.obligation_status("read:a") is ObligationStatus.PENDING


def test_lossy_content_cannot_provide_previous_read_literal_provenance() -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt e procure nos outros arquivos pela palavra observada."),
        [
            TaskObligation(
                "search:previous",
                "search",
                "Procure o texto observado.",
                query_source="previous_read",
            )
        ],
        _strict_evidence=True,
    )
    lossy = _source_result(
        "summary only",
        provenance=EvidenceProvenance.DERIVED_LOSSY,
        extent={"kind": "derived_summary"},
    )

    semantics.observe_tool("file_reader", lossy, evidence_ref=1, args={"file_path": "a.txt"})
    semantics.observe_tool(
        "grep",
        _source_result("", source="."),
        evidence_ref=2,
        args={"path": ".", "pattern": "summary only"},
    )

    assert semantics.obligation_status("search:previous") is ObligationStatus.PENDING


def test_bounded_source_is_complete_only_for_its_declared_extent() -> None:
    bounded = _source_result(
        "line one\n",
        provenance=EvidenceProvenance.BOUNDED_SOURCE,
        extent={"kind": "lines", "start": 1, "end": 1},
    )

    assert canonical_completeness(bounded) == (True, False)
    assert exact_source_covers_whole_result(bounded) is False


def test_compare_requiring_complete_operands_rejects_lossy_operand() -> None:
    semantics = TaskSemantics(
        TaskIntent("Compare a.txt e b.txt."),
        [
            TaskObligation(
                "compare:files",
                "compare",
                "Compare os arquivos.",
                operands=("a.txt", "b.txt"),
            )
        ],
        _strict_evidence=True,
    )

    semantics.observe_tool(
        "file_reader",
        _source_result(
            "summary",
            provenance=EvidenceProvenance.DERIVED_LOSSY,
            extent={"kind": "derived_summary"},
        ),
        evidence_ref=1,
        args={"file_path": "a.txt"},
    )
    semantics.observe_tool(
        "file_reader",
        _source_result("exact"),
        evidence_ref=2,
        args={"file_path": "b.txt"},
    )

    assert semantics.obligation_status("compare:files") is ObligationStatus.PENDING


def test_exact_source_cache_entry_remains_usable(tmp_path) -> None:
    state = SimpleNamespace(
        memory=SimpleNamespace(
            state={
                "file_hashes": {},
                "file_summaries": {},
                "file_cache_entries": {},
            }
        ),
        tool_history=[],
    )
    path = Path(tmp_path) / "a.txt"
    content = "cached exact"
    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    state.memory.state["file_hashes"]["a.txt"] = source_hash
    state.memory.state["file_cache_entries"]["a.txt"] = {
        "data": content,
        "evidence_provenance": EvidenceProvenance.EXACT_SOURCE.value,
        "source_extent": {"kind": "whole"},
    }
    context = SimpleNamespace(
        agent_state=state,
        resolve_user_path=lambda _path: path,
        _emit=lambda *_args: None,
    )
    path.write_text(content, encoding="utf-8")

    hit, result = StepPolicies(context).try_cache(
        "file_reader",
        {"file_path": "a.txt"},
        "a.txt",
        record_result=False,
    )

    assert hit is True
    assert result is not None
    assert exact_source_covers_whole_result(result) is True


def test_checkpoint_restore_does_not_upgrade_lossy_evidence() -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt."),
        [TaskObligation("read:a", "read", "Ler a.txt.", target="a.txt")],
        _strict_evidence=True,
    )
    semantics.observe_tool(
        "file_reader",
        _source_result(
            "summary",
            provenance=EvidenceProvenance.DERIVED_LOSSY,
            extent={"kind": "derived_summary"},
        ),
        evidence_ref=1,
        args={"file_path": "a.txt"},
    )

    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())

    assert restored.obligation_status("read:a") is ObligationStatus.PENDING
    assert restored.obligation_evidence("read:a") == ()
