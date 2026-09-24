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
    assert len(cfg.providers) == 9
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


def test_ollama_states():
    from ai_monitor.checkers import ollama as ol
    ol._speed.clear()
    expires = "2030-01-01T00:00:00Z"
    loaded = []
    generated = []

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3.5:14b", "size": 9 * 1024**3, "details": {"parameter_size": "14.8B", "quantization_level": "Q4_K_M"}},
                {"name": "llama3"}]})
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.1"})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": loaded})
        if req.url.path == "/api/generate":
            generated.append(json.loads(req.content))
            return httpx.Response(200, json={"eval_count": 16, "eval_duration": 400_000_000})
        return httpx.Response(404)

    p = Provider("q", "Q", "ollama", options={"model_hint": "qwen3.5"})
    r = run(ol.check, p, handler)
    assert (r.status, r.extra["state"], r.extra["quant"], r.extra["ollama_version"]) == ("up", "ready", "Q4_K_M", "0.12.1")
    assert not generated  # never loads a model just to measure it

    loaded.append({"name": "qwen3.5:14b", "size": 10 * 1024**3, "size_vram": 8 * 1024**3,
                   "expires_at": expires, "context_length": 8192})
    r = run(ol.check, p, handler)
    x = r.extra
    assert (x["state"], x["gpu_pct"], x["vram_gb"], x["speed"]["tok_s"], x["context"]) == ("active", 80, 8.0, 40.0, 8192)
    assert "CPU 20%" in r.detail
    assert generated[0]["options"]["num_predict"] == 16 and generated[0]["keep_alive"].endswith("s")
    run(ol.check, p, handler)
    assert len(generated) == 1  # probe interval respected

    loaded.clear()
    r = run(ol.check, p, handler)
    assert (r.extra["state"], r.extra["speed"]["tok_s"]) == ("ready", 40.0)  # last speed stays visible while idle

    p.options["model_hint"] = "sparkx"
    r = run(ol.check, p, handler)
    assert (r.status, r.extra["state"]) == ("degraded", "missing")
    assert "qwen3.5:14b" in r.detail


def test_ollama_base_url(monkeypatch):
    from ai_monitor.checkers.ollama import base_url
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert base_url("auto") == "http://127.0.0.1:11434"
    assert base_url("http://box:1234/") == "http://box:1234"
    for env, want in [("0.0.0.0", "http://127.0.0.1:11434"), ("0.0.0.0:9999", "http://127.0.0.1:9999"),
                      (":8080", "http://127.0.0.1:8080"), ("http://192.168.1.5:11434", "http://192.168.1.5:11434")]:
        monkeypatch.setenv("OLLAMA_HOST", env)
        assert base_url(None) == want, env


def test_check_all_uses_proxy_free_client_for_local(monkeypatch):
    from ai_monitor import engine
    seen = {}

    async def fake(p, client):
        seen[p.id] = client._trust_env
        return CheckResult("up")

    monkeypatch.setitem(checkers.CHECKERS, "ollama", fake)
    monkeypatch.setitem(checkers.CHECKERS, "fake", fake)
    cfg = Config(providers=[Provider("o", "O", "ollama"), Provider("r", "R", "fake")])
    asyncio.run(engine.check_all(cfg))
    assert seen == {"o": False, "r": True}


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


def test_usage_claude_code(tmp_path, monkeypatch):
    from ai_monitor.checkers import usage
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    usage._cache.clear()
    assert "ไม่พบ" in asyncio.run(usage.claude_code(None, 5))["error"]

    creds = {"claudeAiOauth": {"accessToken": "tok", "expiresAt": (time.time() + 3600) * 1000, "subscriptionType": "max"}}
    (tmp_path / ".credentials.json").write_text(json.dumps(creds))
    seen = {}

    def handler(req):
        seen.update(auth=req.headers["authorization"], beta=req.headers["anthropic-beta"])
        return httpx.Response(200, json={"five_hour": {"utilization": 42.4, "resets_at": "2030-01-01T00:00:00Z"},
                                         "seven_day": {"utilization": 10, "resets_at": None}, "seven_day_opus": None})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await usage.fetch("claude_code", client, 5)
    out = asyncio.run(go())
    assert seen == {"auth": "Bearer tok", "beta": "oauth-2025-04-20"}
    assert out["plan"] == "max"
    assert [(b["label"], b["used_pct"]) for b in out["bars"]] == [("5 ชม.", 42), ("สัปดาห์", 10)]
    assert out["bars"][0]["resets_at"] == 1893456000

    creds["claudeAiOauth"]["expiresAt"] = 1000
    (tmp_path / ".credentials.json").write_text(json.dumps(creds))
    assert "หมดอายุ" in asyncio.run(usage.claude_code(None, 5))["error"]


