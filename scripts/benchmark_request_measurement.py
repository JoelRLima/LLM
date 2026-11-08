"""Offline benchmark for context-fit to dispatch measurement overhead."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llm_agent.agent.evaluation.trace import RecordingGateway  # noqa: E402
from llm_agent.agent.llm.context_projection import fit_contextual_request  # noqa: E402
from llm_agent.agent.llm.contracts import ModelMessage, ModelRequest  # noqa: E402
from llm_agent.agent.llm.providers.openai_compatible import OpenAICompatibleGateway  # noqa: E402
from llm_agent.agent.runtime.context import TaskExecutionContext  # noqa: E402
from llm_agent.agent.runtime.model_call import ModelCallService  # noqa: E402
from llm_agent.agent.runtime.request_measurement_reuse import RequestMeasurementReuse  # noqa: E402
from llm_agent.cancellation import CancellationToken  # noqa: E402


class Response:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code, self.payload = status, payload

    def json(self) -> dict[str, Any]:
        return self.payload

    def close(self) -> None:
        pass

    def raise_for_status(self) -> None:
        if self.status_code != 200:
            raise RuntimeError("unexpected mock status")


def benchmark(mode: str, repetitions: int, *, recording: bool = False, reuse_enabled: bool = True) -> dict[str, Any]:
    calls: Counter[str] = Counter()

    def post(url: str, **kwargs: Any) -> Response:
        if url.endswith("/input_tokens"):
            calls["exact_counter"] += 1
            return Response(200, {"input_tokens": 32}) if mode == "exact" else Response(404, {})
        if url.endswith("/tokenize"):
            calls["text_counter"] += 1
            return Response(200, {"tokens": list(range(24))})
        if url.endswith("/chat/completions"):
            calls["completion"] += 1
            return Response(200, {
                "model": "offline-fixture", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 32, "completion_tokens": 1, "total_tokens": 33},
            })
        raise AssertionError("Unmocked endpoint")

    started = perf_counter()
    measurements = []
    ledger_calls = ledger_tokens = 0
    with patch("requests.post", post):
        for _ in range(repetitions):
            base_gateway = OpenAICompatibleGateway({
                "api_url": "http://offline.invalid/v1/chat/completions", "model": "offline-fixture",
                "max_tokens": 32, "capabilities": {"token_counting": True},
            })
            profile = base_gateway.resolved_profile
            if not reuse_enabled:
                base_gateway._request_input_measurement_reuse = RequestMeasurementReuse(lambda request: None)
            gateway = RecordingGateway(base_gateway) if recording else base_gateway
            request = ModelRequest((ModelMessage("user", "answer"),), "offline-fixture", 0, 32)

            def build_request(required: str | None, optional: str | None, current: ModelRequest = request) -> ModelRequest:
                return current

            fit = fit_contextual_request(
                mandatory_request=request, context_limit=4_096, gateway=gateway,
                build_request=build_request,
            )
            context = TaskExecutionContext(
                model_gateway=gateway, model_profile=profile, cancellation=CancellationToken(),
            )
            outcome = ModelCallService.for_context(context).complete(fit.request)
            assert outcome.response.content == "ok"
            assert context.budget_ledger is not None
            snapshot = context.budget_ledger.snapshot()
            assert snapshot.model_calls == 1
            ledger_calls += snapshot.model_calls
            ledger_tokens += snapshot.accounted_tokens
            measurements.append(fit.mandatory_measurement.to_dict())
    return {
        "mode": mode, "repetitions": repetitions, "http_calls_mocked": dict(calls),
        "recording_gateway": recording,
        "reuse_enabled": reuse_enabled, "ledger_model_calls": ledger_calls, "ledger_accounted_tokens": ledger_tokens,
        "elapsed_ms_synthetic": round((perf_counter() - started) * 1_000, 3),
        "fit_measurements": measurements, "model_used": False, "network_used": False,
        "timing_predicts_live_latency": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true", help="Compare disabled and enabled handoff with the same simulated backend.")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 100:
        parser.error("--repetitions must be between 1 and 100")
    report: dict[str, Any] = {"schema_version": 1, "results": [
        benchmark(mode, args.repetitions, reuse_enabled=enabled)
        for enabled in ((False, True) if args.compare else (True,)) for mode in ("exact", "text")
    ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{key: value for key, value in item.items() if key != "fit_measurements"} for item in report["results"]]))


if __name__ == "__main__":
    main()
