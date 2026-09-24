from dataclasses import dataclass, field
from typing import Any

STATUSES = ("up", "degraded", "down", "unknown")


@dataclass
class CheckResult:
    status: str  # one of STATUSES
    detail: str = ""
    latency_ms: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
