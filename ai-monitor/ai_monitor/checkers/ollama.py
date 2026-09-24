"""Local models served by Ollama: installed (/api/tags) and loaded in VRAM (/api/ps)."""

import time

import httpx

from ..config import Provider
from ..models import CheckResult


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    base = p.options.get("base_url", "http://localhost:11434").rstrip("/")
    started = time.monotonic()
    r = await client.get(f"{base}/api/tags", timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    r.raise_for_status()

    hint = str(p.options.get("model_hint") or "").lower()
    names = [m.get("name", "") for m in r.json().get("models") or []]
    matched = [n for n in names if hint in n.lower()]
    if not matched:
        return CheckResult("down", f"ไม่พบโมเดล '{hint}' ใน Ollama", latency)

    ps = await client.get(f"{base}/api/ps", timeout=p.timeout_s)
    ps.raise_for_status()
    loaded = {m.get("name"): m.get("size_vram") or 0 for m in ps.json().get("models") or []}
    in_vram = [n for n in matched if n in loaded]

    extra = {"model": (in_vram or matched)[0], "loaded": bool(in_vram)}
    if in_vram:
        extra["vram_gb"] = round(sum(loaded[n] for n in in_vram) / 1024**3, 1)
        detail = f"โหลดอยู่ใน VRAM {extra['vram_gb']} GB"
    else:
        detail = "พร้อมใช้ (ยังไม่โหลดเข้า VRAM)"
    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
