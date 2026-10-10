"""Which models each card's service handled today, from Hermes' own usage records (read-only).

Hermes keeps per-session, per-model token counts in state.db (table session_model_usage) for the main home and
every profile. Summed per model for today, then each card takes the models that belong to it: option `models`
(substrings of the model name), else daily.match / credits.match, else gpt for Codex usage and claude for
Claude Code usage. Tokens = input + output + cache read + cache write, the same count as the daily figures.
Both cards of two ChatGPT accounts see the same models: model names do not say which account ran them.
"""

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Provider

CACHE_S = 30
TOP = 3
USAGE_KEYS = {"codex": ["gpt"], "claude_code": ["claude"]}

_cache: dict[str, tuple[float, dict]] = {}


def model_keys(p: Provider) -> list[str]:
    o = p.options
    if o.get("models"):
        keys = o["models"] if isinstance(o["models"], list) else [o["models"]]
    elif (o.get("daily") or {}).get("match"):
        keys = [o["daily"]["match"]]
    elif (o.get("credits") or {}).get("match"):
        keys = [o["credits"]["match"]]
    else:
        keys = USAGE_KEYS.get(o.get("usage"), [])
    return [str(k).lower() for k in keys if k]


def _today_start(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def read_today(home: Path | None, now: float | None = None) -> dict[str, dict[str, int]]:
    """{model: {"tokens", "calls"}} for today across the Hermes home and all profiles (cached CACHE_S)."""
    if home is None:
        return {}
    now = now or time.time()
    cached = _cache.get(str(home))
    if cached and now - cached[0] < CACHE_S:
        return cached[1]
    start = _today_start(now)
    totals: dict[str, dict[str, int]] = {}
    for db in (home / "state.db", *sorted((home / "profiles").glob("*/state.db"))):
        if not db.is_file():
            continue
        try:
            conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
            try:
                rows = conn.execute(
                    "SELECT model, COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0),"
                    " COALESCE(SUM(api_call_count), 0) FROM session_model_usage"
                    " WHERE COALESCE(last_seen, 0) >= ? GROUP BY model", (start,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue  # older Hermes without session_model_usage, or db busy
        for model, tokens, calls in rows:
            if not model:
                continue
            t = totals.setdefault(str(model), {"tokens": 0, "calls": 0})
            t["tokens"] += int(tokens or 0)
            t["calls"] += int(calls or 0)
    _cache[str(home)] = (now, totals)
    return totals


def share(usage: dict[str, dict[str, int]], keys: list[str]) -> list[dict[str, Any]]:
    """Top models of this card by today's tokens, with their share of the card's total (pct), plus "other"."""
    mine = [(m, v) for m, v in usage.items() if v["tokens"] > 0 and any(k in m.lower() for k in keys)]
    total = sum(v["tokens"] for _, v in mine)
    if not total:
        return []
    mine.sort(key=lambda mv: mv[1]["tokens"], reverse=True)
    rows = [{"model": m, "tokens": v["tokens"], "calls": v["calls"], "pct": round(v["tokens"] * 100 / total, 1)}
            for m, v in mine[:TOP]]
    if rest := mine[TOP:]:
        tokens = sum(v["tokens"] for _, v in rest)
        rows.append({"model": f"อื่น ๆ ({len(rest)})", "tokens": tokens, "calls": sum(v["calls"] for _, v in rest),
                     "pct": round(tokens * 100 / total, 1), "other": True})
    return rows
