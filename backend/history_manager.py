"""Persistent run-history store.

Runs are appended (newest first) to ``history.json`` next to this module.
The frontend POSTs a completed-run record when an SSE stream ends; the
History tab reads them back via ``GET /api/history``.

Kept deliberately simple: a flat JSON list, capped by count and age so the
file never grows without bound. Test-env tool, single writer at a time.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from paths import STATE_DIR

HISTORY_PATH = STATE_DIR / "history.json"

# Retention: keep at most this many records, and drop anything older than
# this many days. The UI advertises "last 30 days".
_MAX_RECORDS = 500
_MAX_AGE_DAYS = 30

_lock = threading.Lock()

# Fields the client is allowed to set; everything else is ignored.
_ALLOWED = {
    "ts", "tool", "env", "params_summary", "params_raw",
    "ok", "fail", "total", "status", "dur_seconds",
}
_VALID_STATUS = {"done", "failed", "stopped"}


def _load() -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(records: List[Dict[str, Any]]) -> None:
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def _prune(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)
    kept: List[Dict[str, Any]] = []
    for r in records:
        ts = r.get("ts")
        try:
            when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < cutoff:
                continue
        except (ValueError, TypeError):
            pass  # keep records with an unparseable timestamp
        kept.append(r)
    return kept[:_MAX_RECORDS]


def list_runs() -> List[Dict[str, Any]]:
    """Return all retained runs, newest first."""
    with _lock:
        records = _prune(_load())
        return records


def add_run(record: Dict[str, Any]) -> Dict[str, Any]:
    """Append a completed-run record and return it (with assigned id)."""
    clean = {k: record.get(k) for k in _ALLOWED if k in record}

    if clean.get("status") not in _VALID_STATUS:
        clean["status"] = "done"
    clean.setdefault("ts", datetime.now(timezone.utc).isoformat())
    for num in ("ok", "fail", "total"):
        try:
            clean[num] = int(clean.get(num, 0) or 0)
        except (ValueError, TypeError):
            clean[num] = 0
    clean["id"] = uuid.uuid4().hex[:8]

    with _lock:
        records = [clean] + _load()
        records = _prune(records)
        _save(records)
    return clean
