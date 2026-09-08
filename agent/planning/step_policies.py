from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Dict, Optional, cast
from uuid import uuid4

from agent.contracts import ToolArgs
from agent.parsers import validate_tool_args
from agent.planning.errors import ToolNotFoundError
from agent.planning.observation_invalidation import (
    clear_observation_state,
    mutation_footprint,
)
from agent.planning.observation_receipts import (
    ObservationClassification,
    ObservationDispatchDecision,
)
from agent.planning.step_contracts import ExecutionContext
from agent.planning.step_observation_policy import ObservationPolicyMixin
from agent.planning.tool_metadata import ToolMetadata, get_tool_metadata
from agent.runtime.failures import FailureFact
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.tools.contracts import ToolResult, ToolStatus
from agent.tools.result_adapter import ensure_canonical_result
from agent.tools.result_completeness import EvidenceProvenance


class StepPolicies(ObservationPolicyMixin):
    """Validation, deduplication, cache and post-processing policies for a step."""

    def __init__(
        self,
        context: ExecutionContext,
        *,
        path_resolver: Callable[[str | Path], Path] | None = None,
    ) -> None:
        self.context = context
        candidate = path_resolver or getattr(context, "resolve_user_path", None)
        self._path_resolver = candidate if callable(candidate) else None
    def _resolve_user_path(self, file_path: str | Path) -> Path:
        if self._path_resolver is None:
            return Path(file_path)
        return self._path_resolver(file_path)

    def validate(self, step_number: int, tool: str, args: ToolArgs) -> bool:
        valid, error = validate_tool_args(tool, args, self.context.skills)
        if not valid:
            return self._reject(
                step_number,
                f"Schema: {error}",
                tool,
                args,
                failure=FailureFact.from_code(
                    "INVALID_ARGUMENTS", message=str(error), tool_name=tool
                ),
            )
        if tool not in self.context.skills:
            raise ToolNotFoundError(f"Tool '{tool}' não foi registrada no Orchestrator.")
        if self.context.active_skills and tool not in self.context.active_skills:
            return self._reject(
                step_number,
                f"Tool '{tool}' não permitida",
                tool,
                args,
                failure=FailureFact.from_code(
                    "TOOL_BLOCKED", message=f"Tool '{tool}' não permitida", tool_name=tool
                ),
            )
        return True
    def _reject(
        self,
        step_number: int,
        reason: str,
        tool: str,
        args: ToolArgs,
        *,
        failure: FailureFact | None = None,
    ) -> bool:
        action = self.context._handle_step_failure(
            step_number, reason, tool, args, failure=failure
        )
        if action == "continue":
            self.context._purge_stale_context()
        else:
            self.context.fail_task()
        return False
    def is_hard_blocked(
        self, tool: str, args: ToolArgs, file_path: str, usage: Dict[str, int],
        *, observation_dispatch: ObservationDispatchDecision | None = None,
    ) -> bool:
        if observation_dispatch is not None and observation_dispatch.classification in {
            ObservationClassification.CACHE_REUSE,
            ObservationClassification.CONTEXT_REHYDRATION,
            ObservationClassification.STALE_REREAD,
        }:
            return False
        reason = self._analyzer_repetition(tool, file_path, usage)
        reason = reason or self._reader_repetition(tool, args, file_path, usage)
        if reason:
            self._record_metric(
                "redundant_read_blocks",
                {"tool": tool, "file_path": self._source_identity(file_path), "reason": reason},
            )
        if reason and self.context.verbose:
            print(f"[DEBUG] Hard block silencioso: {reason} em '{file_path}'")
        return bool(reason)

    @staticmethod
    def _analyzer_repetition(tool: str, file_path: str, usage: Dict[str, int]) -> str | None:
        if tool != "code_analyzer" or not file_path:
            return None
        key = f"code_analyzer_{file_path}"
        usage[key] = usage.get(key, 0) + 1
        if usage[key] <= 1:
            return None
        usage[f"fully_read_{file_path}"] = 1
        usage[f"fully_analyzed_{file_path}"] = 1
        return "code_analyzer repetido"
    @staticmethod
    def _reader_repetition(tool: str, args: ToolArgs, file_path: str, usage: Dict[str, int]) -> str | None:
        if tool != "file_reader" or not file_path:
            return None
        if "start_line" in args and "end_line" in args:
            key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
            usage[key] = usage.get(key, 0) + 1
            if usage[key] > 1:
                return "chunk repetido"
        return "arquivo já totalmente lido" if usage.get(f"fully_read_{file_path}", 0) else None

    def invalidate_observation_state(
        self,
        tool: str,
        usage: Dict[str, int],
        *,
        args: ToolArgs | None = None,
        result: ToolResult | None = None,
    ) -> bool:
        mutation, affected_files = mutation_footprint(
            tool,
            args or {},
            result,
            self._tool_metadata(tool),
            descriptor=self._tool_descriptor(tool),
        )
        evidence = project_mutation_evidence(result)
        if mutation:
            clear_observation_state(self.context, usage, affected_files)
            return True
        if not mutation:
            # An attempted/unverified mutation can invalidate a prior read
            # even when no surviving bytes changed.  Keep physical mutation
            # reporting false, but fail closed for predicate freshness.
            raw_status = getattr(result, "status", "")
            status = str(getattr(raw_status, "value", raw_status) or "").casefold()
            if (
                status != ToolStatus.UNVERIFIED.value
                or not evidence.attempted
                or evidence.occurred
                or evidence.rollback_occurred
                or not evidence.affected_files
            ):
                return False
            affected_files = evidence.affected_files
        if not affected_files:
            return False
        clear_observation_state(self.context, usage, affected_files)
        semantics = getattr(self.context.agent_state, "task_semantics", None)
        invalidate = getattr(semantics, "invalidate_predicates_for_targets", None)
        if callable(invalidate):
            invalidate(affected_files)
        return True
    def _tool_metadata(self, tool: str) -> ToolMetadata:
        registry = getattr(self.context, "tool_registry", None)
        metadata_dict = getattr(registry, "metadata_dict", None)
        if callable(metadata_dict):
            metadata = metadata_dict().get(tool)
            if isinstance(metadata, ToolMetadata):
                return metadata
        return get_tool_metadata(tool)

    def _tool_descriptor(self, tool: str) -> object | None:
        registry = getattr(self.context, "tool_registry", None)
        descriptor = getattr(registry, "descriptor", None)
        if not callable(descriptor):
            return None
        try:
            return cast(object | None, descriptor(tool))
        except (AttributeError, KeyError, LookupError):
            return None

    def is_impossible_chunk(self, tool: str, args: ToolArgs, file_path: str) -> bool:
        if tool != "file_reader" or "start_line" not in args or "end_line" not in args or not file_path:
            return False
        known_total = self._known_total_lines(file_path)
        return bool(known_total and args["start_line"] > known_total)

    def _known_total_lines(self, file_path: str) -> int | None:
        for history in self.context.agent_state.tool_history:
            raw_result = history.get("result")
            result = (
                ensure_canonical_result(raw_result)
                if isinstance(raw_result, Mapping)
                else raw_result
            )
            history_args = history.get("args", {})
            history_file = history_args.get("file_path") or history_args.get("target")
            if (
                history["tool"] == "file_reader"
                and isinstance(result, ToolResult)
                and _total_lines(result) is not None
                and history_file == file_path
            ):
                return int(_total_lines(result) or 0)
        return None

    def try_cache(
        self, tool: str, args: ToolArgs, file_path: str, step_id: Optional[str] = None,
        *, record_result: bool = True, invocation_id: str | None = None,
        current_hash: str | None = None,
    ) -> tuple[bool, Optional[ToolResult]]:
        if tool not in ("code_analyzer", "file_reader") or not file_path or "start_line" in args or "end_line" in args:
            return False, None
        current_hash = current_hash or self._file_hash(file_path)
        memory = self.context.agent_state.memory.state
        if not current_hash or current_hash != memory.get("file_hashes", {}).get(file_path):
            return False, None
        cache_entry = memory.get("file_cache_entries", {}).get(file_path, {})
        if not isinstance(cache_entry, dict):
            cache_entry = {}
        summary = cache_entry.get("data") or memory.get("file_summaries", {}).get(file_path, "")
        if not summary:
            return False, None
        raw_provenance = cache_entry.get("evidence_provenance")
        try:
            provenance = EvidenceProvenance(str(raw_provenance))
        except ValueError:
            provenance = EvidenceProvenance.DERIVED_LOSSY
        source_extent = cache_entry.get("source_extent")
        if not isinstance(source_extent, dict):
            source_extent = {"kind": "summary"}
        complete = provenance is EvidenceProvenance.EXACT_SOURCE and source_extent.get("kind") == "whole"
        cache_metadata = {
            "complete": complete,
            "truncated": False,
            "source_identity": self._source_identity(file_path),
            "source_hash": current_hash,
            "source_extent": source_extent,
            "observation_classification": (
                ObservationClassification.CACHE_REUSE.value
                if complete
                else ObservationClassification.REDUNDANT.value
            ),
            "reusable_exact_bytes": complete,
        }
        result = ToolResult(
            invocation_id=invocation_id or f"cache:{uuid4().hex}",
            status=ToolStatus.SUCCEEDED,
            data=summary,
            message=f"Usando cache de {file_path}.",
            artifacts=((
                {
                    "kind": "cached_observation",
                    "metadata": {
                        **cache_metadata,
                        "evidence_provenance": provenance.value,
                    },
                },
            )),
            executed=False,
            evidence_provenance=provenance.value,
            metadata=cache_metadata,
        )
        self.context._emit(
            "cache_hit",
            {"file": self._source_identity(file_path), "hash": current_hash[:8]},
        )
        if record_result:
            self.context._emit("tool_end", {"tool": tool, "ok": True})
            self.context.agent_state.record_tool_result(tool, args, result, step_id=step_id)
        if complete:
            self.context._emit(
                "observation_reuse",
                {
                    "tool": tool,
                    "source_identity": self._source_identity(file_path),
                    "classification": ObservationClassification.CACHE_REUSE.value,
                    "physical_execution": False,
                },
            )
            self._record_metric(
                "observation_reuses",
                {"tool": tool, "source_identity": self._source_identity(file_path)},
            )
        return True, result

    def _file_hash(self, file_path: str) -> str | None:
        try:
            with self._resolve_user_path(file_path).open(
                "r",
                encoding="utf-8",
            ) as source:
                return hashlib.sha256(source.read().encode("utf-8")).hexdigest()
        except (OSError, ValueError):
            return None

    def post_process(
        self, step_number: int, tool: str, args: ToolArgs, result: ToolResult,
        file_path: str, objective: str, usage: Dict[str, int],
        observation_dispatch: ObservationDispatchDecision | None = None,
    ) -> bool:
        result = ensure_canonical_result(result)
        del objective
        classification = self.classify_observation_result(
            tool,
            args,
            result,
            dispatch=observation_dispatch,
        )
        metadata = dict(result.metadata) if isinstance(result.metadata, Mapping) else {}
        metadata["observation_classification"] = classification.value
        metadata["observation_physical_execution"] = result.executed is True
        if observation_dispatch is not None:
            metadata["pending_need"] = observation_dispatch.pending_need
            # Operational classification alone never proves semantic relevance.
            metadata["satisfies_pending_need"] = (
                self.newly_satisfies_observation_need(observation_dispatch, result)
            )
        annotate = getattr(self.context.agent_state, "annotate_last_tool_result", None)
        if callable(annotate):
            annotate(result, metadata=metadata)
        if classification is ObservationClassification.CONTEXT_REHYDRATION:
            self.context._emit(
                "observation_rehydration",
                {
                    "tool": tool,
                    "classification": classification.value,
                    "physical_execution": True,
                    "source_identity": self._source_identity(file_path),
                },
            )
            self._record_metric(
                "observation_rehydrations",
                {"tool": tool, "source_identity": self._source_identity(file_path)},
            )
        if result.ok:
            self.invalidate_observation_state(tool, usage, args=args, result=result)
        if tool == "file_writer" and result.ok and file_path.endswith(".py"):
            lint_error = self.context.workspace.lint_check(file_path)
            if lint_error:
                self.context._emit("warning", {"step": step_number, "warning": f"Problemas de lint em '{file_path}':\n{lint_error}"})
        total_lines = _total_lines(result)
        if tool == "file_reader" and result.ok and total_lines is not None:
            total = total_lines
            if args.get("end_line", total) == total:
                usage[f"fully_read_{file_path}"] = 1
        self.context.context_manager.maybe_compress_context()
        return True


def _total_lines(result: ToolResult) -> int | None:
    """Read completeness metadata from the typed result/artifact boundary."""

    metadata = result.metadata
    if isinstance(metadata, Mapping) and type(metadata.get("total_lines")) is int:
        return int(metadata["total_lines"])
    for artifact in result.artifacts:
        if not isinstance(artifact, Mapping):
            continue
        artifact_metadata = artifact.get("metadata")
        if isinstance(artifact_metadata, Mapping) and type(
            artifact_metadata.get("total_lines")
        ) is int:
            return int(artifact_metadata["total_lines"])
    return None
