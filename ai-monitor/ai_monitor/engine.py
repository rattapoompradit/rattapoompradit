"""Runs each provider's checker on its own interval and keeps the latest state."""

import asyncio
import logging
import time
from typing import Any

import httpx

from .checkers import CHECKERS
from .config import Config, Provider
from .models import CheckResult
from .store import Store

log = logging.getLogger(__name__)


# Local services must never go through a system/VPN proxy (httpx picks those up from Windows settings).
LOCAL_TYPES = {"ollama", "gpu"}  # gpu may call LibreHardwareMonitor on localhost


def _error_result(p: Provider, exc: Exception) -> CheckResult:
    # A status page we cannot reach says nothing about the service itself.
    status = "unknown" if p.type == "status_page" else "down"
    if isinstance(exc, httpx.ConnectError):
        where = f" {exc.request.url.host}:{exc.request.url.port}" if p.type in LOCAL_TYPES and _has_request(exc) else ""
        detail = f"เชื่อมต่อ{where}ไม่ได้" + (" (Ollama ไม่ได้รัน?)" if p.type == "ollama" else "")
    elif isinstance(exc, httpx.TimeoutException):
        detail = f"ไม่ตอบภายใน {p.timeout_s:g} วินาที"
    elif isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}"
    else:
        detail = f"{type(exc).__name__}: {exc}"[:200]
    return CheckResult(status, detail)


def _has_request(exc: httpx.HTTPError) -> bool:
    try:
        return exc.request is not None
    except RuntimeError:
        return False


async def _run_checker(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    try:
        return await CHECKERS[p.type](p, client)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a status
        log.debug("check %s failed", p.id, exc_info=True)
        return _error_result(p, exc)


def _clients() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    headers = {"User-Agent": "ai-monitor/0.1"}
    return (httpx.AsyncClient(follow_redirects=True, headers=headers),
            httpx.AsyncClient(trust_env=False, headers=headers))


async def check_all(cfg: Config) -> list[tuple[Provider, CheckResult]]:
    """Run every provider once (no debounce); used by `--check`."""
    remote, local = _clients()
    async with remote, local:
        results = await asyncio.gather(*(_run_checker(p, local if p.type in LOCAL_TYPES else remote)
                                         for p in cfg.providers))
    return list(zip(cfg.providers, results))


class Monitor:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.state: dict[str, dict[str, Any]] = {}
        self._fails: dict[str, int] = {}

    async def check_once(self, p: Provider, client: httpx.AsyncClient) -> dict[str, Any]:
        result = await _run_checker(p, client)

        # Only turn red after fail_threshold consecutive failures.
        if result.status == "down":
            fails = self._fails[p.id] = self._fails.get(p.id, 0) + 1
            if fails < p.fail_threshold:
                result.status = "degraded"
                result.detail = f"{result.detail} (ลองใหม่ {fails}/{p.fail_threshold})"
        else:
            self._fails[p.id] = 0

        now = time.time()
        prev = self.state.get(p.id)
        changed = prev is not None and prev["status"] != result.status
        entry = {
            "status": result.status,
            "detail": result.detail,
            "latency_ms": result.latency_ms,
            "extra": result.extra,
            "checked_at": now,
            "since": now if prev is None or changed else prev["since"],
        }
        self.state[p.id] = entry
        self.store.add_check(p.id, result.status, result.latency_ms, result.detail, result.extra)
        if changed:
            self.store.add_event(p.id, prev["status"], result.status, result.detail)
        return entry

    async def _loop(self, p: Provider, client: httpx.AsyncClient) -> None:
        while True:
            started = time.monotonic()
            await self.check_once(p, client)
            # interval_s is the time between check starts, so a 1-second card really updates every second
            await asyncio.sleep(max(0.1, p.interval_s - (time.monotonic() - started)))

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.to_thread(self.store.purge, self.cfg.retention_days)
            await asyncio.sleep(86400)

    async def run(self) -> None:
        remote, local = _clients()
        async with remote, local:
            await asyncio.gather(self._purge_loop(), *(
                self._loop(p, local if p.type in LOCAL_TYPES else remote) for p in self.cfg.providers))
