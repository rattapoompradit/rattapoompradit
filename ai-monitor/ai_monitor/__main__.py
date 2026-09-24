import argparse
import asyncio
import logging
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config, load_env
from .demo import seed
from .engine import check_all
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Monitor dashboard")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--demo", action="store_true", help="show sample data instead of real checks")
    parser.add_argument("--check", action="store_true", help="check every provider once, print the result and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env(args.config.parent / ".env")
    cfg = load_config(args.config)
    if args.check:
        sys.stdout.reconfigure(errors="replace")
        icons = {"up": "OK  ", "degraded": "WARN", "down": "DOWN", "unknown": "--  "}
        for p, r in asyncio.run(check_all(cfg)):
            latency = f" ({r.latency_ms} ms)" if r.latency_ms is not None else ""
            print(f"[{icons.get(r.status, r.status)}] {p.name:<16} {r.detail}{latency}")
        return
    if args.demo:
        app = create_app(cfg, store=Store(":memory:"), start=False)
        seed(app.state.monitor)
    else:
        app = create_app(cfg)
    print(f"AI Monitor: http://{cfg.host}:{cfg.port}")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
