"""Run deterministic model compatibility canaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.evaluation.model_compatibility_canaries import (  # noqa: E402
    DeterministicCanaryGateway,
    run_model_canaries,
)
from agent.llm.model_profile import resolve_model_profile  # noqa: E402
from agent.llm.providers.factory import create_model_gateway  # noqa: E402


def _load_profile(profile_name: str) -> Any:
    config_path = ROOT / "agent" / "resources" / "default_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return resolve_model_profile(config, profile_name=profile_name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model compatibility canaries")
    parser.add_argument("--mode", choices=("deterministic", "live-model"), default="deterministic")
    parser.add_argument("--profile", default="local_8gb")
    parser.add_argument("--output", type=Path, default=Path(".audit-local/out/model-compatibility-canary-deterministic.json"))
    parser.add_argument("--live-model-authorized", action="store_true")
    arguments = parser.parse_args(argv)
    profile = _load_profile(arguments.profile)
    if arguments.mode == "live-model" and not arguments.live_model_authorized:
        report = {
            "schema_version": "MODEL-COMPATIBILITY-CANARY-V1",
            "profile_name": profile.name,
            "model_config_fingerprint": profile.fingerprint,
            "declared_model_identity": {"profile": profile.name, "model": profile.model},
            "observed_model_identity": None,
            "live_model_used": False,
            "cases": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "not_applicable": 0, "blocked": 1},
            "blocked_reason": "LIVE_MODEL_AUTHORIZATION_REQUIRED",
        }
        status = 2
    else:
        gateway = (
            DeterministicCanaryGateway(profile)
            if arguments.mode == "deterministic"
            else create_model_gateway(profile)
        )
        report = run_model_canaries(
            profile,
            gateway,
            live_model_used=arguments.mode == "live-model",
        )
        status = 0 if report["summary"]["failed"] == 0 and report["summary"]["blocked"] == 0 else 1
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed" if status == 0 else "blocked" if status == 2 else "failed", **report["summary"], "live_model_used": report["live_model_used"]}, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
