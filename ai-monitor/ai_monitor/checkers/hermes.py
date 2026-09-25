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
        home = Path(env).expanduser()
        # A profile home (<root>/profiles/<name>) has no gateway of its own; monitor the main Hermes home.
        return home.parent.parent if home.parent.name == "profiles" else home
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


ACTIVE_WINDOW_S = 300  # a session with activity in the last 5 minutes and no end counts as running


def _session_dbs(home: Path) -> list[tuple[str | None, Path]]:
    """(profile name or None for the main home, state.db) for the main Hermes home and every profile."""
    dbs = [(None, home / "state.db")]
    dbs += [(db.parent.name, db) for db in sorted((home / "profiles").glob("*/state.db"))]
    return [(name, db) for name, db in dbs if db.is_file()]


def _query_sessions(db: Path, today: float, now: float) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    try:
        try:  # newer schema also has cache token columns
            count, tokens = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0)"
                " FROM sessions WHERE started_at >= ?", (today,)).fetchone()
        except sqlite3.OperationalError:
            count, tokens = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)"
                " FROM sessions WHERE started_at >= ?", (today,)).fetchone()
        try:  # running = not ended and active recently (Hermes heartbeats last_activity_at)
            (active,) = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
                " AND COALESCE(last_activity_at, started_at) >= ?", (now - ACTIVE_WINDOW_S,)).fetchone()
        except sqlite3.OperationalError:
            active = 0
        try:
            last = conn.execute("SELECT started_at, source, model FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError:  # older schema without model
            last = conn.execute("SELECT started_at, source, NULL FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return {"today": count, "tokens_today": tokens, "active": active, "last": last}


def _sessions(home: Path, now: float | None = None) -> dict[str, Any] | None:
    """Today's sessions / tokens and running sessions, summed over the main home and all profiles
    (each Hermes profile keeps its own state.db)."""
    now = now or time.time()
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    total = {"today": 0, "tokens_today": 0, "active": 0, "last_at": None, "last_source": None,
             "last_model": None, "last_profile": None, "profiles": []}
    found = False
    for profile, db in _session_dbs(home):
        try:
            r = _query_sessions(db, today, now)
        except sqlite3.Error:
            continue
        found = True
        total["today"] += r["today"]
        total["tokens_today"] += r["tokens_today"]
        total["active"] += r["active"]
        if r["today"] or r["active"]:
            total["profiles"].append(profile or "main")
        last = r["last"]
        if last and (total["last_at"] is None or last[0] > total["last_at"]):
            total.update(last_at=last[0], last_source=last[1], last_model=last[2], last_profile=profile)
    return total if found else None


QUEUED = ("triage", "todo", "scheduled", "ready")


def _kanban(home: Path, now: float | None = None) -> dict[str, Any] | None:
    """Task counts by status over every Kanban board: <root>/kanban.db (default board) and
    <root>/kanban/boards/*/kanban.db (HERMES_KANBAN_HOME overrides the root, as in Hermes)."""
    root = Path(os.environ["HERMES_KANBAN_HOME"]).expanduser() if os.environ.get("HERMES_KANBAN_HOME") else home
    dbs = [root / "kanban.db", *sorted((root / "kanban" / "boards").glob("*/kanban.db"))]
    now = now or time.time()
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    counts: dict[str, int] = {}
    done_today, boards = 0, 0
    for db in (d for d in dbs if d.is_file()):
        try:
            conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
            try:
                for status, n in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status"):
                    counts[status] = counts.get(status, 0) + n
                (d,) = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done' AND completed_at >= ?",
                                    (today,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        boards += 1
        done_today += d
    if not boards:
        return None
    return {"boards": boards, "running": counts.get("running", 0), "queued": sum(counts.get(s, 0) for s in QUEUED),
            "blocked": counts.get("blocked", 0), "review": counts.get("review", 0), "done_today": done_today}


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
        "sessions": _sessions(home),
        "cron": _cron(home / "cron" / "jobs.json"),
        "kanban": _kanban(home),
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


def read_pool_credential(home: Path, provider: str) -> tuple[str, str | None, str | None] | None:
    """(key, inference_base_url, base_url) from Hermes' credential pool (`hermes auth add`), read-only.

    inference_base_url is where Hermes actually routes the key; plain base_url is often just the default
    `hermes auth add` fills in (e.g. the pay-as-you-go endpoint for a Token Plan key), so callers should
    rank it below their own configuration.

    Looks in <home>/auth.json, then <home>/profiles/*/auth.json: credential_pool.<provider> is a list of
    {"access_token", "priority", "base_url", "inference_base_url", "last_status"}; lowest priority wins,
    credentials marked exhausted go last."""
    files = [home / "auth.json", *sorted((home / "profiles").glob("*/auth.json"))]
    for path in files:
        data = _read_json(path)
        pool = (data or {}).get("credential_pool") if isinstance(data, dict) else None
        entries = pool.get(provider) if isinstance(pool, dict) else None
        usable = [e for e in entries or [] if isinstance(e, dict) and str(e.get("access_token") or "").strip()]
        if not usable:
            continue
        best = min(usable, key=lambda e: (e.get("last_status") == "exhausted", e.get("priority") or 0))
        def url(key: str) -> str | None:
            return str(best[key]).rstrip("/") if best.get(key) else None

        return str(best["access_token"]).strip(), url("inference_base_url"), url("base_url")
    return None


def resolve_home(options: dict) -> Path:
    home = options.get("home", "auto")
    return default_home() if home in (None, "auto") else Path(os.path.expandvars(str(home))).expanduser()


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    return await asyncio.to_thread(read_status, resolve_home(p.options), bool(p.options.get("gateway_required", True)))
