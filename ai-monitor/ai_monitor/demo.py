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
    "sparkx": ("up", "พร้อมใช้ · จะโหลดเข้า VRAM เมื่อมีการเรียกใช้", 18, {
        "state": "ready", "loaded": False, "model": "sparkx-2.5:8b", "params": "8.2B", "quant": "Q5_K_M",
        "disk_gb": 5.6, "ollama_version": "0.12.1", "server": "127.0.0.1:11434",
        "speed": {"tok_s": 61.3, "prompt_tok_s": 1480, "load_s": 2.4, "at": time.time() - 5400, "source": "bench"}}),
    "qwen": ("up", "โหลดอยู่ใน VRAM 10.8 GB", 35, {
        "state": "active", "loaded": True, "model": "qwen3.5:14b", "params": "14.8B", "quant": "Q4_K_M",
        "vram_gb": 10.8, "gpu_pct": 100, "context": 8192, "expires_at": time.time() + 222,
        "speed": {"tok_s": 52.4, "prompt_tok_s": 912, "load_s": 3.1, "at": time.time() - 30, "source": "auto"},
        "ollama_version": "0.12.1", "server": "127.0.0.1:11434"}),
    "gpu": ("up", "67°C · VRAM 17.4/24.0 GB", None, {"temp": 67, "vram_pct": 73, "gpus": [{
        "index": 0, "name": "NVIDIA GeForce RTX 4090", "temp": 67, "util": 58, "mem_used_mb": 17818,
        "mem_total_mb": 24564, "power_w": 286.4, "power_limit_w": 450, "fan": 46}]}),
}


def seed(monitor: Monitor) -> None:
    rng = random.Random(7)
    now = time.time()
    for p in monitor.cfg.providers:
        status, detail, latency, extra = SAMPLES.get(p.id, ("unknown", "ตัวอย่าง", None, {}))
        base = latency or 0
        for _ in range(40):
            value = int(base * rng.uniform(0.7, 1.4)) if latency else None
            past = {"temp": rng.randint(58, 72)} if p.type == "gpu" else {}
            monitor.store.add_check(p.id, "up" if rng.random() > 0.05 else "degraded", value, "", past)
        monitor.state[p.id] = {"status": status, "detail": detail, "latency_ms": latency, "extra": extra,
                               "checked_at": now, "since": now - rng.randint(120, 7200)}
    for provider, frm, to, detail in [("glm", "up", "degraded", "โดน rate limit (HTTP 429)"),
                                      ("sparkx", "down", "up", "Ollama กลับมาแล้ว"),
                                      ("claude", "up", "degraded", "claude.ai: degraded performance")]:
        monitor.store.add_event(provider, frm, to, detail)


def router_sample() -> dict:
    """Sample Hermes Router decision for demo mode (real mode reads it from the router's own output)."""
    return {"available": True, "route": "DIRECT", "model": "sparkx-2.5:8b", "reason": "Simple task",
            "status": "RUNNING", "at": time.time() - 12, "source": "demo", "read_at": time.time()}
