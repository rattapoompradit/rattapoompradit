import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_monitor import checkers
from ai_monitor.app import create_app
from ai_monitor.checkers import hermes, ollama, openai_compat, status_page
from ai_monitor.config import Config, Provider, load_config
from ai_monitor.engine import Monitor
from ai_monitor.models import CheckResult
from ai_monitor.store import Store

ROOT = Path(__file__).resolve().parents[1]


def run(checker, provider, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await checker(provider, client)
    return asyncio.run(go())


def test_example_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert {p.type for p in cfg.providers} <= set(checkers.CHECKERS)
    assert len(cfg.providers) == 8
    hermes_p = next(p for p in cfg.providers if p.type == "hermes")
    assert hermes_p.interval_s == 15 and hermes_p.options["home"] == "auto"


def test_status_page_component_and_indicator():
    body = {"status": {"indicator": "minor", "description": "Minor"},
            "components": [{"name": "ChatGPT", "status": "major_outage"}],
            "incidents": [{"name": "Login errors"}]}
    handler = lambda req: httpx.Response(200, json=body)
    p = Provider("c", "C", "status_page", options={"url": "https://x/summary.json", "component": "chatgpt"})
    r = run(status_page.check, p, handler)
    assert (r.status, r.extra["incident"]) == ("down", "Login errors")
    p.options["component"] = "missing"
    assert run(status_page.check, p, handler).status == "degraded"


def test_openai_compat(monkeypatch):
    p = Provider("g", "G", "openai_compat", options={"base_url": "https://api/v1", "api_key_env": "T_KEY", "model_hint": "flash"})
    monkeypatch.delenv("T_KEY", raising=False)
    assert run(openai_compat.check, p, lambda r: httpx.Response(200)).status == "unknown"

    monkeypatch.setenv("T_KEY", "k")
    assert run(openai_compat.check, p, lambda r: httpx.Response(401)).status == "down"
    assert run(openai_compat.check, p, lambda r: httpx.Response(429)).status == "degraded"

    models = {"data": [{"id": "glm-4.5"}, {"id": "glm-4.5-flash"}]}
    headers = {"x-ratelimit-remaining-requests": "5", "x-ratelimit-limit-requests": "20"}
    r = run(openai_compat.check, p, lambda req: httpx.Response(200, json=models, headers=headers))
    assert (r.status, r.extra["model"], r.extra["quota_pct"]) == ("up", "glm-4.5-flash", 25)

    p.options["model_hint"] = "nope"
    assert run(openai_compat.check, p, lambda req: httpx.Response(200, json=models)).status == "degraded"


def test_openai_compat_probe_sends_one_token(monkeypatch):
    monkeypatch.setenv("T_KEY", "k")
    sent = []

    def handler(req):
        if req.url.path.endswith("/chat/completions"):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    p = Provider("probe", "P", "openai_compat", options={"base_url": "https://api/v1", "api_key_env": "T_KEY", "model": "m1", "probe": True})
    assert run(openai_compat.check, p, handler).detail == "ตอบ prompt ได้"
    run(openai_compat.check, p, handler)  # within probe_interval_s: no second probe
    assert len(sent) == 1 and sent[0]["max_tokens"] == 1


def test_ollama():
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:14b"}, {"name": "llama3"}]})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:14b", "size_vram": 2 * 1024**3}]})

    p = Provider("q", "Q", "ollama", options={"model_hint": "qwen3.5"})
    r = run(ollama.check, p, handler)
    assert (r.status, r.extra["loaded"], r.extra["vram_gb"]) == ("up", True, 2.0)
    p.options["model_hint"] = "sparkx"
    assert run(ollama.check, p, handler).status == "down"


def make_hermes_home(tmp_path: Path, pid: int) -> Path:
    home = tmp_path / "hermes"
    (home / "cron").mkdir(parents=True)
    (home / "logs").mkdir()
    (home / "gateway.pid").write_text(json.dumps({"pid": pid}))
    (home / "gateway_state.json").write_text(json.dumps({
        "gateway_state": "running", "active_agents": 1, "code_version": "0.9",
        "platforms": {"telegram": {"state": "connected"}}}))
    (home / "config.yaml").write_text("model:\n  default: qwen3.5\n  provider: custom\n")
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"name": "a", "next_run_at": "2030-01-01T08:00:00+07:00", "last_status": "error"},
        {"name": "b", "enabled": False}]}))
    (home / "logs" / "errors.log").write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, input_tokens INT, output_tokens INT)")
    db.execute("INSERT INTO sessions VALUES ('s1', 'telegram', ?, 100, 50)", (time.time(),))
    db.commit()
    db.close()
    return home


def test_hermes_running(tmp_path):
    home = make_hermes_home(tmp_path, os.getpid())
    r = hermes.read_status(home)
    x = r.extra
    assert r.status == "up"
    assert (x["model"], x["provider"], x["version"]) == ("qwen3.5", "custom", "0.9")
    assert x["sessions"]["today"] == 1 and x["sessions"]["tokens_today"] == 150
    assert (x["cron"]["enabled"], x["cron"]["failed"], x["cron"]["next_name"]) == (1, 1, "a")
    assert x["errors"] == ["line 7", "line 8", "line 9"]

    state = json.loads((home / "gateway_state.json").read_text())
    state["platforms"]["discord"] = {"state": "retrying"}
    (home / "gateway_state.json").write_text(json.dumps(state))
    assert hermes.read_status(home).status == "degraded"


def test_hermes_stopped_or_missing(tmp_path):
    home = make_hermes_home(tmp_path, 2**22 + 12345)  # pid that does not exist
    assert hermes.read_status(home).status == "down"
    assert hermes.read_status(home, gateway_required=False).status == "up"
    assert hermes.read_status(tmp_path / "nope").status == "unknown"


def test_engine_debounce_and_events(monkeypatch):
    results = iter([CheckResult("down", "x"), CheckResult("down", "x"), CheckResult("up", "ok")])

    async def fake(p, client):
        return next(results)

    monkeypatch.setitem(checkers.CHECKERS, "fake", fake)
    p = Provider("f", "F", "fake")
    store = Store(":memory:")
    mon = Monitor(Config(providers=[p]), store)
    statuses = [asyncio.run(mon.check_once(p, None))["status"] for _ in range(3)]
    assert statuses == ["degraded", "down", "up"]
    assert [(e["from"], e["to"]) for e in store.recent_events()] == [("down", "up"), ("degraded", "down")]


def test_engine_maps_connection_errors(monkeypatch):
    async def boom(p, client):
        raise httpx.ConnectError("refused")

    monkeypatch.setitem(checkers.CHECKERS, "boom", boom)
    p = Provider("b", "B", "boom", fail_threshold=1)
    entry = asyncio.run(Monitor(Config(providers=[p]), Store(":memory:")).check_once(p, None))
    assert (entry["status"], entry["detail"]) == ("down", "เชื่อมต่อไม่ได้")


def test_api_and_page():
    cfg = Config(providers=[Provider("q", "Q", "ollama", group="Local")])
    with TestClient(create_app(cfg, store=Store(":memory:"), start=False)) as client:
        data = client.get("/api/status").json()
        assert data["providers"][0]["status"] == "unknown"
        assert "AI Monitor" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
