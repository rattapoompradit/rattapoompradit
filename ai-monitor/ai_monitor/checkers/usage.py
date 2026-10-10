"""Subscription usage limits, using the login that Claude Code / Codex CLI already keep on this machine.

Tokens are only read (never refreshed or stored) and only sent to the provider that issued them.
Neither endpoint is an official public API, so any failure is shown on the card, not raised.

claude_code: <claude_home option, else the freshest of CLAUDE_CONFIG_DIR, ~/.claude, ~/.config/claude>/.credentials.json {"claudeAiOauth": {"accessToken", "expiresAt"(ms)}}
             GET https://api.anthropic.com/api/oauth/usage
             -> {"five_hour": {"utilization", "resets_at"(ISO)}, "seven_day": {...}, "seven_day_opus": {...}|null}
codex:       <codex_home option, CODEX_HOME or ~/.codex>/auth.json   (one card per ChatGPT account: give each its own
             codex_home and run `CODEX_HOME=<that folder> codex login` once per account) {"tokens": {"access_token", "account_id"}}
             GET https://chatgpt.com/backend-api/wham/usage
             -> {"plan_type", "rate_limit": {"primary_window": {"used_percent", "limit_window_seconds", "reset_at"(epoch)},
                                             "secondary_window": {...}}}
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLAUDE_WINDOWS = (("five_hour", "5 ชม."), ("seven_day", "สัปดาห์"), ("seven_day_opus", "Opus/สัปดาห์"))


def _home(env: str, default: str, configured: str | None = None) -> Path:
    if configured:
        return Path(os.path.expandvars(str(configured))).expanduser()
    return Path(os.environ.get(env) or Path.home() / default)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window_label(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "โควตา"
    if seconds >= 6 * 86400:
        return "สัปดาห์"
    return f"{round(seconds / 3600)} ชม."


def _auth_error(code: int, app: str) -> dict:
    if code in (401, 403):
        return {"error": f"token ใช้ไม่ได้ เปิด {app} สักครั้งเพื่อต่ออายุ"}
    return {"error": f"ดึงโควตาไม่ได้ (HTTP {code})"}


def _claude_dirs(home: str | None) -> list[Path]:
    if home:
        return [_home("", "", home)]
    dirs = [Path(os.environ["CLAUDE_CONFIG_DIR"])] if os.environ.get("CLAUDE_CONFIG_DIR") else []
    return dirs + [Path.home() / ".claude", Path.home() / ".config" / "claude"]


async def claude_code(client: httpx.AsyncClient, timeout: float, home: str | None = None) -> dict:
    # Claude Code CLI renews .credentials.json whenever it runs; a copy no CLI renews just ages out, so take the
    # login that is valid the longest. Hermes may read the same file for its Anthropic provider.
    found = []
    for folder in _claude_dirs(home):
        path = folder / ".credentials.json"
        oauth = (_read_json(path) or {}).get("claudeAiOauth") or {}
        if oauth.get("accessToken"):
            expires = oauth.get("expiresAt")
            found.append((expires / 1000 if isinstance(expires, (int, float)) else float("inf"), path, oauth))
    if not found:
        return {"error": "ไม่พบการล็อกอิน Claude Code (รัน `claude` ใน terminal แล้วล็อกอินด้วย subscription)"}
    expires_at, path, oauth = max(found, key=lambda f: f[0])
    token = oauth["accessToken"]
    if expires_at < time.time():
        since = datetime.fromtimestamp(expires_at).strftime("%d/%m %H:%M")
        return {"error": f"token ใน {path} หมดอายุตั้งแต่ {since} (ไม่มีใครต่ออายุไฟล์นี้) — "
                         "รัน `claude` ใน terminal แล้วพิมพ์ /login เพื่อเขียนไฟล์ใหม่"}

    r = await client.get(CLAUDE_USAGE_URL, timeout=timeout, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
    if r.status_code in (401, 403):
        return {"error": f"token ใน {path} ใช้ไม่ได้ (HTTP {r.status_code}) — รัน `claude` ใน terminal แล้วพิมพ์ /login"}
    if r.status_code >= 400:
        return _auth_error(r.status_code, "Claude Code")
    data = r.json()
    bars = []
    for key, label in CLAUDE_WINDOWS:
        window = data.get(key)
        if isinstance(window, dict) and window.get("utilization") is not None:
            bars.append({"label": label, "used_pct": round(float(window["utilization"])),
                         "resets_at": _epoch(window.get("resets_at"))})
    return {"plan": oauth.get("subscriptionType"), "bars": bars}


async def codex(client: httpx.AsyncClient, timeout: float, home: str | None = None) -> dict:
    folder = _home("CODEX_HOME", ".codex", home)
    auth = _read_json(folder / "auth.json") or {}
    tokens = auth.get("tokens") or {}
    token = tokens.get("access_token")
    if not token:
        return {"error": f"ไม่พบการล็อกอิน Codex CLI ใน {folder} (รัน `codex login` ด้วยบัญชี ChatGPT)"}
    headers = {"Authorization": f"Bearer {token}"}
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]

    r = await client.get(CODEX_USAGE_URL, timeout=timeout, headers=headers)
    if r.status_code >= 400:
        return _auth_error(r.status_code, "Codex CLI")
    data = r.json()
    limits = data.get("rate_limit") or {}
    bars = []
    for key in ("primary_window", "secondary_window"):
        window = limits.get(key)
        if isinstance(window, dict) and window.get("used_percent") is not None:
            bars.append({"label": _window_label(window.get("limit_window_seconds")),
                         "used_pct": round(float(window["used_percent"])),
                         "resets_at": _epoch(window.get("reset_at"))})
    return {"plan": data.get("plan_type"), "bars": bars}


SOURCES = {"claude_code": claude_code, "codex": codex}
_cache: dict[str, tuple[float, dict]] = {}


async def fetch(source: str, client: httpx.AsyncClient, timeout: float, every_s: float = 300,
                home: str | None = None) -> dict:
    """Usage for `source` (from the login in `home`, if given), re-fetched at most every `every_s` seconds."""
    key = f"{source}|{home or ''}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < every_s:
        return cached[1]
    try:
        result = await SOURCES[source](client, timeout, home)
    except (httpx.HTTPError, ValueError) as exc:
        result = {"error": f"ดึงโควตาไม่ได้: {type(exc).__name__}"}
    _cache[key] = (time.time(), result)
    return result
