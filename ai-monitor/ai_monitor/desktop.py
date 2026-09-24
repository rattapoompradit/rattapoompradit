"""Desktop app (AI-Monitor.exe): runs the monitor in the background and shows the dashboard in its own
full-screen window on the Xeneon Edge (the first 32:9 screen), using Windows' built-in WebView2.

config.yaml, .env, the database and ai-monitor.log live next to the exe; defaults are created on first run.
Flags: --demo (sample data)  --windowed (normal window)  --config PATH  --selftest (start, check the API, exit)
"""

import argparse
import shutil
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from .app import build_app
from .config import load_config, load_env


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def bundled(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / name


def ensure_files(base: Path) -> None:
    for target, source in (("config.yaml", "config.yaml"), (".env", ".env.example")):
        if not (base / target).exists() and bundled(source).is_file():
            shutil.copyfile(bundled(source), base / target)


def pick_screen(screens: list):
    """The ultra-wide (>= 3:1) screen, else the first one."""
    wide = [s for s in screens if s.height and s.width / s.height >= 3]
    return wide[0] if wide else (screens[0] if screens else None)


def server_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/status", timeout=1, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def start_server(app, host: str, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    return server


def selftest(url: str, base: Path) -> int:
    lines, ok = [], True
    for path in ("/api/status", "/", "/static/app.js", "/static/app.css"):
        try:
            code = httpx.get(url + path, timeout=5, trust_env=False).status_code
        except httpx.HTTPError as exc:
            code = type(exc).__name__
        ok = ok and code == 200
        lines.append(f"{path} {code}")
    (base / "selftest.txt").write_text(("OK" if ok else "FAIL") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Monitor desktop app")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    base = app_dir()
    if sys.stdout is None or sys.stderr is None:  # windowed exe has no console
        log = open(base / "ai-monitor.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    ensure_files(base)
    config_path = args.config or base / "config.yaml"
    load_env(config_path.parent / ".env")
    cfg = load_config(config_path)
    url = f"http://{cfg.host}:{cfg.port}"

    if not server_running(url):  # reuse a monitor that is already running (e.g. from run.bat)
        start_server(build_app(cfg, args.demo or args.selftest), cfg.host, cfg.port)
    if args.selftest:
        sys.exit(selftest(url, base))

    import webview  # imported late so --selftest works without a GUI

    screen = pick_screen(list(webview.screens))
    webview.create_window(
        "AI Monitor", url, screen=screen, background_color="#03050C",
        fullscreen=not args.windowed, frameless=not args.windowed,
        width=1600 if args.windowed else (screen.width if screen else 2560),
        height=500 if args.windowed else (screen.height if screen else 720),
    )
    webview.start()


if __name__ == "__main__":
    main()
