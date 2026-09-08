"""Observation dispatch policy mixed into the canonical step policies."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.contracts import ToolArgs
from agent.planning.observation_receipts import (
    ObservationClassification,
    ObservationDispatchDecision,
    build_observation_receipt,
    canonical_source_identity,
    classify_observation,
)
from agent.tools.contracts import ToolResult


class ObservationPolicyMixin:
    """Pre-dispatch facts and post-dispatch classification for file reads."""

    context: Any

    def _file_hash(self, file_path: str) -> str | None:
        """Provided by the canonical StepPolicies owner."""
        raise NotImplementedError

    def _source_identity(self, file_path: str | Path) -> str:
        return canonical_source_identity(
            file_path,
            workspace_root=getattr(self.context, "workspace_root", None),
        )

    def _record_metric(
        self,
        metric_type: str,
        data: Mapping[str, object] | None = None,
    ) -> None:
        payload = dict(data or {})
        recorder = getattr(self.context, "record_metric", None)
        if callable(recorder):
            try:
                recorder(metric_type, payload)
                return
            except Exception:
                pass
        logger = getattr(self.context, "_log_metric", None)
        if callable(logger):
            try:
                logger({"metric_type": metric_type, **payload})
            except Exception:
                pass

    def prepare_observation_dispatch(
        self,
        tool: str,
        args: ToolArgs,
        file_path: str,
    ) -> ObservationDispatchDecision | None:
        """Capture source/reuse facts before the gateway can execute a read."""

        if tool not in {"file_reader", "code_analyzer"} or not file_path:
            return None
        source_identity = self._source_identity(file_path)
        source_hash = self._file_hash(file_path) or ""
        extent: Mapping[str, object] = (
            {
                "kind": "lines",
                "start": args.get("start_line", 1),
                "end": args.get("end_line"),
            }
            if "start_line" in args or "end_line" in args
            else {"kind": "whole"}
        )
        exact_available = self._exact_cache_available(
            tool,
            args,
            file_path,
            source_hash,
        )
        state = self.context.agent_state
        semantics = getattr(state, "task_semantics", None)
        requirements = tuple(
            item.id for item in semantics.pending_obligations()
            if item.kind == "read" and self._source_identity(item.target) == source_identity
        ) if semantics is not None else ()
        # The selected executable request owns its minimal extent. Semantic
        # whole-source requirements may additionally establish evidence credit.
        active_request = any(
            getattr(step, "tool", None) == tool
            and dict(getattr(step, "args", {})) == dict(args)
            and str(getattr(getattr(state.step_records.get(step.step_id), "status", None), "value", ""))
            in {"pending", "running"}
            for step in getattr(state, "plan", ())
        )
        required_extent = extent if active_request else {"kind": "whole"} if requirements else {}
        pending_need = bool(required_extent) and dict(extent) == dict(required_extent)
        prior = self._latest_observation(tool, file_path, source_identity)
        current = build_observation_receipt(
            {
                "source_identity": source_identity,
                "source_hash": source_hash,
                "source_extent": extent,
                "evidence_provenance": "EXACT_SOURCE" if exact_available else "UNKNOWN",
                "complete": exact_available,
                "truncated": False,
            },
            reusable_exact_bytes=exact_available,
            physical_execution=not exact_available,
        )
        physical_execution = not exact_available
        classification = classify_observation(
            prior,
            current,
            pending_need=pending_need,
            exact_bytes_available=exact_available,
            physical_execution=physical_execution,
            retention_gap=(prior is not None and not exact_available),
        )
        return ObservationDispatchDecision(
            source_identity=source_identity,
            source_hash=source_hash,
            source_extent=extent,
            pending_need=pending_need,
            exact_bytes_available=exact_available,
            physical_execution=physical_execution,
            source_current=bool(source_hash),
            prior=prior,
            classification=classification,
            retention_gap=(prior is not None and not exact_available),
            required_extent=required_extent,
            pending_requirement_ids=requirements,
            exactness_required=pending_need,
        )

    def newly_satisfies_observation_need(
        self, dispatch: ObservationDispatchDecision, result: ToolResult,
    ) -> bool:
        """Prove relevance using the semantic owner's accepted evidence ref."""
        receipt = build_observation_receipt(result)
        state = self.context.agent_state
        semantics = getattr(state, "task_semantics", None)
        if not (
            semantics is not None and dispatch.pending_need and result.ok
            and result.executed is True and receipt.complete and not receipt.truncated
            and receipt.reusable_exact_bytes and dispatch.source_current
            and receipt.source_hash and receipt.source_hash == dispatch.source_hash
            and self._source_identity(receipt.source_identity) == dispatch.source_identity
            and dict(receipt.source_extent) == dict(dispatch.required_extent)
        ):
            return False
        refs = tuple(
            index + 1 for index, entry in enumerate(state.tool_history)
            if getattr(entry.get("result"), "invocation_id", None) == result.invocation_id
        )
        return any(
            semantics.obligation_status(identifier).value == "satisfied"
            and any(ref in semantics.obligation_evidence(identifier) for ref in refs)
            for identifier in dispatch.pending_requirement_ids
        )

    def _exact_cache_available(
        self,
        tool: str,
        args: ToolArgs,
        file_path: str,
        current_hash: str,
    ) -> bool:
        if (
            tool not in {"file_reader", "code_analyzer"}
            or not file_path
            or "start_line" in args
            or "end_line" in args
            or not current_hash
        ):
            return False
        memory = self.context.agent_state.memory.state
        hashes = memory.get("file_hashes", {})
        if not isinstance(hashes, Mapping) or hashes.get(file_path) != current_hash:
            return False
        entries = memory.get("file_cache_entries", {})
        entry = entries.get(file_path, {}) if isinstance(entries, Mapping) else {}
        if not isinstance(entry, Mapping):
            return False
        provenance = str(entry.get("evidence_provenance", "")).casefold()
        extent = entry.get("source_extent")
        return bool(
            entry.get("data")
            and provenance in {"exact_source", "bounded_source"}
            and isinstance(extent, Mapping)
            and extent.get("kind") == "whole"
        )

    def _latest_observation(
        self,
        tool: str,
        file_path: str,
        source_identity: str,
    ) -> Any:
        for entry in reversed(getattr(self.context.agent_state, "tool_history", ())):
            if not isinstance(entry, Mapping) or entry.get("tool") != tool:
                continue
            prior_args = entry.get("args", {})
            if not isinstance(prior_args, Mapping):
                continue
            prior_path = prior_args.get("target") or prior_args.get("file_path")
            if canonical_source_identity(
                prior_path,
                workspace_root=getattr(self.context, "workspace_root", None),
            ) != source_identity:
                continue
            raw_result = entry.get("result")
            if raw_result is None:
                continue
            prior = build_observation_receipt(raw_result)
            projection = prior.to_dict()
            projection["source_identity"] = source_identity
            return build_observation_receipt(projection)
        return None

    def classify_observation_result(
        self,
        tool: str,
        args: ToolArgs,
        result: ToolResult,
        *,
        pending_need: bool = False,
        exact_bytes_available: bool | None = None,
        dispatch: ObservationDispatchDecision | None = None,
    ) -> ObservationClassification:
        """Classify a result against the latest same-source canonical record."""

        current = build_observation_receipt(result)
        prior = dispatch.prior if dispatch is not None else None
        current_invocation = getattr(result, "invocation_id", None)
        file_path = str(args.get("target") or args.get("file_path") or "")
        for entry in reversed(getattr(self.context.agent_state, "tool_history", ())):
            if dispatch is not None:
                break
            if entry.get("tool") != tool:
                continue
            prior_args = entry.get("args", {})
            prior_path = prior_args.get("target") or prior_args.get("file_path")
            if prior_path != file_path:
                continue
            prior_result = entry.get("result")
            if prior_result is not None:
                prior_invocation = getattr(prior_result, "invocation_id", None)
                if current_invocation and prior_invocation and current_invocation == prior_invocation:
                    continue
                prior = build_observation_receipt(prior_result)
                break
        if dispatch is not None:
            current_projection = current.to_dict()
            current_projection["source_identity"] = dispatch.source_identity
            current_projection.setdefault("source_hash", dispatch.source_hash)
            current_projection.setdefault("source_extent", dict(dispatch.source_extent))
            current = build_observation_receipt(
                current_projection,
                reusable_exact_bytes=dispatch.exact_bytes_available,
                physical_execution=dispatch.physical_execution,
            )
            pending_need = dispatch.pending_need
            exact_bytes_available = dispatch.exact_bytes_available
            physical_execution = dispatch.physical_execution
        else:
            physical_execution = result.executed is True
        return classify_observation(
            prior,
            current,
            source_current=dispatch.source_current if dispatch is not None else True,
            pending_need=pending_need,
            exact_bytes_available=exact_bytes_available,
            physical_execution=physical_execution,
            retention_gap=dispatch.retention_gap if dispatch is not None else False,
        )


__all__ = ["ObservationPolicyMixin"]
