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


def test_openai_compat_masked_key(monkeypatch):
    monkeypatch.setenv("T_KEY", "tp-abc*****xyz")
    p = Provider("m", "M", "openai_compat", options={"base_url": "https://api/v1", "api_key_env": "T_KEY"})
    r = run(openai_compat.check, p, lambda req: httpx.Response(200, json={"data": []}))
    assert r.status == "down" and "Copy" in r.detail


def test_api_key_from_hermes(tmp_path, monkeypatch):
    from ai_monitor.checkers.openai_compat import resolve_key
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text("\ufeffXIAOMI_API_KEY=tp-hermes\nexport XIAOMI_BASE_URL=https://token-plan-sgp.x/v1/\n", encoding="utf-8")
    opts = {"base_url": "https://api.x/v1", "api_key_env": "MIMO_API_KEY", "_hermes_home": str(home)}

    monkeypatch.setenv("MIMO_API_KEY", "own-key")
    assert resolve_key(Provider("m", "M", "openai_compat", options=dict(opts))) == ("own-key", "https://api.x/v1", ".env")
    assert resolve_key(Provider("m", "M", "openai_compat", options={**opts, "key_from": "hermes"})) == (
        "tp-hermes", "https://token-plan-sgp.x/v1", "Hermes")
    monkeypatch.delenv("MIMO_API_KEY")
    assert resolve_key(Provider("m", "M", "openai_compat", options=dict(opts)))[2] == "Hermes"  # auto falls back
    assert resolve_key(Provider("m", "M", "openai_compat", options={**opts, "key_from": "env"}))[0] == ""

    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers["authorization"]
        return httpx.Response(200, json={"data": [{"id": "mimo-v2.6-pro"}]})

    r = run(openai_compat.check, Provider("m", "M", "openai_compat", options={**opts, "model_hint": "mimo"}), handler)
    assert r.status == "up" and r.detail.endswith("key จาก Hermes")
    assert seen == {"url": "https://token-plan-sgp.x/v1/models", "auth": "Bearer tp-hermes"}


def test_config_passes_hermes_home_to_api_checks(tmp_path):
    (tmp_path / "c.yaml").write_text(
        "providers:\n  - {id: h, type: hermes, home: 'D:/h'}\n  - {id: m, type: openai_compat, base_url: 'x'}\n", encoding="utf-8")
    cfg = load_config(tmp_path / "c.yaml")
    assert cfg.providers[1].options["_hermes_home"] == "D:/h"


def test_api_key_from_hermes_credential_pool(tmp_path, monkeypatch):
    from ai_monitor.checkers.hermes import read_pool_credential
    from ai_monitor.checkers.openai_compat import resolve_key
    home = tmp_path / "hermes"
    (home / "profiles" / "mimo").mkdir(parents=True)
    (home / ".env").write_text("# XIAOMI_API_KEY=your_key_here\n", encoding="utf-8")  # commented out, like the sample
    (home / "profiles" / "mimo" / "auth.json").write_text(json.dumps({"credential_pool": {"xiaomi": [
        {"access_token": "tp-exhausted", "priority": 0, "last_status": "exhausted"},
        {"access_token": "tp-second", "priority": 2},
        {"access_token": "tp-first", "priority": 1, "base_url": "https://api.x/v1/",
         "inference_base_url": "https://token-plan-sgp.x/v1/"},
        {"access_token": "", "priority": -1}]}}), encoding="utf-8")
    assert read_pool_credential(home, "xiaomi") == ("tp-first", "https://token-plan-sgp.x/v1", "https://api.x/v1")
    assert read_pool_credential(home, "zai") is None

    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    p = Provider("m", "M", "openai_compat", options={"base_url": "https://api.x/v1", "api_key_env": "MIMO_API_KEY",
                                                     "key_from": "hermes", "_hermes_home": str(home)})
    assert resolve_key(p) == ("tp-first", "https://token-plan-sgp.x/v1", "Hermes")


def test_hermes_home_ignores_profile_override(tmp_path, monkeypatch):
    from ai_monitor.checkers.hermes import default_home
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes" / "profiles" / "mimo"))
    assert default_home() == tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))
    assert default_home() == tmp_path / "custom"


