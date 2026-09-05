"""Auxiliary context source assembly for the context manager."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.llm.context_projection import (
    UNTRUSTED_MEMORY,
    UNTRUSTED_SESSION,
    UNTRUSTED_WORKSPACE,
    ContextSourceRecord,
    discover_project_guidance,
)
from agent.llm.context_view_support import repository_state_records
from agent.memory.prompt_context import (
    DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS,
    build_memory_prompt_context,
)


class ContextAuxiliaryMixin:
    hardware_profile: Any
    agent_state: Any
    workspace_root: Path
    get_file_hints: Callable[[str], str]

    def build_auxiliary_records(
        self,
        objective: str,
        *,
        target_files: tuple[str, ...] | list[str] = (),
        required_records: tuple[ContextSourceRecord, ...] = (),
    ) -> tuple[ContextSourceRecord, ...]:
        """Build ordered untrusted sources without changing task authority."""

        records: list[ContextSourceRecord] = list(required_records)
        memory_budget = min(
            DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS,
            max(0, self.hardware_profile.context_limit // 8),
        )
        memory_projection = build_memory_prompt_context(
            self.agent_state.memory.state,
            objective=objective,
            budget_tokens=memory_budget,
            workspace_root=self.workspace_root,
        )
        if memory_projection:
            records.append(
                ContextSourceRecord(
                    source_id="memory:objective-projection",
                    source_kind="memory",
                    trust_class=UNTRUSTED_MEMORY,
                    reason="fresh objective-scoped memory projection",
                    estimated_tokens=max(1, len(memory_projection) // 4),
                    data={"content": memory_projection},
                )
            )
        hints = self.get_file_hints(objective)
        if hints:
            records.append(
                ContextSourceRecord(
                    source_id="workspace:objective-file-hints",
                    source_kind="workspace_hints",
                    trust_class=UNTRUSTED_WORKSPACE,
                    reason="objective-relevant bounded hint from existing selector",
                    estimated_tokens=max(1, len(hints) // 4),
                    data={"content": hints},
                )
            )
        guidance = discover_project_guidance(
            self.workspace_root,
            target_files,
        )
        records.extend(guidance.records)
        records.extend(repository_state_records(self.agent_state.tool_history))
        conversation = getattr(self.agent_state, "conversation_history", ())
        if conversation:
            recent = list(conversation)[-self.agent_state.max_history_turns :]
            records.append(
                ContextSourceRecord(
                    source_id="session:derived-history",
                    source_kind="derived_session",
                    trust_class=UNTRUSTED_SESSION,
                    reason="bounded recent session-derived context",
                    data={"turns": recent},
                )
            )
        return tuple(records)
