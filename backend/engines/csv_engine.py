"""Engine: CSV / Excel → JSON publisher (event_new_issuance_data)."""
from __future__ import annotations

import io
import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import requests

_SENTINEL_DATE = "00-01-1900"


def _log(q: queue.Queue, level: str, msg: str) -> None:
    q.put({"type": "log", "level": level, "msg": msg})


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return
        time.sleep(min(0.2, deadline - time.monotonic()))


def _normalize_url(url: str) -> str:
    scheme, _, rest = url.partition("://")
    return f"{scheme}://{re.sub(r'/{2,}', '/', rest)}"


def _login(host_name: str, username: str, password: str, verify_ssl: bool) -> str:
    url = f"https://{host_name}/sm/event-login-auth"
    payload = {
        "MESSAGE_TYPE": "TXN_LOGIN_AUTH",
        "SERVICE_NAME": "AUTH_MANAGER",
        "DETAILS": {"USER_NAME": username, "PASSWORD": password},
    }
    headers = {"Content-Type": "application/json", "SOURCE_REF": "12345"}
    resp = requests.post(url, json=payload, headers=headers, verify=verify_ssl, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("SESSION_AUTH_TOKEN") or data.get("DETAILS", {}).get("SESSION_AUTH_TOKEN")
    if not token:
        raise RuntimeError("No SESSION_AUTH_TOKEN in auth response")
    return token


def _parse_row_range(rows_str: str, total: int) -> list:
    s = str(rows_str).strip() if rows_str else ""
    if not s:
        return list(range(1, total + 1))
    try:
        if "-" in s:
            lo, hi = s.split("-", 1)
            return list(range(max(1, int(lo.strip())), min(total, int(hi.strip())) + 1))
        n = int(s)
        return [n] if 1 <= n <= total else []
    except Exception:
        return list(range(1, total + 1))


def _build_column_groups(df: pd.DataFrame) -> Dict[str, list]:
    groups: Dict[str, list] = {}
    for col in df.columns:
        base = col.split(".")[0]
        groups.setdefault(base, []).append(col)
    return groups


def _parse_insert_time_ms(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, pd.Timestamp):
        return int(val.timestamp() * 1000)
    if isinstance(val, (int, float)):
        v = float(val)
        return int(v) if v >= 1e12 else int(v * 1000)
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        v = float(s)
        return int(v) if v >= 1e12 else int(v * 1000)
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def _convert_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, pd.Timestamp):
        return str(int(val.timestamp() * 1000))
    if isinstance(val, bool):
        return val
    if hasattr(val, "item"):
        val = val.item()
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if not np.isfinite(val):
            return None
        return str(int(val)) if val == int(val) else str(val)
    s = str(val).strip()
    if not s or s == _SENTINEL_DATE:
        return None
    # Scientific notation (e.g. 7.5E8)
    if re.fullmatch(r"-?\d+\.?\d*[eE][+\-]?\d+", s):
        try:
            f = float(s)
            return str(int(f)) if f == int(f) else str(f)
        except Exception:
            pass
    # dd-MM-YYYY date
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", s)
    if m:
        try:
            dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
            return str(int(dt.timestamp() * 1000))
        except Exception:
            pass
    # Other datetime strings
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return str(int(dt.timestamp() * 1000))
        except ValueError:
            continue
    return s


def _row_to_details(row: pd.Series, col_groups: Dict[str, list]) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    for base, cols in col_groups.items():
        for col in cols:
            converted = _convert_value(row.get(col))
            if converted is not None:
                details[base] = converted
                break
    return details


