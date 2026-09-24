"""OpenAI-compatible APIs: GET /models (free), optional 1-token chat probe."""

import os
import time

import httpx

from ..config import Provider
from ..models import CheckResult

_last_probe: dict[str, float] = {}


def _http_problem(code: int) -> CheckResult | None:
    if code in (401, 403):
        return CheckResult("down", f"API key ใช้ไม่ได้ (HTTP {code})")
    if code == 429:
        return CheckResult("degraded", "โดน rate limit (HTTP 429)")
    if code >= 400:
        return CheckResult("down", f"HTTP {code}")
    return None


def _quota(headers: httpx.Headers) -> int | None:
    """Remaining request quota in percent, when the provider sends x-ratelimit headers."""
    try:
        remaining = int(headers["x-ratelimit-remaining-requests"])
        limit = int(headers["x-ratelimit-limit-requests"])
    except (KeyError, ValueError):
        return None
    return round(remaining * 100 / limit) if limit > 0 else None


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    env = p.options.get("api_key_env")
    key = os.environ.get(env, "") if env else ""
    if env and not key:
        return CheckResult("unknown", f"ยังไม่ได้ใส่ {env} ใน .env")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    base = p.options["base_url"].rstrip("/")

    started = time.monotonic()
    r = await client.get(f"{base}/models", headers=headers, timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    if problem := _http_problem(r.status_code):
        problem.latency_ms = latency
        return problem

    ids = [m["id"] for m in r.json().get("data") or [] if isinstance(m, dict) and "id" in m]
    model = p.options.get("model")
    hint = str(p.options.get("model_hint") or "").lower()
    if not model and hint:
        model = next((i for i in ids if hint in i.lower()), None)
        if not model:
            return CheckResult("degraded", f"API ใช้ได้ แต่ไม่พบโมเดลที่มีคำว่า '{hint}'", latency)

    extra = {"model": model, "quota_pct": _quota(r.headers)}
    detail = "API ใช้ได้"

    probe_every = float(p.options.get("probe_interval_s", 1800))
    if p.options.get("probe") and model and time.time() - _last_probe.get(p.id, 0) >= probe_every:
        _last_probe[p.id] = time.time()
        started = time.monotonic()
        r = await client.post(
            f"{base}/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=p.timeout_s,
        )
        latency = int((time.monotonic() - started) * 1000)
        if problem := _http_problem(r.status_code):
            problem.latency_ms = latency
            problem.extra = extra
            return problem
        extra["quota_pct"] = _quota(r.headers) if _quota(r.headers) is not None else extra["quota_pct"]
        detail = "ตอบ prompt ได้"

    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