def test_usage_codex_and_status_page_integration(tmp_path, monkeypatch):
    from ai_monitor.checkers import usage
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    usage._cache.clear()
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"access_token": "t", "account_id": "acc"}}))

    def handler(req):
        if req.url.host == "chatgpt.com":
            assert req.headers["chatgpt-account-id"] == "acc"
            return httpx.Response(200, json={"plan_type": "plus", "rate_limit": {
                "primary_window": {"used_percent": 100, "limit_window_seconds": 18000, "reset_after_seconds": 60, "reset_at": 1893456000},
                "secondary_window": {"used_percent": 30, "limit_window_seconds": 604800, "reset_after_seconds": 60, "reset_at": 1893456000}}})
        return httpx.Response(200, json={"status": {"indicator": "none", "description": "All good"}})

    p = Provider("chatgpt", "ChatGPT", "status_page", options={"url": "https://status/summary.json", "usage": "codex"})
    r = run(status_page.check, p, handler)
    assert [(b["label"], b["used_pct"]) for b in r.extra["usage"]["bars"]] == [("5 ชม.", 100), ("สัปดาห์", 30)]
    assert (r.status, r.detail) == ("degraded", "ใช้โควตา 5 ชม. หมดแล้ว")

    usage._cache.clear()
    (tmp_path / "auth.json").write_text("{}")
    r = run(status_page.check, p, lambda req: httpx.Response(503))
    assert r.status == "unknown" and "Codex" in r.extra["usage"]["error"]


def test_gpu_parse_and_status(monkeypatch):
    import subprocess
    from ai_monitor.checkers import gpu

    out = "0, NVIDIA GeForce RTX 4090, 67, 58, 17818, 24564, 286.40, 450.00, 46\n1, Old GPU, 40, 0, 10, 2048, [N/A], [N/A], [Not Supported]\n"
    gpus = gpu.parse(out)
    assert gpus[0]["name"] == "NVIDIA GeForce RTX 4090" and gpus[0]["temp"] == 67 and gpus[1]["power_w"] is None

    def fake_run(stdout, code=0):
        return lambda exe, timeout: subprocess.CompletedProcess([exe], code, stdout=stdout, stderr="boom")

    p = Provider("gpu", "GPU", "gpu", options={"nvidia_smi": "nvidia-smi"})
    monkeypatch.setattr(gpu, "_run", fake_run(out))
    r = asyncio.run(gpu.check(p, None))
    assert (r.status, r.detail, r.extra["vram_pct"]) == ("up", "67°C · VRAM 17.4/24.0 GB", 73)

    monkeypatch.setattr(gpu, "_run", fake_run(out.replace(", 67,", ", 84,", 1)))
    assert asyncio.run(gpu.check(p, None)).status == "degraded"
    monkeypatch.setattr(gpu, "_run", fake_run(out.replace(", 67,", ", 93,", 1)))
    assert asyncio.run(gpu.check(p, None)).status == "down"
    monkeypatch.setattr(gpu, "_run", fake_run("", code=9))
    assert asyncio.run(gpu.check(p, None)).detail == "nvidia-smi error: boom"

    monkeypatch.setattr(gpu.shutil, "which", lambda name: None)
    assert asyncio.run(gpu.check(Provider("gpu", "GPU", "gpu"), None)).status == "unknown"


def test_history_series_from_extra():
    store = Store(":memory:")
    for t in (60, 65):
        store.add_check("gpu", "up", None, "", {"temp": t})
    store.add_check("q", "up", 12, "", {})
    assert store.latencies("gpu", extra_key="temp") == [60, 65]
    assert store.latencies("q") == [12]


def test_benchmark_and_api(monkeypatch):
    from ai_monitor.checkers import ollama as ol
    ol._speed.clear()
    sent = []

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        if req.url.path == "/api/generate":
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"eval_count": 96, "eval_duration": 2_000_000_000, "prompt_eval_count": 120,
                                             "prompt_eval_duration": 150_000_000, "load_duration": 2_345_000_000})
        return httpx.Response(200, json={"models": []})

    p = Provider("q", "Q", "ollama", options={"model_hint": "qwen"})
    r = run(ol.benchmark, p, handler)
    assert (r["tok_s"], r["prompt_tok_s"], r["load_s"], r["source"]) == (48.0, 800.0, 2.3, "bench")
    assert sent[0]["options"]["num_predict"] == 96 and "keep_alive" not in sent[0]

    async def fake_bench(p, client):
        return {"tok_s": 12.5}

    async def fake_check(p, client):
        return CheckResult("up", "ok")

    monkeypatch.setattr(ol, "benchmark", fake_bench)
    monkeypatch.setitem(checkers.CHECKERS, "ollama", fake_check)
    cfg = Config(providers=[p, Provider("g", "G", "gpu")])
    app = create_app(cfg, store=Store(":memory:"), start=False)
    with TestClient(app) as client:
        assert client.post("/api/bench/q").json() == {"tok_s": 12.5}
        assert app.state.monitor.state["q"]["detail"] == "ok"  # card refreshed immediately
        assert client.post("/api/bench/g").status_code == 404


