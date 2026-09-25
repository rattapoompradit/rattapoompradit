"""NVIDIA GPU stats (temperature, VRAM, utilization, power, fan) from NVML (nvidia-ml-py, milliseconds per read)
or, when that is unavailable, `nvidia-smi` (slow to start on Windows), plus CPU / RAM, how much
CPU Ollama uses (a model that does not fit in VRAM runs partly on the CPU) and, optionally, CPU temperature
from LibreHardwareMonitor's web server (Windows has no standard non-admin way to read it)."""

import asyncio
import logging
import shutil
import subprocess
import sys

import httpx
import psutil

from ..config import Provider
from ..models import CheckResult

log = logging.getLogger(__name__)

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


_nvml = None  # the pynvml module once initialised, False when NVML is unavailable


def _nvml_lib():
    global _nvml
    if _nvml is None:
        try:
            import pynvml
            pynvml.nvmlInit()
            _nvml = pynvml
        except Exception:  # noqa: BLE001 - not installed, no driver, no NVIDIA GPU: use nvidia-smi
            log.debug("NVML unavailable, using nvidia-smi", exc_info=True)
            _nvml = False
    return _nvml or None


def nvml_query(nv) -> list[dict]:
    """Same fields as parse() (MiB, W), read through NVML. Unsupported values (e.g. laptop fan) are None."""
    def get(fn, *args):
        try:
            return fn(*args)
        except nv.NVMLError:
            return None

    gpus = []
    for i in range(nv.nvmlDeviceGetCount()):
        h = nv.nvmlDeviceGetHandleByIndex(i)
        name = get(nv.nvmlDeviceGetName, h)
        mem = get(nv.nvmlDeviceGetMemoryInfo, h)
        util = get(nv.nvmlDeviceGetUtilizationRates, h)
        power = get(nv.nvmlDeviceGetPowerUsage, h)
        limit = get(nv.nvmlDeviceGetEnforcedPowerLimit, h)
        temp = get(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU)
        fan = get(nv.nvmlDeviceGetFanSpeed, h)
        gpus.append({
            "index": float(i),
            "name": name.decode(errors="replace") if isinstance(name, bytes) else name,
            "temp": float(temp) if temp is not None else None,
            "util": float(util.gpu) if util is not None else None,
            "mem_used_mb": round(mem.used / 1024**2) if mem is not None else None,
            "mem_total_mb": round(mem.total / 1024**2) if mem is not None else None,
            "power_w": round(power / 1000, 2) if power is not None else None,
            "power_limit_w": round(limit / 1000, 2) if limit is not None else None,
            "fan": float(fan) if fan is not None else None,
        })
    return gpus


_procs: dict[int, psutil.Process] = {}  # kept between checks: cpu_percent() measures since the previous call
CPU_TEMP_SENSORS = ("cpu package", "core (tctl/tdie)", "package", "cpu cores", "core average")


def cpu_stats() -> dict:
    """Whole-machine CPU % and RAM, and CPU % / RAM of Ollama processes (% of the whole machine)."""
    cores = psutil.cpu_count() or 1
    mem = psutil.virtual_memory()
    ollama_cpu, ollama_ram, seen = 0.0, 0, set()
    for proc in psutil.process_iter(["name"]):
        if "ollama" not in (proc.info.get("name") or "").lower():
            continue
        seen.add(proc.pid)
        tracked = _procs.setdefault(proc.pid, proc)
        try:
            ollama_cpu += tracked.cpu_percent(None)  # 0.0 on the first sighting, real value from the next check
            ollama_ram += tracked.memory_info().rss
        except psutil.Error:
            continue
    for pid in set(_procs) - seen:
        _procs.pop(pid, None)
    return {
        "util": psutil.cpu_percent(None),
        "cores": cores,
        "ram_used_gb": round((mem.total - mem.available) / 1024**3, 1),
        "ram_total_gb": round(mem.total / 1024**3, 1),
        "ram_pct": round(mem.percent),
        "ollama_cpu_pct": round(ollama_cpu / cores, 1) if seen else None,
        "ollama_ram_gb": round(ollama_ram / 1024**3, 1) if seen else None,
    }


def lhm_cpu_temp(data: dict) -> float | None:
    """CPU temperature from LibreHardwareMonitor's /data.json tree (Remote Web Server)."""
    found: list[tuple[int, float]] = []

    def walk(node: dict, in_cpu: bool) -> None:
        text = str(node.get("Text", ""))
        value = str(node.get("Value", ""))
        sensor = str(node.get("SensorId", "")).lower()  # e.g. /intelcpu/0/temperature/0, /amdcpu/0/...
        is_cpu_hw = "cpu" in str(node.get("ImageURL", "")).lower() or any(
            k in text.lower() for k in ("intel core", "ryzen", "cpu")) and not value.endswith("°C")
        in_cpu = in_cpu or is_cpu_hw or "cpu/" in sensor
        if in_cpu and value.endswith("°C") and text.lower() in CPU_TEMP_SENSORS:
            try:
                found.append((CPU_TEMP_SENSORS.index(text.lower()), float(value[:-2].strip().replace(",", "."))))
            except ValueError:
                pass
        for child in node.get("Children") or []:
            if isinstance(child, dict):
                walk(child, in_cpu)

    walk(data, False)
    return min(found)[1] if found else None


def _cpu_stats_safe() -> dict | None:
    try:
        return cpu_stats()
    except psutil.Error:
        return None


async def _none() -> None:
    return None


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    # GPU and CPU are read in parallel so one check fits in a 1-second interval
    result, cpu = await asyncio.gather(
        _check_gpu(p), asyncio.to_thread(_cpu_stats_safe) if p.options.get("cpu", True) else _none())
    if cpu is not None:
        result.extra["cpu"] = cpu
    if (url := p.options.get("cpu_temp_url")) and "cpu" in result.extra:
        try:
            r = await client.get(url, timeout=2)
            result.extra["cpu"]["temp"] = lhm_cpu_temp(r.json())
        except (httpx.HTTPError, ValueError):
            result.extra["cpu"]["temp"] = None
    return result


async def _check_gpu(p: Provider) -> CheckResult:
    gpus = None
    if not p.options.get("nvidia_smi") and (nv := _nvml_lib()):  # an explicit nvidia_smi path forces nvidia-smi
        try:
            gpus = await asyncio.to_thread(nvml_query, nv)
        except Exception:  # noqa: BLE001 - fall back to nvidia-smi
            log.debug("NVML read failed", exc_info=True)
    if not gpus:
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
