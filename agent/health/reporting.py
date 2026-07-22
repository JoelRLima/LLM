from __future__ import annotations

import json
from typing import Any, Callable, Dict, Sequence

from agent.health.core import (
    HEALTH_REPORT_PATH,
    PROJECT_ROOT,
    STATUS_ERROR,
    STATUS_ICON,
    STATUS_OK,
    STATUS_WARNING,
    ensure_sys_path,
    safe_check,
)


def run_checks(
    checks: Sequence[tuple[str, Callable[[], object]]], *, write_report: bool, verbose: bool
) -> Dict[str, Any]:
    ensure_sys_path()
    results = [safe_check(key, function) for key, function in checks]
    counts = {
        "ok": sum(item.status == STATUS_OK for item in results),
        "warnings": sum(item.status == STATUS_WARNING for item in results),
        "errors": sum(item.status == STATUS_ERROR for item in results),
    }
    problems = counts["warnings"] + counts["errors"]
    summary = "Sistema saudável." if not problems else f"Foram encontrados {problems} problema(s)."
    report: Dict[str, Any] = {
        "summary": summary, "total_checks": len(results), **counts,
        "project_root": str(PROJECT_ROOT), "checks": [item.to_dict() for item in results],
    }
    if verbose:
        print_report(report)
    if write_report:
        try:
            HEALTH_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            if verbose:
                print(f"Não foi possível salvar o relatório: {exc}")
    return report


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 70)
    print("RELATÓRIO DE SAÚDE DO AGENTE")
    print("=" * 70)
    for check in report["checks"]:
        print(f"\n{STATUS_ICON.get(check['status'], '?')} {check['name']}\n   {check['message']}")
    print("\n" + "-" * 70)
    print(report["summary"])
    print("-" * 70)
