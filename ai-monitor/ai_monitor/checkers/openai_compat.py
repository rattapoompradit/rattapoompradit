"""OpenAI-compatible APIs: GET /models (free), optional 1-token chat probe."""

import os
import time

import httpx

from ..config import Provider
from ..models import CheckResult

_last_probe: dict[str, float] = {}


def _server_message(r: httpx.Response) -> str:
    """The provider's own error text (e.g. "Invalid API key"), shortened; never includes our request."""
    try:
        data = r.json()
    except ValueError:
        return r.text.strip()[:120]
    if isinstance(data, dict):
        err = data.get("error", data)
        msg = err.get("message") or err.get("msg") or err.get("detail") if isinstance(err, dict) else err
        return str(msg or "").strip()[:120]
    return ""


def _http_problem(r: httpx.Response) -> CheckResult | None:
    code = r.status_code
    if code < 400:
        return None
    msg = _server_message(r)
    suffix = f": {msg}" if msg else ""
    if code in (401, 403):
        return CheckResult("down", f"API key ใช้ไม่ได้ (HTTP {code}{suffix})")
    if code == 429:
        return CheckResult("degraded", f"โดน rate limit (HTTP 429{suffix})")
    return CheckResult("down", f"HTTP {code}{suffix}")


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
    if "*" in key or "…" in key:
        return CheckResult("down", f"{env} มีเครื่องหมาย * (คัดลอกตัวที่เว็บปิดไว้) ให้กดปุ่ม Copy ในเว็บแล้ววางใหม่")
    # auth_header: "authorization" (default, "Bearer <key>") or a header name that takes the raw key, e.g. "api-key"
    auth = str(p.options.get("auth_header", "authorization")).lower()
    headers = ({"Authorization": f"Bearer {key}"} if auth == "authorization" else {auth: key}) if key else {}
    base = p.options["base_url"].rstrip("/")

    started = time.monotonic()
    r = await client.get(f"{base}/models", headers=headers, timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    if problem := _http_problem(r):
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
        if problem := _http_problem(r):
            problem.latency_ms = latency
            problem.extra = extra
            return problem
        extra["quota_pct"] = _quota(r.headers) if _quota(r.headers) is not None else extra["quota_pct"]
        detail = "ตอบ prompt ได้"

    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
