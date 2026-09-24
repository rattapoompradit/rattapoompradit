"""OpenAI-compatible APIs: GET /models (free), optional 1-token chat probe."""

import os
import time

import httpx

from ..config import Provider, read_env_file
from ..models import CheckResult
from . import hermes

# AI Monitor env name -> (Hermes' key variable, Hermes' base-URL variable), from Hermes' provider table
HERMES_VARS = {
    "MIMO_API_KEY": ("XIAOMI_API_KEY", "XIAOMI_BASE_URL"),
    "ZAI_API_KEY": ("GLM_API_KEY", "GLM_BASE_URL"),
    "NVIDIA_API_KEY": ("NVIDIA_API_KEY", "NVIDIA_BASE_URL"),
}
# AI Monitor env name -> Hermes provider id in its credential pool (auth.json, `hermes auth add`)
HERMES_POOL = {"MIMO_API_KEY": "xiaomi", "ZAI_API_KEY": "zai", "NVIDIA_API_KEY": "nvidia"}

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


def resolve_key(p: Provider) -> tuple[str, str, str]:
    """(key, base_url, source). key_from: "env" (AI Monitor .env only), "hermes" (Hermes first, with its
    base URL if it sets one) or "auto" (default: AI Monitor .env, else Hermes).
    Hermes keys come from its .env, else its credential pool (auth.json, incl. profiles/*/auth.json)."""
    env = p.options.get("api_key_env")
    base = p.options["base_url"].rstrip("/")
    own = os.environ.get(env, "").strip() if env else ""
    mode = str(p.options.get("key_from", "auto")).lower()
    hermes_key_var, hermes_url_var = HERMES_VARS.get(env, (p.options.get("hermes_key"), p.options.get("hermes_base_url")))
    pool_provider = p.options.get("hermes_provider") or HERMES_POOL.get(env)
    if mode == "env" or not (hermes_key_var or pool_provider) or (mode == "auto" and own):
        return own, base, ".env"
    home = hermes.resolve_home({"home": p.options.get("_hermes_home", "auto")})
    values = read_env_file(home / ".env")
    env_url = values.get(p.options.get("hermes_base_url") or hermes_url_var or "", "").strip().rstrip("/")
    if theirs := values.get(p.options.get("hermes_key") or hermes_key_var or "", "").strip():
        return theirs, env_url or base, "Hermes"
    if pool_provider and (cred := hermes.read_pool_credential(home, pool_provider)):
        return cred[0], cred[1] or env_url or base, "Hermes"
    return own, base, ".env"


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    env = p.options.get("api_key_env")
    key, base, source = resolve_key(p)
    if env and not key:
        return CheckResult("unknown", f"ยังไม่ได้ใส่ {env} ใน .env (และไม่พบใน Hermes)")
    if "*" in key or "…" in key:
        return CheckResult("down", f"{env} มีเครื่องหมาย * (คัดลอกตัวที่เว็บปิดไว้) ให้กดปุ่ม Copy ในเว็บแล้ววางใหม่")
    # auth_header: "authorization" (default, "Bearer <key>") or a header name that takes the raw key, e.g. "api-key"
    auth = str(p.options.get("auth_header", "authorization")).lower()
    headers = ({"Authorization": f"Bearer {key}"} if auth == "authorization" else {auth: key}) if key else {}
    via = " · key จาก Hermes" if source == "Hermes" else ""

    started = time.monotonic()
    r = await client.get(f"{base}/models", headers=headers, timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    if problem := _http_problem(r):
        problem.latency_ms = latency
        problem.detail += via
        return problem

    ids = [m["id"] for m in r.json().get("data") or [] if isinstance(m, dict) and "id" in m]
    model = p.options.get("model")
    hint = str(p.options.get("model_hint") or "").lower()
    if not model and hint:
        model = next((i for i in ids if hint in i.lower()), None)
        if not model:
            return CheckResult("degraded", f"API ใช้ได้ แต่ไม่พบโมเดลที่มีคำว่า '{hint}'{via}", latency)

    extra = {"model": model, "quota_pct": _quota(r.headers)}
    detail = "API ใช้ได้" + via

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
        detail = "ตอบ prompt ได้" + via

    status = "degraded" if latency > p.degraded_latency_ms else "up"
    return CheckResult(status, detail, latency, extra)
