"""Human-readable rendering for the operational receipt."""

from __future__ import annotations

from typing import Any


def print_operational_receipt(result: Any) -> None:
    receipt = getattr(result, "receipt", None)
    if not isinstance(receipt, dict) or not receipt:
        return
    print(chr(10) + "Operational receipt:")
    print(f"  status: {getattr(result, 'status', receipt.get('status', ''))}")
    print(f"  workspace: {receipt.get('workspace', getattr(result, 'workspace', ''))}")
    tools = receipt.get("tools") or []
    print("  tools:")
    for item in tools:
        if not isinstance(item, dict):
            continue
        identity = item.get("invocation_id") or "-"
        print(
            f"    {item.get('tool', '')}: status={item.get('status', '')} "
            f"executed={item.get('executed')} invocation={identity}"
        )
    files = receipt.get("files_affected") or []
    print(f"  files_affected: {', '.join(map(str, files)) if files else '[]'}")
    if receipt.get("final_state") is not None:
        print(f"  final_state: {receipt['final_state']}")
    validation = receipt.get("validation")
    if isinstance(validation, dict):
        print(f"  validation: {validation.get('outcome') if validation.get('ran') else 'not_run'}")
    rollback = receipt.get("rollback")
    if isinstance(rollback, dict) and rollback.get("occurred"):
        print(f"  rollback: {rollback.get('outcome') or 'restored'}")
    if receipt.get("replan") is not None:
        print(f"  replan: {receipt['replan']}")
    cause = receipt.get("error")
    if isinstance(cause, dict):
        print(f"  cause: {cause.get('code')} ({cause.get('layer')}): {cause.get('message')}")
    report_path = getattr(result, "report_path", None) or receipt.get("report_path")
    if report_path:
        print(f"  report_path: {report_path}")