def test_desktop_helpers(tmp_path, monkeypatch):
    from types import SimpleNamespace as S
    from ai_monitor import desktop
    screens = [S(width=2560, height=1440), S(width=2560, height=720)]
    assert desktop.pick_screen(screens) is screens[1]
    assert desktop.pick_screen(screens[:1]) is screens[0]
    assert desktop.pick_screen([]) is None

    monkeypatch.setattr(desktop, "bundled", lambda name: ROOT / name)
    desktop.ensure_files(tmp_path)
    assert (tmp_path / "config.yaml").read_bytes() == (ROOT / "config.yaml").read_bytes()
    assert (tmp_path / ".env").is_file()
    (tmp_path / "config.yaml").write_text("mine", encoding="utf-8")
    desktop.ensure_files(tmp_path)
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "mine"  # never overwritten


def test_ollama_autostart(monkeypatch):
    from ai_monitor.checkers import ollama as ol
    started = []
    monkeypatch.setattr(ol.shutil, "which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(ol.subprocess, "Popen", lambda args, **kw: started.append(args))
    ol._autostart_at.clear()

    def refused(req):
        raise httpx.ConnectError("refused", request=req)

    p = Provider("q", "Q", "ollama", options={"model_hint": "qwen"})
    r = run(ol.check, p, refused)
    assert (r.status, r.extra["state"]) == ("degraded", "starting") and started == [["/usr/bin/ollama", "serve"]]
    r = run(ol.check, p, refused)  # within cooldown: waits, does not spawn again
    assert r.extra["state"] == "starting" and len(started) == 1

    ol._autostart_at.clear()
    p.options["auto_start"] = False
    try:
        run(ol.check, p, refused)
        raise AssertionError("expected ConnectError")
    except httpx.ConnectError:
        pass
    assert len(started) == 1

    p.options.update(auto_start=True, base_url="http://192.168.1.9:11434")  # never for another machine
    try:
        run(ol.check, p, refused)
    except httpx.ConnectError:
        pass
    assert len(started) == 1


def test_router_status_sources(tmp_path):
    from ai_monitor.router_status import read_router, normalize_status

    # nothing configured, nothing found -> N/A (never guessed)
    r = read_router({}, tmp_path)
    assert r["available"] is False

    # JSON-lines: the latest decision is the last routing record
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "router.jsonl"
    log.write_text("\n".join([
        json.dumps({"route": "code", "model": "qwen3.5:14b", "reason": "Code task", "status": "done", "ts": 1893456000}),
        json.dumps({"event": "heartbeat"}),
        json.dumps({"decision": {"route_type": "direct", "selected_model": "sparkx-2.5"}, "state": "running"}),
        "not json",
    ]), encoding="utf-8")
    r = read_router({}, tmp_path)  # auto-discovered
    assert (r["available"], r["route"], r["model"], r["status"], r["reason"]) == (True, "DIRECT", "sparkx-2.5", "RUNNING", None)
    assert r["at"] == log.stat().st_mtime  # no timestamp in the record -> file time

    # explicit file + dotted field override, millisecond timestamp
    f = tmp_path / "last.json"
    f.write_text(json.dumps({"kind": "complex", "target": {"name": "glm-flash"}, "why": "Multi-step", "ts": 1893456000000}), encoding="utf-8")
    r = read_router({"file": str(f), "fields": {"route": "kind", "model": "target.name"}}, None)
    assert (r["route"], r["model"], r["reason"], r["at"]) == ("COMPLEX", "glm-flash", "Multi-step", 1893456000)

    # plain-text log + regex
    t = tmp_path / "agent.log"
    t.write_text('x\nroute=KANBAN model=nemotron reason="Board update" status=routing\nother line\n', encoding="utf-8")
    pattern = r'route=(?P<route>\w+) model=(?P<model>\S+) reason="(?P<reason>[^"]*)" status=(?P<status>\w+)'
    r = read_router({"log": str(t), "pattern": pattern}, None)
    assert (r["route"], r["model"], r["reason"], r["status"]) == ("KANBAN", "nemotron", "Board update", "ROUTING")
    assert read_router({"log": str(t)}, None)["available"] is False  # pattern required

    assert read_router({"file": str(tmp_path / "missing.json")}, None)["available"] is False
    assert read_router({"auto_discover": False}, tmp_path)["available"] is False
    assert [normalize_status(s) for s in ("failed", "IDLE", "weird", None)] == ["ERROR", "IDLE", "WEIRD", None]


def test_router_api():
    cfg = Config(providers=[])
    app = create_app(cfg, store=Store(":memory:"), start=False, router_reader=lambda: {"available": False, "note": "x"})
    with TestClient(app) as client:
        assert client.get("/api/router").json() == {"available": False, "note": "x"}


def test_openai_compat_error_message_and_auth_header(monkeypatch):
    monkeypatch.setenv("T_KEY", "k")
    p = Provider("m", "M", "openai_compat", options={"base_url": "https://api/v1", "api_key_env": "T_KEY"})
    r = run(openai_compat.check, p, lambda req: httpx.Response(401, json={"error": {"message": "Invalid API key"}}))
    assert r.detail == "API key ใช้ไม่ได้ (HTTP 401: Invalid API key)"

    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json={"data": [{"id": "mimo-v2"}]})

    p.options["auth_header"] = "api-key"
    assert run(openai_compat.check, p, handler).status == "up"
    assert seen["api-key"] == "k" and "authorization" not in seen