def test_mimo_credit_estimate(tmp_path):
    from datetime import datetime, timezone
    from ai_monitor.checkers import credits
    home = tmp_path / "hermes"
    (home / "profiles" / "mimo").mkdir(parents=True)
    db = sqlite3.connect(home / "profiles" / "mimo" / "state.db")
    db.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INT,"
               " output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, last_seen REAL)")
    peak = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc).timestamp()      # 10:00 Thai time
    offpeak = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc).timestamp()  # 01:00 Thai time
    old = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()              # previous cycle
    db.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?)", [
        ("a", "mimo-v2.5-pro", "xiaomi", 1000, 100, 10000, 0, peak),     # 1000*300 + 100*600 + 10000*2.5 = 385000
        ("b", "mimo-v2.5", "xiaomi", 1000, 100, 0, 0, offpeak),          # (100000 + 20000) * 0.8 = 96000
        ("c", "mimo-v2.6-pro", "xiaomi", 50, 5, 0, 0, peak),             # no rate configured
        ("d", "mimo-v2.5-pro", "xiaomi", 10**9, 0, 0, 0, old),           # before this cycle
        ("e", "qwen3.5:9b", "custom", 999, 999, 0, 0, peak),             # not MiMo
    ])
    db.commit()
    db.close()

    cfg = {"plan": "Lite", "monthly": 4_100_000_000, "renews_at": "2026-08-23T23:59:59Z", "cycle_days": 30,
           "rates": {"mimo-v2.5-pro": [2.5, 300, 600], "mimo-v2.5": [2, 100, 200]}}
    now = datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp()
    out = credits.estimate(cfg, home, now)
    bar = out["bars"][0]
    assert bar["used"] == 385000 + 96000
    assert bar["resets_at"] == datetime(2026, 10, 22, 23, 59, 59, tzinfo=timezone.utc).timestamp()  # rolled forward
    assert bar["tokens"] == 11100 + 1100 + 55
    assert "mimo-v2.6-pro" in out["note"] and out["estimated"] is True
    assert credits.estimate({"monthly": 0}, home, now)["error"]


def test_pool_default_base_url_does_not_override_config(tmp_path, monkeypatch):
    """Regression (seen on the user's machine): `hermes auth add` stores the pay-as-you-go base_url with a
    Token Plan key; config.yaml points at the Token Plan endpoint and must win, or MiMo returns 401."""
    from ai_monitor.checkers.openai_compat import resolve_key
    home = tmp_path / "hermes"
    (home / "profiles" / "mimo").mkdir(parents=True)
    (home / "profiles" / "mimo" / "auth.json").write_text(json.dumps({"credential_pool": {"xiaomi": [
        {"access_token": "tp-key", "priority": 0, "base_url": "https://api.xiaomimimo.com/v1"}]}}), encoding="utf-8")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    p = Provider("m", "M", "openai_compat", options={"base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                                                     "api_key_env": "MIMO_API_KEY", "key_from": "hermes",
                                                     "_hermes_home": str(home)})
    assert resolve_key(p) == ("tp-key", "https://token-plan-sgp.xiaomimimo.com/v1", "Hermes")


def test_credit_rates_match_exact_model_only():
    from ai_monitor.checkers.credits import _rate
    rates = {"mimo-v2.6-pro": [2.5, 300, 600], "mimo-v2.6-flash": [2, 100, 200]}
    assert _rate(rates, "mimo-v2.6-pro") == [2.5, 300, 600]
    assert _rate(rates, "xiaomi/MiMo-V2.6-Pro:latest") == [2.5, 300, 600]
    assert _rate(rates, "mimo-v2.6-pro-ultraspeed") is None
    assert _rate(rates, "mimo-v2.6") is None


