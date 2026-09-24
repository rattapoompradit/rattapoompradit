"""Sample data for `python -m ai_monitor --demo` (no real checks are made)."""

import random
import time

from .engine import Monitor

SAMPLES = {
    "hermes": ("up", "Gateway ทำงาน (2 ช่องทาง)", None, {
        "pid": 18244, "uptime_s": 3 * 86400 + 4 * 3600, "active_agents": 1, "version": "0.9.2",
        "model": "qwen3.5:14b", "provider": "custom",
        "platforms": {"telegram": {"state": "connected"}, "discord": {"state": "retrying", "error": "rate limited"}},
        "sessions": {"today": 18, "tokens_today": 412_380, "last_at": time.time() - 540, "last_source": "telegram"},
        "cron": {"total": 4, "enabled": 3, "failed": 0, "next_at": time.time() + 1500, "next_name": "morning-brief"},
        "errors": ["2026-09-24 09:12:44 WARNING gateway.discord: 429 Too Many Requests, retry in 30s"],
    }),
    "chatgpt": ("up", "ChatGPT: operational", 180, {"usage": {"plan": "plus", "bars": [
        {"label": "5 ชม.", "used_pct": 23, "resets_at": time.time() + 3 * 3600},
        {"label": "สัปดาห์", "used_pct": 61, "resets_at": time.time() + 2 * 86400}]}}),
    "claude": ("degraded", "claude.ai: degraded performance", 210, {
        "incident": "Elevated errors on Claude Opus",
        "usage": {"plan": "max", "bars": [
            {"label": "5 ชม.", "used_pct": 78, "resets_at": time.time() + 5400},
            {"label": "สัปดาห์", "used_pct": 44, "resets_at": time.time() + 4 * 86400},
            {"label": "Opus/สัปดาห์", "used_pct": 93, "resets_at": time.time() + 4 * 86400}]}}),
    "mimo": ("unknown", "ยังไม่ได้ใส่ MIMO_API_KEY ใน .env", None, {}),
    "glm": ("degraded", "โดน rate limit (HTTP 429)", 950, {"model": "glm-4.5-flash", "quota_pct": 6}),
    "nemotron": ("up", "API ใช้ได้", 640, {"model": "nvidia/nemotron-3-nano", "quota_pct": 72}),
    "sparkx": ("down", "เชื่อมต่อไม่ได้ (Ollama ไม่ได้รัน?)", None, {}),
    "qwen": ("up", "โหลดอยู่ใน VRAM 10.8 GB", 35, {"model": "qwen3.5:14b", "loaded": True, "vram_gb": 10.8}),
}


def seed(monitor: Monitor) -> None:
    rng = random.Random(7)
    now = time.time()
    for p in monitor.cfg.providers:
        status, detail, latency, extra = SAMPLES.get(p.id, ("unknown", "ตัวอย่าง", None, {}))
        base = latency or 0
        for _ in range(40):
            value = int(base * rng.uniform(0.7, 1.4)) if latency else None
            monitor.store.add_check(p.id, "up" if rng.random() > 0.05 else "degraded", value, "", {})
        monitor.state[p.id] = {"status": status, "detail": detail, "latency_ms": latency, "extra": extra,
                               "checked_at": now, "since": now - rng.randint(120, 7200)}
    for provider, frm, to, detail in [("glm", "up", "degraded", "โดน rate limit (HTTP 429)"),
                                      ("sparkx", "up", "down", "เชื่อมต่อไม่ได้"),
                                      ("claude", "up", "degraded", "claude.ai: degraded performance")]:
        monitor.store.add_event(provider, frm, to, detail)
