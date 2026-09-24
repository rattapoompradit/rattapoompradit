"""Load config.yaml and .env."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS = {"interval_s": 60, "timeout_s": 15, "degraded_latency_ms": 5000, "fail_threshold": 2}
_PROVIDER_KEYS = {"id", "name", "group", "type", *DEFAULTS}


@dataclass
class Provider:
    id: str
    name: str
    type: str
    group: str = ""
    interval_s: float = 60
    timeout_s: float = 15
    degraded_latency_ms: int = 5000
    fail_threshold: int = 2
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    providers: list[Provider]
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("ai-monitor.db")
    retention_days: int = 30
    router: dict[str, Any] = field(default_factory=dict)  # Hermes Router status source (router_status.py)


def load_env(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = {**DEFAULTS, **(raw.get("defaults") or {})}
    providers = []
    for item in raw.get("providers") or []:
        values = {k: item.get(k, defaults[k]) for k in DEFAULTS}
        providers.append(Provider(
            id=item["id"],
            name=item.get("name", item["id"]),
            type=item["type"],
            group=item.get("group", ""),
            options={k: v for k, v in item.items() if k not in _PROVIDER_KEYS},
            **values,
        ))
    db_path = Path(raw.get("db_path", "ai-monitor.db"))
    if not db_path.is_absolute():
        db_path = path.parent / db_path
    return Config(
        providers=providers,
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8765)),
        db_path=db_path,
        retention_days=int(raw.get("retention_days", 30)),
        router=raw.get("router") or {},
    )
