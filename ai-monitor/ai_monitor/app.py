import asyncio
import contextlib
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .checkers import hermes, ollama
from .config import Config
from .engine import Monitor
from .router_status import read_router
from .store import Store

WEB = Path(__file__).parent / "web"


def create_app(cfg: Config, store: Store | None = None, start: bool = True,
               router_reader: Callable[[], dict] | None = None) -> FastAPI:
    store = store or Store(cfg.db_path)
    if router_reader is None:
        hermes_p = next((p for p in cfg.providers if p.type == "hermes"), None)
        home = hermes.resolve_home(hermes_p.options if hermes_p else {})
        router_reader = lambda: read_router(cfg.router, home)  # noqa: E731
    monitor = Monitor(cfg, store)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(monitor.run()) if start else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="AI Monitor", lifespan=lifespan)
    app.state.monitor = monitor

    @app.get("/api/status")
    def status() -> dict:
        day_ago = time.time() - 86400
        providers = []
        for p in cfg.providers:
            state = monitor.state.get(p.id) or {"status": "unknown", "detail": "กำลังเช็ค…", "extra": {}}
            providers.append({
                "id": p.id, "name": p.name, "group": p.group, "type": p.type, "interval_s": p.interval_s, **state,
                "history": store.latencies(p.id, extra_key="temp" if p.type == "gpu" else None),
                "uptime_24h": store.uptime_pct(p.id, day_ago),
            })
        names = {p.id: p.name for p in cfg.providers}
        events = [{**e, "name": names.get(e["provider"], e["provider"])} for e in store.recent_events()]
        return {"generated_at": time.time(), "providers": providers, "events": events}

    @app.get("/api/router")
    async def router() -> dict:
        return await asyncio.to_thread(router_reader)

    @app.post("/api/bench/{provider_id}")
    async def bench(provider_id: str) -> dict:
        p = next((p for p in cfg.providers if p.id == provider_id and p.type == "ollama"), None)
        if p is None:
            raise HTTPException(404, "not a local model")
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                result = await ollama.benchmark(p, client)
            except httpx.ConnectError:
                return {"error": "เชื่อมต่อ Ollama ไม่ได้"}
            except httpx.TimeoutException:
                return {"error": "Ollama ไม่ตอบภายในเวลาที่กำหนด"}
            except (httpx.HTTPError, ValueError) as exc:
                return {"error": str(exc) or type(exc).__name__}
            await monitor.check_once(p, client)  # refresh the card right away
        return result

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def build_app(cfg: Config, demo: bool = False) -> FastAPI:
    """The app with real checks, or with sample data and no checks when `demo`."""
    if not demo:
        return create_app(cfg)
    from .demo import router_sample, seed

    app = create_app(cfg, store=Store(":memory:"), start=False, router_reader=router_sample)
    seed(app.state.monitor)
    return app
