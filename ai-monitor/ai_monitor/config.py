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
    ui_refresh_ms: int = 3000  # how often the dashboard page polls /api/status


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines, quotes, `export ` prefix, Notepad BOM)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        values[key] = value.strip().strip("\"'")
    return values


def load_env(path: Path) -> None:
    """Load a .env file into the environment; real environment variables win."""
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
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
    # API checks may fall back to the keys Hermes already uses (its own .env), see checkers/openai_compat.py
    hermes_p = next((p for p in providers if p.type == "hermes"), None)
    for p in providers:
        if p.type == "openai_compat":
            p.options.setdefault("_hermes_home", (hermes_p.options.get("home") if hermes_p else None) or "auto")
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
        # accepted at the top level or under defaults:; clamped so a typo cannot hammer or freeze the page
        ui_refresh_ms=min(60000, max(1000, int(raw.get("ui_refresh_ms") or (raw.get("defaults") or {}).get("ui_refresh_ms") or 3000))),
    )
