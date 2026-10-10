"""Official status pages in Atlassian Statuspage format (/api/v2/summary.json),
plus optional subscription usage bars (option `usage: claude_code | codex`, login folder `codex_home` / `claude_home`)."""

import time

import httpx

from ..config import Provider
from ..models import CheckResult
from . import usage

INDICATOR = {"none": "up", "minor": "degraded", "maintenance": "degraded", "major": "down", "critical": "down"}
COMPONENT = {
    "operational": "up",
    "degraded_performance": "degraded",
    "partial_outage": "degraded",
    "under_maintenance": "degraded",
    "major_outage": "down",
}


async def check(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    source = p.options.get("usage")
    if not source:
        return await _status(p, client)
    home = p.options.get("codex_home" if source == "codex" else "claude_home")
    quota = await usage.fetch(source, client, p.timeout_s, float(p.options.get("usage_interval_s", 300)), home)
    try:
        result = await _status(p, client)
    except (httpx.HTTPError, ValueError) as exc:
        result = CheckResult("unknown", f"เข้า status page ไม่ได้ ({type(exc).__name__})")
    result.extra["usage"] = quota
    full = [b for b in quota.get("bars", []) if b["used_pct"] >= 100]
    if full and result.status == "up":
        result.status = "degraded"
        result.detail = f"ใช้โควตา {full[0]['label']} หมดแล้ว"
    return result


async def _status(p: Provider, client: httpx.AsyncClient) -> CheckResult:
    started = time.monotonic()
    r = await client.get(p.options["url"], timeout=p.timeout_s)
    latency = int((time.monotonic() - started) * 1000)
    r.raise_for_status()
    data = r.json()

    wanted = (p.options.get("component") or "").lower()
    comp = next((c for c in data.get("components") or []
                 if isinstance(c, dict) and wanted and wanted in str(c.get("name", "")).lower()), None)
    healthy = comp is not None and comp.get("status") == "operational"
    # A status page lists incidents for all of the company's products. On the card, show one only if it names the
    # watched component, or if it names none (some pages never do) while that component is not operational.
    incidents, other = [], []
    for i in data.get("incidents") or []:
        if not isinstance(i, dict):
            continue
        names = [str(c.get("name", "")).lower() for c in i.get("components") or [] if isinstance(c, dict)]
        ours = any(wanted in n for n in names) if names else not healthy
        (incidents if not wanted or ours else other).append(i.get("name", ""))
    extra: dict = {"incident": incidents[0]} if incidents else {}
    if other:
        extra["other_incidents"] = other
    if comp is not None:
        state = comp.get("status", "")
        return CheckResult(COMPONENT.get(state, "unknown"), f"{comp['name']}: {state.replace('_', ' ')}", latency, extra)

    status = data.get("status") or {}
    indicator = status.get("indicator")
    if indicator is None:
        return CheckResult("unknown", "อ่านรูปแบบ status page ไม่ออก", latency, extra)
    return CheckResult(INDICATOR.get(indicator, "unknown"), status.get("description", indicator), latency, extra)