def test_free_tier_daily_usage_and_429_count(tmp_path):
    from datetime import datetime
    from ai_monitor.checkers import credits
    home = tmp_path / "hermes"
    home.mkdir()
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE session_model_usage (model TEXT, billing_provider TEXT, api_call_count INT, input_tokens INT,"
               " cache_read_tokens INT, cache_write_tokens INT, output_tokens INT, last_seen REAL)")
    now = datetime(2026, 9, 24, 15, 0).timestamp()  # local time
    today, yesterday = datetime(2026, 9, 24, 9, 0).timestamp(), datetime(2026, 9, 23, 22, 0).timestamp()
    db.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?)", [
        ("glm-4.5-flash", "zai", 10, 100, 50, 0, 20, today),
        ("glm-4.5-flash", "zai", 99, 9999, 0, 0, 0, yesterday),
        ("qwen3.5:9b", "custom", 5, 5, 0, 0, 5, today)])
    db.commit()
    db.close()
    d = credits.daily_usage({"match": "glm", "requests": 1000}, home, now)
    assert (d["requests"], d["tokens"], d["limits"]) == (10, 170, {"requests": 1000.0})
    assert d["resets_at"] == datetime(2026, 9, 25).timestamp()
    assert credits.daily_usage({}, home, now)["error"]

    store = Store(":memory:")
    store.add_check("glm", "degraded", 1, "โดน rate limit (HTTP 429: busy)", {})
    store.add_check("glm", "up", 1, "API ใช้ได้", {})
    assert store.count_detail("glm", 0, "%HTTP 429%") == 1


def test_hermes_sessions_include_profiles_and_running(tmp_path):
    home = tmp_path / "hermes"
    (home / "profiles" / "mimo").mkdir(parents=True)
    now = time.time()

    def make(db, rows):
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE sessions (id TEXT, source TEXT, model TEXT, started_at REAL, ended_at REAL,"
                  " last_activity_at REAL, input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT)")
        c.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()

    make(home / "state.db", [("a", "oneshot", "spark", now - 86400 * 2, now - 86400 * 2, None, 5, 5, 0, 0)])
    make(home / "profiles" / "mimo" / "state.db", [
        ("b", "cli", "mimo-v2.6-pro", now - 600, None, now - 30, 100, 20, 1000, 0),   # running today
        ("c", "cli", "mimo-v2.6-pro", now - 3600, now - 3000, now - 3000, 10, 10, 0, 0),  # finished today
        ("d", "cli", "mimo-v2.6-pro", now - 7200, None, now - 7000, 1, 1, 0, 0)])        # stale, not running
    s = hermes._sessions(home, now)
    assert (s["today"], s["tokens_today"], s["active"]) == (3, 1120 + 20 + 2, 1)
    assert (s["last_profile"], s["last_model"], s["last_source"]) == ("mimo", "mimo-v2.6-pro", "cli")
    assert s["profiles"] == ["mimo"]


def test_hermes_kanban_counts(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    home = tmp_path / "hermes"
    (home / "kanban" / "boards" / "work").mkdir(parents=True)
    now = time.time()

    def make(db, rows):
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE tasks (id TEXT, status TEXT, completed_at INTEGER)")
        c.executemany("INSERT INTO tasks VALUES (?,?,?)", rows)
        c.commit()
        c.close()

    make(home / "kanban.db", [("1", "running", None), ("2", "ready", None), ("3", "todo", None),
                              ("4", "done", int(now) - 60), ("5", "done", int(now) - 86400 * 3)])
    make(home / "kanban" / "boards" / "work" / "kanban.db", [("6", "blocked", None), ("7", "review", None)])
    assert hermes._kanban(home, now) == {"boards": 2, "running": 1, "queued": 2, "blocked": 1, "review": 1, "done_today": 1}
    assert hermes._kanban(tmp_path / "empty", now) is None


def test_ui_refresh_ms_from_config(tmp_path):
    def cfg_with(text):
        (tmp_path / "c.yaml").write_text(text, encoding="utf-8")
        return load_config(tmp_path / "c.yaml")

    assert cfg_with("providers: []\n").ui_refresh_ms == 3000
    assert cfg_with("defaults:\n  ui_refresh_ms: 2000\nproviders: []\n").ui_refresh_ms == 2000
    assert cfg_with("ui_refresh_ms: 5000\nproviders: []\n").ui_refresh_ms == 5000
    assert cfg_with("ui_refresh_ms: 10\nproviders: []\n").ui_refresh_ms == 1000  # clamped
    cfg = Config(providers=[], ui_refresh_ms=2500)
    with TestClient(create_app(cfg, store=Store(":memory:"), start=False)) as client:
        assert client.get("/api/status").json()["ui_refresh_ms"] == 2500
