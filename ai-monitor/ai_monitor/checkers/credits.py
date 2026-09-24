"""Estimated monthly plan credits (e.g. MiMo Token Plan), counted from Hermes' own usage records.

Providers like MiMo expose no quota API, so the bar is an *estimate*: tokens Hermes recorded for matching
models in state.db (table session_model_usage; input_tokens there is uncached input, cache_read_tokens the
cache hits) times the per-token credit rates from config. Usage by other tools on the same plan is not seen.

config (under the provider):
  credits:
    plan: Lite
    monthly: 4100000000
    renews_at: "2026-10-22T23:59:59Z"   # "Valid until" from the plan page; rolls forward by cycle_days
    cycle_days: 30
    match: mimo                          # model name / billing provider substring to count
    rates:                               # credits per token: [cache hit input, cache miss input, output]
      mimo-v2.5-pro: [2.5, 300, 600]
    offpeak_utc: [16, 24]                # hours with the discount factor
    offpeak_factor: 0.8
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DAY = 86400


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def cycle(renews_at: float, cycle_days: float, now: float) -> tuple[float, float]:
    """(start, end) of the billing cycle containing `now`, rolling an auto-renewing plan forward."""
    length = cycle_days * DAY
    end = renews_at
    while end <= now:
        end += length
    while end - length > now:
        end -= length
    return end - length, end


def _model_id(name: str) -> str:
    """"xiaomi/MiMo-V2.6-Pro:latest" -> "mimo-v2.6-pro" (drop vendor prefix and tag, lowercase)."""
    return str(name).strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]


def _rate(rates: dict, model: str) -> list[float] | None:
    """Exact model match only: a variant such as mimo-v2.6-pro-ultraspeed can cost far more than
    mimo-v2.6-pro, so it must not borrow that rate; unmatched models are reported, not guessed."""
    wanted = _model_id(model)
    for key, value in rates.items():
        if _model_id(key) == wanted:
            return [float(x) for x in value]
    return None


def _offpeak(ts: float, hours: list, factor: float) -> float:
    start, end = (int(h) for h in hours)
    hour = datetime.fromtimestamp(ts, timezone.utc).hour
    return factor if start <= hour < end else 1.0


def _rows(db: Path, match: str, since: float) -> list[tuple]:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    try:
        return conn.execute(
            "SELECT model, input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, last_seen"
            " FROM session_model_usage WHERE (lower(model) LIKE ? OR lower(billing_provider) LIKE ?)"
            " AND COALESCE(last_seen, 0) >= ?",
            (f"%{match}%", f"%{match}%", since)).fetchall()
    finally:
        conn.close()


def estimate(cfg: dict, hermes_home: Path, now: float) -> dict:
    """Usage entry in the same shape as the subscription usage bars ({"plan", "bars", ...})."""
    monthly = float(cfg.get("monthly") or 0)
    renews = _epoch(cfg.get("renews_at"))
    if monthly <= 0 or renews is None:
        return {"error": "ตั้ง credits.monthly และ credits.renews_at ใน config.yaml"}
    start, end = cycle(renews, float(cfg.get("cycle_days", 30)), now)
    match = str(cfg.get("match", "mimo")).lower()
    rates = cfg.get("rates") or {}
    hours, factor = cfg.get("offpeak_utc", [16, 24]), float(cfg.get("offpeak_factor", 0.8))

    dbs = [hermes_home / "state.db", *sorted((hermes_home / "profiles").glob("*/state.db"))]
    used, tokens, unpriced = 0.0, 0, set()
    for db in (d for d in dbs if d.is_file()):
        try:
            rows = _rows(db, match, start)
        except sqlite3.Error:
            continue  # older Hermes without session_model_usage, or db busy
        for model, miss, hit, write, out, last_seen in rows:
            miss, hit, write, out = (int(x or 0) for x in (miss, hit, write, out))
            tokens += miss + hit + write + out
            rate = _rate(rates, model or "")
            if rate is None:
                unpriced.add(model)
                continue
            cost = hit * rate[0] + (miss + write) * rate[1] + out * rate[2]
            used += cost * _offpeak(last_seen or now, hours, factor)

    notes = ["ประมาณการจาก token ที่ Hermes ใช้ (เครื่องมืออื่นไม่นับ)"]
    if unpriced:
        notes.append("ไม่มีอัตรา credit ของ " + ", ".join(sorted(unpriced)) + " (เพิ่มใน credits.rates)")
    return {
        "plan": cfg.get("plan"),
        "estimated": True,
        "bars": [{
            "label": "Credits (ประมาณ)",
            "used_pct": round(used * 100 / monthly, 1),
            "used": used, "total": monthly, "tokens": tokens,
            "resets_at": end,
        }],
        "note": " · ".join(notes),
        "console_url": cfg.get("console_url"),
        "cycle_start": start,
    }

