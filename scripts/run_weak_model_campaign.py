"""CLI for the isolated weak-model evaluation campaign."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llm_agent.agent.evaluation.weak_model_campaign import run_weak_model_campaign  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("standard", "compact", "both"), default="both")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--canary", action="store_true", help="Run a bounded 10-fixture subset for an authorized later live canary.")
    parser.add_argument("--live", action="store_true", help="Use configured model; incurs provider calls/cost.")
    parser.add_argument("--config", type=Path, help="JSON config source required with --live.")
    parser.add_argument("--profile", help="Named model profile selected from --config.")
    parser.add_argument("--output", type=Path, default=Path("reports/weak-model-campaign.json"))
    args = parser.parse_args(argv)
    if args.canary and args.repetitions != 1:
        parser.error("--canary is bounded to one repetition")
    if args.canary and args.variant == "both":
        parser.error("--canary runs one variant; choose --variant standard or --variant compact")
    if args.live and args.config is None:
        parser.error("--live requires --config so the selected model cannot silently fall back to defaults")
    if not args.live and (args.config is not None or args.profile is not None):
        parser.error("--config and --profile are only accepted with --live")

    config: dict[str, Any] | None = None
    gateway_factory = None
    profile_fingerprint = None
    configured_model_alias = None
    if args.live:
        try:
            config = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("configuration root must be an object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"cannot load --config: {type(exc).__name__}")
        from llm_agent.agent.evaluation.trace import RecordingGateway
        from llm_agent.agent.llm.model_profile import resolve_model_profile
        from llm_agent.agent.llm.providers.factory import create_model_gateway

        resolved = resolve_model_profile(config, profile_name=args.profile)
        profile_fingerprint = resolved.fingerprint
        configured_model_alias = resolved.name
        config["default_model_profile"] = resolved.name
        def gateway_factory(_objective: str, _workspace: Path) -> Any:
            return RecordingGateway(create_model_gateway(resolved))

    variants = ("standard", "compact") if args.variant == "both" else (args.variant,)
    try:
        report = run_weak_model_campaign(
            ROOT, variants=variants, repetitions=args.repetitions,
            live=args.live, config=config, profile_fingerprint=profile_fingerprint,
            gateway_factory=gateway_factory, output_path=args.output,
            case_ids=(
                ("h1-direct-no-tool", "PV1-02", "PV1-03", "PV1-05", "r4-h5-continuation", "r5-h6-fail-closed", "r7-h8-failure", "r10-h10-false", "WM-JSON", "WM-REPORT")
                if args.canary else None
            ),
            configured_model_alias=configured_model_alias,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": type(exc).__name__, "model_used": "unknown"}))
        return 2
    return _finish_report(report, args)


def _finish_report(report: dict[str, Any], args: argparse.Namespace) -> int:
    failed = sum(1 for item in report["results"] if not item["passed"])
    comparison_valid = report["comparison_valid"] is True
    gate_status = report["evaluation_gate"]["status"]
    status = (
        "invalid_source_changed" if not comparison_valid
        else gate_status if args.live
        else "passed" if failed == 0 else "completed_with_failures"
    )
    print(json.dumps({
        "status": status,
        "model_used": report["model_used"], "total": len(report["results"]),
        "failed": failed, "report": str(args.output),
        "evaluation_gate": report["evaluation_gate"]["status"],
    }, ensure_ascii=False))
    if not comparison_valid or (args.live and gate_status == "inconclusive"):
        return 2
    return 1 if failed or (args.live and gate_status == "failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
