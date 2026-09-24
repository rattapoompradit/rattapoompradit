"""Read-only view of the Hermes Router's latest routing decision (monitoring only, never changes routing).

Where the decision comes from, first match wins (config.yaml `router:` section):
  file:     JSON file, or JSON-lines log (the last line is the latest decision)
  log:      plain-text log + `pattern`, a regex with named groups route / model / reason / status (last match wins)
  url:      HTTP endpoint returning the decision as JSON
  auto_discover (default true): router*.json / router*.jsonl in the Hermes home and its logs/ folder

Field names are looked up from common spellings (see FIELDS) at the top level or inside a nested
"decision" / "routing" / "router" object; `fields:` overrides them with dotted paths, e.g. {model: "target.name"}.
Nothing is inferred: a value the source does not contain is reported as missing, and no source means N/A.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

FIELDS = {
    "route": ("route", "route_type", "routeType", "category", "intent", "type"),
    "model": ("model", "selected_model", "selectedModel", "target_model", "model_name"),
    "reason": ("reason", "why", "explanation", "rationale"),
    "status": ("status", "state", "phase"),
    "at": ("ts", "timestamp", "time", "at", "updated_at", "created_at"),
}
NESTED = ("decision", "routing", "router")
STATUS = {
    "IDLE": ("idle", "done", "completed", "complete", "finished", "success", "ok"),
    "ROUTING": ("routing", "classifying", "deciding"),
    "RUNNING": ("running", "in_progress", "generating", "busy", "executing", "streaming"),
    "ERROR": ("error", "failed", "failure", "exception"),
}
AUTO_PATTERNS = ("router*.jsonl", "router*.json", "logs/router*.jsonl", "logs/router*.json")
TAIL_BYTES = 65536


def _dig(record: dict, path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _lookup(record: dict, field: str, overrides: dict) -> Any:
    if field in overrides:
        return _dig(record, overrides[field])
    scopes = [record] + [record[k] for k in NESTED if isinstance(record.get(k), dict)]
    for scope in scopes:
        for key in FIELDS[field]:
            if scope.get(key) not in (None, ""):
                return scope[key]
    return None


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else float(value)  # accept milliseconds
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize_status(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    for name, spellings in STATUS.items():
        if text == name.lower() or text in spellings:
            return name
    return str(value).strip().upper()  # unknown status: shown as the router wrote it


def _decision(record: dict, overrides: dict) -> dict | None:
    values = {f: _lookup(record, f, overrides) for f in FIELDS}
    if values["route"] is None and values["model"] is None:
        return None  # not a routing record
    return {
        "route": str(values["route"]).strip().upper() if values["route"] is not None else None,
        "model": str(values["model"]).strip() if values["model"] is not None else None,
        "reason": str(values["reason"]).strip() if values["reason"] is not None else None,
        "status": normalize_status(values["status"]),
        "at": _epoch(values["at"]),
    }


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL_BYTES))
        return f.read().decode("utf-8", "replace")


def _from_json_file(path: Path, overrides: dict) -> dict | None:
    text = _tail(path)
    try:
        whole = json.loads(text)
        records = whole if isinstance(whole, list) else [whole]
    except ValueError:
        records = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    for record in reversed(records):
        if isinstance(record, dict) and (d := _decision(record, overrides)):
            return d
    return None


def _from_log(path: Path, pattern: str) -> dict | None:
    regex = re.compile(pattern)
    for line in reversed(_tail(path).splitlines()):
        if m := regex.search(line):
            groups = {k: v for k, v in m.groupdict().items() if v is not None}
            record = {k: groups.get(k) for k in ("route", "model", "reason", "status")}
            record["at"] = groups.get("at") or groups.get("ts")
            return _decision(record, {})
    return None


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(str(path))).expanduser()


def read_router(options: dict, hermes_home: Path | None) -> dict:
    """Latest routing decision as {"available": True, route, model, reason, status, at, source}
    or {"available": False, "note": why}."""
    overrides = options.get("fields") or {}
    try:
        if file := options.get("file"):
            path = _expand(file)
            if not path.is_file():
                return {"available": False, "note": f"ไม่พบไฟล์ {path}"}
            d = _from_json_file(path, overrides)
            return _result(d, str(path), path.stat().st_mtime)
        if log := options.get("log"):
            path = _expand(log)
            if not options.get("pattern"):
                return {"available": False, "note": "ตั้ง router.pattern (regex) สำหรับอ่าน log ด้วย"}
            if not path.is_file():
                return {"available": False, "note": f"ไม่พบไฟล์ {path}"}
            return _result(_from_log(path, options["pattern"]), str(path), path.stat().st_mtime)
        if url := options.get("url"):
            r = httpx.get(url, timeout=2, trust_env=False)
            r.raise_for_status()
            data = r.json()
            return _result(_decision(data, overrides) if isinstance(data, dict) else None, url, None)
        if options.get("auto_discover", True) and hermes_home and hermes_home.is_dir():
            files = sorted({f for pat in AUTO_PATTERNS for f in hermes_home.glob(pat) if f.is_file()},
                           key=lambda f: f.stat().st_mtime, reverse=True)
            for path in files:
                if d := _from_json_file(path, overrides):
                    return _result(d, str(path), path.stat().st_mtime)
    except (OSError, re.error, httpx.HTTPError, ValueError) as exc:
        return {"available": False, "note": f"อ่านข้อมูล router ไม่ได้: {type(exc).__name__}"}
    return {"available": False, "note": "ยังไม่มีข้อมูล routing จาก Hermes (ตั้งค่า router: ใน config.yaml)"}


def _result(decision: dict | None, source: str, mtime: float | None) -> dict:
    if not decision:
        return {"available": False, "note": "ยังไม่พบการตัดสินใจของ router ในแหล่งข้อมูล", "source": source}
    if decision["at"] is None:
        decision["at"] = mtime
    return {"available": True, "source": source, "read_at": time.time(), **decision}
