"""Local models served by Ollama.

States shown on the card (extra["state"]):
  active   loaded in memory (/api/ps): VRAM, GPU/CPU split, unload countdown
  ready    installed (/api/tags) but not loaded; it loads on the next request — this is normal, not an error
  missing  Ollama is running but no installed model matches model_hint
Ollama itself unreachable -> the engine reports "down" (after trying to start `ollama serve` when auto_start).

Speed (extra["speed"], last known value, kept while the model is idle):
  auto   every speed_probe_interval_s, 16 tokens, only when the model is already loaded
  bench  on request from the dashboard (benchmark()): may load the model; also measures prompt speed and load time
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import httpx

from ..config import Provider
from ..models import CheckResult

log = logging.getLogger(__name__)
DEFAULT_BASE = "http://127.0.0.1:11434"
_speed: dict[str, dict] = {}  # provider id -> {"tok_s", "prompt_tok_s", "load_s", "at", "source"}
BENCH_PROMPT = (
    "Summarise the following in three sentences, then list five key terms. "
    "Local language models run entirely on your own computer. They use the graphics card memory to hold the model "
    "weights, and when a model is larger than that memory part of it runs on the processor instead, which is much "
    "slower. Speed is measured in tokens per second, both for reading the prompt and for writing the answer. "
    "Loading a model from disk takes a few seconds the first time it is used after being idle."
)


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


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_autostart_at: dict[str, float] = {}  # base url -> when we last tried `ollama serve`
AUTOSTART_COOLDOWN_S = 120


def _try_autostart(base: str) -> bool:
    """Start `ollama serve` hidden in the background if Ollama on this machine is not running."""
    host = base.split("://", 1)[-1].rsplit(":", 1)[0]
    exe = shutil.which("ollama")
    if host not in _LOCAL_HOSTS or not exe or time.time() - _autostart_at.get(base, 0) < AUTOSTART_COOLDOWN_S:
        return False
    _autostart_at[base] = time.time()
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
    log_file = open(os.path.join(tempfile.gettempdir(), "ai-monitor-ollama-serve.log"), "ab")
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file,
                         creationflags=flags, start_new_session=sys.platform != "win32")
    except OSError:
        log.warning("could not start ollama serve", exc_info=True)
        return False
    finally:
        log_file.close()
    log.info("started `ollama serve` for %s", base)
    return True


def _epoch(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _rate(count, duration_ns) -> float | None:
    return round(count / (duration_ns / 1e9), 1) if count and duration_ns else None


async def _measure_speed(p: Provider, client: httpx.AsyncClient, base: str, model: str, expires_at: float | None) -> None:
    """Generate a few tokens on an already-loaded model to measure tokens/sec (never loads a model)."""
    body = {"model": model, "prompt": "Hi", "stream": False, "options": {"num_predict": 16}}
    if expires_at:  # keep Ollama's own unload timer unchanged
        body["keep_alive"] = f"{max(1, int(expires_at - time.time()))}s"
    r = await client.post(f"{base}/api/generate", json=body, timeout=max(p.timeout_s, 60))
    r.raise_for_status()
    data = r.json()
    if tok_s := _rate(data.get("eval_count"), data.get("eval_duration")):
        # keep prompt speed / load time from the last full benchmark; a 2-word prompt says nothing about them
        _speed[p.id] = {**_speed.get(p.id, {}), "tok_s": tok_s, "at": time.time(), "source": "auto"}


async def benchmark(p: Provider, client: httpx.AsyncClient) -> dict:
    """Full speed test requested from the dashboard. Loads the model if needed (uses VRAM for Ollama's keep-alive)."""
    base = base_url(p.options.get("base_url"))
    r = await client.get(f"{base}/api/tags", timeout=p.timeout_s)
    r.raise_for_status()
    hint = str(p.options.get("model_hint") or "").lower()
    model = next((m.get("name") for m in r.json().get("models") or [] if hint in m.get("name", "").lower()), None)
    if not model:
        raise ValueError(f"ไม่พบโมเดล '{hint}' ใน Ollama")
    body = {"model": model, "prompt": f"[{time.time():.0f}] {BENCH_PROMPT}",  # timestamp defeats the prompt cache
            "stream": False, "options": {"num_predict": 96, "temperature": 0}}
    r = await client.post(f"{base}/api/generate", json=body, timeout=180)
    r.raise_for_status()
    data = r.json()
    result = {
        "tok_s": _rate(data.get("eval_count"), data.get("eval_duration")),
        "prompt_tok_s": _rate(data.get("prompt_eval_count"), data.get("prompt_eval_duration")),
        "load_s": round(data["load_duration"] / 1e9, 1) if data.get("load_duration") else None,
        "at": time.time(),
        "source": "bench",
        "model": model,
    }
    _speed[p.id] = result
    return result


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    base = base_url(p.options.get("base_url"))
    started = time.monotonic()
    try:
        r = await client.get(f"{base}/api/tags", timeout=p.timeout_s)
    except httpx.ConnectError:
        if p.options.get("auto_start", True) and _try_autostart(base):
            return CheckResult("degraded", "Ollama ไม่ได้รัน · กำลังเปิดให้อัตโนมัติ…", extra={"state": "starting"})
        if time.time() - _autostart_at.get(base, 0) < AUTOSTART_COOLDOWN_S:
            return CheckResult("degraded", "กำลังรอ Ollama เปิด…", extra={"state": "starting"})
        raise
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
    if speed := _speed.get(p.id):
        extra["speed"] = speed

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
            log.debug("speed probe for %s failed", p.id, exc_info=True)
        if speed := _speed.get(p.id):
            extra["speed"] = speed

    if gpu_pct is not None and gpu_pct < 100:
        detail = f"โหลดอยู่ · GPU {gpu_pct}% / CPU {100 - gpu_pct}% (ช้าลง)"
    else:
        detail = f"โหลดอยู่ใน VRAM {extra['vram_gb']} GB"
    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