def run_csv_upload(
    params: Dict[str, Any],
    env: Dict[str, Any],
    stop_event: threading.Event,
    log_queue: queue.Queue,
) -> None:
    ok = err = 0
    try:
        host_name = env["host_name"]
        verify_ssl = env.get("verify_ssl", True)
        creds = env.get("credentials", {}).get("bonds_loans", {})
        username = creds.get("username", "")
        password = creds.get("password", "")

        file_bytes: bytes = params["file_bytes"]
        file_name: str = params.get("file_name", "upload.csv")
        rows_str: str = params.get("rows", "")
        delay_seconds: float = float(params.get("delay_seconds", 2))
        simulation: bool = str(params.get("simulation", "N")).strip().upper() == "Y"
        time_scale: float = float(params.get("time_scale", 1.0)) or 1.0
        max_sleep_raw = params.get("max_sleep_seconds")
        max_sleep: Optional[float] = float(max_sleep_raw) if max_sleep_raw is not None else None
        dry_run: bool = bool(params.get("dry_run", False))
        insert_time_col: str = params.get("insert_time_column", "INSERT_TIME")
        message_type: str = params.get("message_type", "EVENT_NEW_ISSUANCE_DATA")
        service_name: str = params.get("service_name", "ISSUANCE_EVENT_HANDLER")

        # --- Load file ---
        _log(log_queue, "info", f"Loading: {file_name}")
        buf = io.BytesIO(file_bytes)
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "csv"
        if ext in ("xlsx", "xls"):
            df = pd.read_excel(buf, dtype=str)
        else:
            df = pd.read_csv(buf, dtype=str)

        # Normalise column headers to UPPER CASE
        df.columns = [c.upper() for c in df.columns]

        total_rows = len(df)
        row_numbers = _parse_row_range(rows_str, total_rows)
        row_set = set(row_numbers)

        row_desc = f"rows {rows_str}" if str(rows_str).strip() else "all rows"
        _log(log_queue, "info",
             f"{total_rows} total rows — processing {len(row_numbers)} ({row_desc})")
        mode_parts = ["simulation (time-replay)" if simulation else f"fixed delay {delay_seconds}s"]
        if simulation:
            mode_parts.append(f"time_scale ×{time_scale}")
        if dry_run:
            mode_parts.append("DRY RUN")
        _log(log_queue, "info", "Mode: " + " · ".join(mode_parts))

        session = url = req_headers = None
        if not dry_run:
            if stop_event.is_set():
                return
            _log(log_queue, "info", "Authenticating…")
            token = _login(host_name, username, password, verify_ssl)
            _log(log_queue, "success", "Authenticated.")
            url = _normalize_url(f"https://{host_name}/gwf//event_new_issuance_data")
            session = requests.Session()
            session.verify = verify_ssl
            req_headers = {
                "Content-Type": "application/json",
                "SESSION_AUTH_TOKEN": token,
                "Accept": "*/*",
                "Cache-Control": "no-cache",
                "User-Agent": "PostmanRuntime/7.48.0",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
            }

        col_groups = _build_column_groups(df)
        prev_insert_ms: Optional[int] = None
        processed = 0

        for original_idx, (_, row) in enumerate(df.iterrows(), start=1):
            if stop_event.is_set():
                _log(log_queue, "warn", "Stopped.")
                break
            if original_idx not in row_set:
                continue

            details = _row_to_details(row, col_groups)
            payload = {
                "MESSAGE_TYPE": message_type,
                "SERVICE_NAME": service_name,
                "DETAILS": details,
            }

            if dry_run:
                preview = json.dumps(payload, separators=(",", ":"))
                if len(preview) > 400:
                    preview = preview[:400] + "…"
                _log(log_queue, "info", f"[DRY RUN] Row {original_idx}: {preview}")
                ok += 1
                processed += 1
                continue

            # --- Timing ---
            if simulation and insert_time_col in df.columns:
                curr_ms = _parse_insert_time_ms(row.get(insert_time_col))
                if curr_ms is not None and prev_insert_ms is not None:
                    delta_s = (curr_ms - prev_insert_ms) / 1000.0
                    sleep_s = delta_s / time_scale
                    if max_sleep is not None:
                        sleep_s = min(sleep_s, max_sleep)
                    if sleep_s > 0:
                        _log(log_queue, "info",
                             f"Simulation: waiting {sleep_s:.1f}s before row {original_idx}…")
                        _interruptible_sleep(sleep_s, stop_event)
                elif prev_insert_ms is not None and delay_seconds > 0:
                    # fallback when INSERT_TIME is unparseable
                    _interruptible_sleep(delay_seconds, stop_event)
                if curr_ms is not None:
                    prev_insert_ms = curr_ms
            elif delay_seconds > 0 and processed > 0:
                _interruptible_sleep(delay_seconds, stop_event)

            if stop_event.is_set():
                _log(log_queue, "warn", "Stopped.")
                break

            # --- POST ---
            _log(log_queue, "info", f"Posting row {original_idx}/{total_rows}…")
            try:
                resp = session.post(url, json=payload, headers=req_headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                errors = data.get("ERROR", [])
                if data.get("MESSAGE_TYPE") == "EVENT_NACK" or errors:
                    err_parts = [e.get("TEXT", str(e)) for e in errors] if errors else ["NACK"]
                    raise RuntimeError(" | ".join(err_parts))
                _log(log_queue, "success", f"Row {original_idx}: ACK ({resp.status_code})")
                ok += 1
            except Exception as exc:
                _log(log_queue, "error", f"Row {original_idx}: {exc}")
                err += 1

            processed += 1

        if stop_event.is_set():
            _log(log_queue, "warn", f"Run stopped — {ok} sent, {err} failed.")
        else:
            _log(log_queue, "info" if err == 0 else "warn",
                 f"Done — {ok} sent, {err} failed, {ok + err} total.")

    except Exception as exc:
        _log(log_queue, "error", f"Fatal: {exc}")
    finally:
        log_queue.put({"type": "summary", "success": ok, "failed": err, "total": ok + err})
        log_queue.put(None)
