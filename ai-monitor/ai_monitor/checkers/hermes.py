"""Hermes Agent (Nous Research) status, read-only from its home directory.

Files used (Hermes home = %LOCALAPPDATA%\\hermes on Windows, ~/.hermes elsewhere):
  gateway.pid          JSON {"pid", "start_time", ...} (legacy: bare integer)
  gateway_state.json   {"gateway_state", "active_agents", "platforms": {name: {"state", "error_message"}}, ...}
  config.yaml          model.default / model.provider
  state.db             SQLite, table sessions(started_at, source, model, input_tokens, output_tokens)
  cron/jobs.json       {"jobs": [{"name", "enabled", "next_run_at", "last_status"}]}
  logs/errors.log
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
import yaml

from ..config import Provider
from ..models import CheckResult

_IGNORED_PLATFORM_STATES = {None, "", "connected", "disabled"}


def default_home() -> Path:
    if env := os.environ.get("HERMES_HOME"):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "hermes"
    return Path.home() / ".hermes"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gateway_process(home: Path) -> tuple[int | None, float | None]:
    """(pid, uptime seconds) of a live gateway, else (None, None)."""
    record = _read_json(home / "gateway.pid")
    pid = record if isinstance(record, int) else (record or {}).get("pid") if isinstance(record, dict) else None
    if not isinstance(pid, int):
        return None, None
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None, None
        return pid, time.time() - proc.create_time()
    except psutil.Error:
        return None, None


def _model(home: Path) -> dict[str, Any]:
    try:
        cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    model = cfg.get("model")
    if isinstance(model, dict):
        return {"model": model.get("default") or model.get("model"), "provider": model.get("provider")}
    return {"model": model} if isinstance(model, str) else {}


def _sessions(db: Path) -> dict[str, Any] | None:
    if not db.is_file():
        return None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            count, tokens = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)"
                " FROM sessions WHERE started_at >= ?", (today,)).fetchone()
            last = conn.execute(
                "SELECT started_at, source FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {"today": count, "tokens_today": tokens,
            "last_at": last[0] if last else None, "last_source": last[1] if last else None}


def _cron(path: Path) -> dict[str, Any] | None:
    data = _read_json(path)
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        return None
    jobs = [j for j in jobs if isinstance(j, dict)]
    enabled = [j for j in jobs if j.get("enabled", True)]
    upcoming = []
    for job in enabled:
        try:
            upcoming.append((datetime.fromisoformat(str(job["next_run_at"])).timestamp(), job))
        except (KeyError, ValueError):
            continue
    nxt = min(upcoming, key=lambda x: x[0]) if upcoming else None
    return {
        "total": len(jobs),
        "enabled": len(enabled),
        "failed": sum(1 for j in jobs if j.get("last_status") == "error"),
        "next_at": nxt[0] if nxt else None,
        "next_name": (nxt[1].get("name") or nxt[1].get("id")) if nxt else None,
    }


def _tail(path: Path, n: int = 3, max_len: int = 180) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - 16384))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    return [line[:max_len] for line in lines if line.strip()][-n:]


def read_status(home: Path, gateway_required: bool = True) -> CheckResult:
    if not home.is_dir():
        return CheckResult("unknown", f"ไม่พบโฟลเดอร์ Hermes: {home}")

    pid, uptime = _gateway_process(home)
    state = _read_json(home / "gateway_state.json") if pid else None
    state = state if isinstance(state, dict) else {}
    platforms = {
        name: {"state": v.get("state"), "error": v.get("error_message")}
        for name, v in (state.get("platforms") or {}).items() if isinstance(v, dict)
    }
    extra = {
        "home": str(home),
        "pid": pid,
        "uptime_s": uptime,
        "gateway_state": state.get("gateway_state"),
        "active_agents": state.get("active_agents"),
        "version": state.get("code_version"),
        "platforms": platforms,
        **_model(home),
        "sessions": _sessions(home / "state.db"),
        "cron": _cron(home / "cron" / "jobs.json"),
        "errors": _tail(home / "logs" / "errors.log"),
    }

    if not pid:
        if gateway_required:
            return CheckResult("down", "Gateway ไม่ได้รัน", extra=extra)
        return CheckResult("up", "โหมด CLI (ไม่ได้เปิด gateway)", extra=extra)
    broken = [f"{name}: {v['state']}" for name, v in platforms.items() if v["state"] not in _IGNORED_PLATFORM_STATES]
    if broken:
        return CheckResult("degraded", ", ".join(broken), extra=extra)
    return CheckResult("up", f"Gateway ทำงาน ({len(platforms)} ช่องทาง)", extra=extra)


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    home = p.options.get("home", "auto")
    home = default_home() if home in (None, "auto") else Path(os.path.expandvars(str(home))).expanduser()
    return await asyncio.to_thread(read_status, home, bool(p.options.get("gateway_required", True)))
