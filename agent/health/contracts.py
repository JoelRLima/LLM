"""Shared health-report contracts without environment assumptions."""

from __future__ import annotations

import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_ICON = {STATUS_OK: "OK", STATUS_WARNING: "AVISO", STATUS_ERROR: "ERRO"}


@dataclass
class CheckResult:
    name: str
    status: str = STATUS_OK
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def safe_check(name: str, function: Callable[[], object]) -> CheckResult:
    try:
        result = function()
        if isinstance(result, CheckResult):
            return result
        return CheckResult(
            name,
            STATUS_WARNING,
            "Verificação não retornou CheckResult.",
            {"raw_result": str(result)},
        )
    except Exception as exc:
        return CheckResult(
            name,
            STATUS_ERROR,
            f"Falha inesperada: {exc}",
            {"traceback": traceback.format_exc()},
        )
