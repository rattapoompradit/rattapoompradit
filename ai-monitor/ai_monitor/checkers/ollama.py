"""Local models served by Ollama.

States shown on the card (extra["state"]):
  active   loaded in memory (/api/ps): VRAM, GPU/CPU split, unload countdown, measured tokens/sec
  ready    installed (/api/tags) but not loaded; it loads on the next request — this is normal, not an error
  missing  Ollama is running but no installed model matches model_hint
Ollama itself unreachable -> the engine reports "down".
"""

import os
import time
from datetime import datetime

import httpx

from ..config import Provider
from ..models import CheckResult

DEFAULT_BASE = "http://127.0.0.1:11434"
_speed: dict[str, dict] = {}  # provider id -> {"tok_s", "at"} from the last speed probe


def base_url(configured: str | None) -> str:
    """Configured URL, else OLLAMA_HOST (as Ollama reads it), else 127.0.0.1:11434."""
    if configured and configured != "auto":
        return configured.rstrip("/")
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return DEFAULT_BASE
    if "://" not in host:
        host = "http://" + host
    scheme, rest = host.split("://", 1)
    rest = rest.rstrip("/")
    hostname, _, port = rest.rpartition(":") if ":" in rest else (rest, "", "")
    if not hostname:  # ":11434"
        hostname = ""
    if hostname in ("", "0.0.0.0", "[::]", "::"):
        hostname = "127.0.0.1"
    return f"{scheme}://{hostname}:{port or 11434}"


def _epoch(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def _measure_speed(p: Provider, client: httpx.AsyncClient, base: str, model: str, expires_at: float | None) -> None:
    """Generate a few tokens on an already-loaded model to measure tokens/sec (never loads a model)."""
    body = {"model": model, "prompt": "Hi", "stream": False, "options": {"num_predict": 16}}
    if expires_at:  # keep Ollama's own unload timer unchanged
        body["keep_alive"] = f"{max(1, int(expires_at - time.time()))}s"
    r = await client.post(f"{base}/api/generate", json=body, timeout=max(p.timeout_s, 60))
    r.raise_for_status()
    data = r.json()
    count, duration = data.get("eval_count"), data.get("eval_duration")
    if count and duration:
        _speed[p.id] = {"tok_s": round(count / (duration / 1e9), 1), "at": time.time()}


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    base = base_url(p.options.get("base_url"))
    started = time.monotonic()
    r = await client.get(f"{base}/api/tags", timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    r.raise_for_status()
    installed = {m.get("name", ""): m for m in r.json().get("models") or []}

    extra: dict = {"server": base.split("://", 1)[-1]}
    try:
        extra["ollama_version"] = (await client.get(f"{base}/api/version", timeout=p.timeout_s)).json().get("version")
    except (httpx.HTTPError, ValueError):
        pass

    hint = str(p.options.get("model_hint") or "").lower()
    matched = [n for n in installed if hint in n.lower()]
    if not matched:
        names = list(installed)
        extra.update(state="missing", available=names)
        have = f"มี: {', '.join(names[:4])}" if names else "ยังไม่มีโมเดลเลย"
        return CheckResult("degraded", f"ไม่พบโมเดล '{hint}' ใน Ollama ({have})", latency, extra)

    ps = await client.get(f"{base}/api/ps", timeout=p.timeout_s)
    ps.raise_for_status()
    running = {m.get("name"): m for m in ps.json().get("models") or []}
    active = [n for n in matched if n in running]
    model = (active or matched)[0]
    details = installed[model].get("details") or {}
    extra.update(model=model, params=details.get("parameter_size"), quant=details.get("quantization_level"),
                 disk_gb=round((installed[model].get("size") or 0) / 1024**3, 1) or None)

    if not active:
        extra.update(state="ready", loaded=False)
        status = "degraded" if latency > p.degraded_latency_ms else "up"
        return CheckResult(status, "พร้อมใช้ · จะโหลดเข้า VRAM เมื่อมีการเรียกใช้", latency, extra)

    m = running[model]
    size, vram = m.get("size") or 0, m.get("size_vram") or 0
    gpu_pct = round(vram * 100 / size) if size else None
    expires_at = _epoch(m.get("expires_at"))
    extra.update(state="active", loaded=True, vram_gb=round(vram / 1024**3, 1), gpu_pct=gpu_pct,
                 expires_at=expires_at, context=m.get("context_length"))

    every = float(p.options.get("speed_probe_interval_s", 600))
    if p.options.get("speed_probe", True) and time.time() - _speed.get(p.id, {}).get("at", 0) >= every:
        try:
            await _measure_speed(p, client, base, model, expires_at)
        except (httpx.HTTPError, ValueError):
            _speed[p.id] = {"tok_s": None, "at": time.time()}
    if speed := _speed.get(p.id):
        extra.update(tok_s=speed["tok_s"], tok_s_at=speed["at"])

    if gpu_pct is not None and gpu_pct < 100:
        detail = f"โหลดอยู่ · GPU {gpu_pct}% / CPU {100 - gpu_pct}% (ช้าลง)"
    else:
        detail = f"โหลดอยู่ใน VRAM {extra['vram_gb']} GB"
    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
