"""Bounded repetition policy for H-series campaign decisions."""

from __future__ import annotations

from dataclasses import dataclass

from llm_agent.agent.evaluation.evidence import EvidenceContractError


@dataclass(frozen=True)
class RepetitionPolicy:
    """Define required samples and adaptive stopping for scenarios."""

    initial_repetitions: int = 3
    h2_repetitions: int = 5
    maximum_repetitions: int = 5

    def __post_init__(self) -> None:
        values = (self.initial_repetitions, self.h2_repetitions, self.maximum_repetitions)
        if any(type(value) is not int for value in values):
            raise EvidenceContractError("repetitions must be integers")
        if any(value < 1 for value in values):
            raise EvidenceContractError("repetitions must be positive")
        if self.initial_repetitions > self.maximum_repetitions:
            raise EvidenceContractError("initial repetitions exceed campaign maximum")
        if self.h2_repetitions > self.maximum_repetitions:
            raise EvidenceContractError("H2 repetitions exceed campaign maximum")

    def required_for(self, scenario_id: str) -> int:
        return self.h2_repetitions if scenario_id.upper() == "H2" else self.initial_repetitions

    def to_dict(self) -> dict[str, int]:
        return {
            "initial_repetitions": self.initial_repetitions,
            "h2_repetitions": self.h2_repetitions,
            "maximum_repetitions": self.maximum_repetitions,
        }

    def initial_decision(self, pass_count: int, valid_count: int) -> str:
        """Return the only permitted decision after the initial sample.

        The decision uses scenario repetitions rather than arm executions.
        H1 is one paired repetition even though it produces two arm records.
        """

        if type(valid_count) is not int or valid_count != self.initial_repetitions:
            raise EvidenceContractError(
                f"initial repetition decision requires exactly {self.initial_repetitions} valid samples"
            )
        if type(pass_count) is not int or not 0 <= pass_count <= valid_count:
            raise EvidenceContractError("pass count must be between zero and the valid sample count")
        if pass_count == valid_count:
            return "stop_unanimous_pass"
        if pass_count == 0:
            return "stop_unanimous_fail"
        return "extend_exactly_two"

    def target_for_initial_result(self, scenario_id: str, pass_count: int, valid_count: int) -> int:
        if scenario_id.upper() == "H2":
            return self.h2_repetitions
        decision = self.initial_decision(pass_count, valid_count)
        return self.initial_repetitions if decision.startswith("stop_") else self.maximum_repetitions
