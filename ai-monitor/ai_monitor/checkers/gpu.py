"""NVIDIA GPU stats from `nvidia-smi` (temperature, VRAM, utilization, power, fan)."""

import asyncio
import shutil
import subprocess
import sys

import httpx

from ..config import Provider
from ..models import CheckResult

FIELDS = ("index", "name", "temperature.gpu", "utilization.gpu", "memory.used", "memory.total",
          "power.draw", "power.limit", "fan.speed")
KEYS = ("index", "name", "temp", "util", "mem_used_mb", "mem_total_mb", "power_w", "power_limit_w", "fan")


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:  # "[N/A]", "[Not Supported]"
        return None


def parse(output: str) -> list[dict]:
    gpus = []
    for line in output.strip().splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(FIELDS):
            continue
        gpu = {k: (v if k == "name" else _num(v)) for k, v in zip(KEYS, cells)}
        gpus.append(gpu)
    return gpus


def _run(exe: str, timeout: float) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flash on Windows
    return subprocess.run([exe, f"--query-gpu={','.join(FIELDS)}", "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, timeout=timeout, creationflags=flags)


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    exe = p.options.get("nvidia_smi") or shutil.which("nvidia-smi")
    if not exe:
        return CheckResult("unknown", "ไม่พบ nvidia-smi (ติดตั้ง NVIDIA driver หรือยัง?)")
    proc = await asyncio.to_thread(_run, exe, p.timeout_s)
    if proc.returncode != 0:
        return CheckResult("down", f"nvidia-smi error: {(proc.stderr or proc.stdout).strip()[:150]}")
    gpus = parse(proc.stdout)
    if not gpus:
        return CheckResult("unknown", "nvidia-smi ไม่คืนข้อมูล GPU")

    gpu = gpus[0]
    temp, used, total = gpu["temp"], gpu["mem_used_mb"], gpu["mem_total_mb"]
    vram_pct = round(used * 100 / total) if used is not None and total else None
    extra = {"gpus": gpus, "temp": temp, "vram_pct": vram_pct}

    warn, crit = float(p.options.get("temp_warn_c", 80)), float(p.options.get("temp_crit_c", 90))
    vram_warn = float(p.options.get("vram_warn_pct", 95))
    parts = [f"{temp:.0f}°C" if temp is not None else None,
             f"VRAM {used / 1024:.1f}/{total / 1024:.1f} GB" if used is not None and total else None]
    detail = " · ".join(x for x in parts if x)

    if temp is not None and temp >= crit:
        return CheckResult("down", f"ร้อนเกินไป {detail}", extra=extra)
    if (temp is not None and temp >= warn) or (vram_pct is not None and vram_pct >= vram_warn):
        return CheckResult("degraded", detail, extra=extra)
    return CheckResult("up", detail, extra=extra)
