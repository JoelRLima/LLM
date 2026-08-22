"""CLI for Block 7 deterministic preparation and the gated Phase 5 campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence, cast

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.block7_runner import (
    DEFAULT_DRY_RUN_EPOCH,
    DEFAULT_PROFILE,
    DEFAULT_REAL_MODEL_EPOCH,
    build_corrective_readiness,
    campaign_config,
    phase4_audit,
    run_real_model_campaign,
    run_scripted_campaign,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block 7 acceptance campaign")
    parser.add_argument("--phase", choices=("dry-run", "4", "5", "corrective-ready"), default="dry-run")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--epoch", default=DEFAULT_REAL_MODEL_EPOCH)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".audit-local/out/block7-dry-run.json"),
        help="bounded local campaign report path",
    )
    parser.add_argument(
        "--qwen-loaded",
        action="store_true",
        help="explicit authorization gate for Phase 5; never needed by dry-run",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="write the frozen campaign configuration beside the report",
    )
    arguments = parser.parse_args(argv)
    output = arguments.output

    if arguments.phase == "corrective-ready":
        dry_path = ROOT / ".audit-local" / "out" / "block7-corrective-dry-run.json"
        if dry_path.exists():
            dry_report = json.loads(dry_path.read_text(encoding="utf-8"))
        else:
            dry_report = run_scripted_campaign(ROOT, output_path=dry_path)
        readiness = build_corrective_readiness(ROOT, dry_report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "passed", "phase": "corrective-ready", "report": str(output)}))
        return 0

    if arguments.phase == "5":
        if not arguments.qwen_loaded:
            print("Phase 5 is gated: confirm that Qwen is loaded with --qwen-loaded.")
            return 2
        config_path = ROOT / "agent" / "resources" / "default_config.json"
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        profiles = raw.get("model_profiles") if isinstance(raw, dict) else {}
        profile = dict(profiles.get(arguments.profile, {})) if isinstance(profiles, dict) else {}
        from agent.evaluation.trace import RecordingGateway
        from agent.llm.providers.openai_compatible import OpenAICompatibleGateway

        def live_factory(_objective: str, _workspace: Path) -> Any:
            return RecordingGateway(OpenAICompatibleGateway(profile))

        run_real_model_campaign(
            ROOT,
            output_path=output,
            gateway_factory=cast(GatewayFactory, live_factory),
            profile_name=arguments.profile,
            epoch=arguments.epoch,
        )
        print(json.dumps({"status": "passed", "phase": 5, "report": str(output)}))
        return 0

    if arguments.phase == "4":
        audit = phase4_audit(ROOT)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "passed" if not audit["known_deterministic_blockers"] else "failed", "phase": 4}))
        return 0 if not audit["known_deterministic_blockers"] else 1

    report = run_scripted_campaign(ROOT, output_path=output)
    if arguments.write_config:
        config_path = output.with_name("block7-campaign-config.json")
        config_path.write_text(
            json.dumps(
                campaign_config(
                    ROOT,
                    output_dir=output.parent,
                    profile_name=arguments.profile,
                    epoch=DEFAULT_DRY_RUN_EPOCH,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    summary = report["summary"]
    print(
        json.dumps(
            {
                "status": "passed" if summary["failed"] == 0 and summary["unknown_failures"] == 0 else "failed",
                "phase": "dry-run",
                "total": summary["total"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "unknown_failures": summary["unknown_failures"],
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["failed"] == 0 and summary["unknown_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
