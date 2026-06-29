"""TIG order-creation engine — posts EVENT_TIG_CREATE_ORDER events.

Mirrors the engine contract used by the other tools: it puts log/summary
dicts on `log_queue`, checks `stop_event` before each HTTP call and inside
delays, and emits a `None` sentinel when done.

Auth uses the dedicated `tig_orders` credential set and the session-token
flow (like Bonds/Loans — a single SESSION_AUTH_TOKEN, no refresh token).
"""
from __future__ import annotations

import json
import queue
import random
import string
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

DEFAULT_AUTH_PATH = "/sm/event-login-auth"
DEFAULT_ORDER_PATH = "/gwf/EVENT_TIG_CREATE_ORDER"


# ── helpers ──────────────────────────────────────────────────────────────────

def _deep_get(data: Dict[str, Any], path) -> Optional[Any]:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _gen_account_code() -> str:
    """6-char uppercase alphanumeric code, guaranteed ≥1 letter and ≥1 digit."""
    letters = string.ascii_uppercase
    digits = string.digits
    pool = letters + digits
    chars = [random.choice(letters), random.choice(digits)]
    chars += [random.choice(pool) for _ in range(4)]
    random.shuffle(chars)
    return "".join(chars)


def _aligned_quantity(minimum: int, maximum: int,
                      min_piece: Optional[int], increment: Optional[int]) -> int:
    """Random quantity in [minimum, maximum]. When an increment is given, snap
    to a multiple of `increment` that is also ≥ `min_piece`."""
    lo = max(0, int(minimum))
    hi = max(lo, int(maximum))
    if not increment or increment <= 0:
        return random.randint(lo, hi)
    floor = max(lo, int(min_piece or 0))
    start_n = (floor + increment - 1) // increment   # smallest multiple ≥ floor
    end_n = hi // increment                           # largest multiple ≤ hi
    if end_n < start_n:
        # No aligned value fits the window — fall back to the floor multiple.
        return start_n * increment
    return random.randint(start_n, end_n) * increment


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_event.is_set():
            return False
        time.sleep(min(0.1, end - time.monotonic()))
    return True


# ── main engine entry point ───────────────────────────────────────────────────

def run_tig_orders(params: Dict[str, Any], env: Dict[str, Any],
                   stop_event: threading.Event, log_queue: queue.Queue) -> None:
    try:
        _run(params, env, stop_event, log_queue)
    except Exception as exc:
        log_queue.put({"type": "log", "level": "error", "msg": f"Fatal error: {exc}"})
    finally:
        log_queue.put(None)


def _run(params: Dict[str, Any], env: Dict[str, Any],
         stop_event: threading.Event, log_queue: queue.Queue) -> None:

    def log(msg: str, level: str = "info") -> None:
        log_queue.put({"type": "log", "level": level, "msg": msg})

    host = env.get("host_name", "")
    verify_ssl = env.get("verify_ssl", True)
    creds = env.get("credentials", {}).get("tig_orders", {})
    username = creds.get("username", "")
    password = creds.get("password", "")
    dry_run = bool(params.get("dry_run", False))

    # ── Run parameters ────────────────────────────────────────────────────────
    tranche_id = (params.get("tranche_id") or "").strip()
    region_entity = (params.get("region_entity") or "AMFR").strip() or "AMFR"
    market_type = (params.get("market_type") or "Market").strip() or "Market"
    registration_type = (params.get("registration_type") or "").strip()
    trade_desk = (params.get("trade_desk") or "").strip()
    portfolio_manager = (params.get("portfolio_manager") or "").strip()
    count = int(params.get("count", 1))
    delay_seconds = float(params.get("delay_seconds", 1))

    qr = params.get("quantity_ranges", {}) or {}
    qty_min = int(qr.get("min", 100000))
    qty_max = int(qr.get("max", 1000000))
    sz = params.get("sizing", {}) or {}
    min_piece = int(sz["min_piece"]) if sz.get("min_piece") is not None else None
    increment = int(sz["increment"]) if sz.get("increment") is not None else None

    if not tranche_id:
        log("TRANCHE_ID is empty — orders will be posted without a tranche ID.", "warn")

    # ── Auth (session token only — like Bonds/Loans) ──────────────────────────
    token = None
    if dry_run:
        log("DRY RUN — generating payloads only, nothing will be sent.", "warn")
    else:
        auth_url = f"https://{host}{DEFAULT_AUTH_PATH}"
        log(f"Authenticating as {username} ...")
        try:
            auth_resp = requests.post(
                auth_url,
                headers={"Content-Type": "application/json", "SOURCE_REF": "12345"},
                json={"MESSAGE_TYPE": "TXN_LOGIN_AUTH", "SERVICE_NAME": "AUTH_MANAGER",
                      "DETAILS": {"USER_NAME": username, "PASSWORD": password}},
                timeout=30, verify=verify_ssl,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Auth connection error: {exc}")

        if auth_resp.status_code >= 400:
            raise RuntimeError(f"Auth failed (HTTP {auth_resp.status_code}): {auth_resp.text[:300]}")

        try:
            aj = auth_resp.json()
            token = aj.get("SESSION_AUTH_TOKEN") or aj.get("DETAILS", {}).get("SESSION_AUTH_TOKEN")
        except Exception:
            token = None
        if not token:
            raise RuntimeError("No SESSION_AUTH_TOKEN in auth response")

        log("Authenticated.")

    if stop_event.is_set():
        log("Stopped.", "warn")
        return

    # ── Build orders ──────────────────────────────────────────────────────────
    def build_order() -> Dict[str, Any]:
        return {
            "DETAILS": {
                "ACCOUNT_CODE": _gen_account_code(),
                "TRANCHE_ID": tranche_id,
                "REGION_ENTITY": region_entity,
                "MARKET_TYPE": market_type,
                "ORDER_STATUS_FIELD": "",
                "PORTFOLIO_MANAGER": portfolio_manager,
                "QUANTITY": _aligned_quantity(qty_min, qty_max, min_piece, increment),
                "SECURITY_ID": None,
                "REGISTRATION_TYPE": registration_type,
                "REGULATION_SUBCATEGORY": None,
                "TRADE_DESK": trade_desk,
            }
        }

    # ── Post orders ───────────────────────────────────────────────────────────
    order_url = f"https://{host}{DEFAULT_ORDER_PATH}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "User-Agent": "PostmanRuntime/7.48.0",
        "SOURCE_REF": str(uuid.uuid4()),
        "SESSION_AUTH_TOKEN": token,
        "Connection": "keep-alive",
    }

    total = max(0, count)
    success = failures = 0
    sess = requests.Session()

    for idx in range(1, total + 1):
        if stop_event.is_set():
            log(f"Stopped at [{idx}/{total}]", "warn")
            break

        order = build_order()
        d = order["DETAILS"]

        if dry_run:
            success += 1
            log(f"[{idx}/{total}] DRY RUN  ACCOUNT={d['ACCOUNT_CODE']}  "
                f"QTY={d['QUANTITY']:,}  MARKET_TYPE={d['MARKET_TYPE']}")
            log(json.dumps(order, indent=2))
            continue

        try:
            resp = sess.post(order_url, headers=headers, json=order,
                             timeout=30, verify=verify_ssl)
        except requests.RequestException as exc:
            failures += 1
            log(f"[{idx}/{total}] Connection error: {exc}", "error")
        else:
            if 200 <= resp.status_code < 300:
                try:
                    rj = resp.json()
                except Exception:
                    rj = {}
                msg_type = str(rj.get("MESSAGE_TYPE", "")).upper()
                if "NACK" in msg_type:
                    failures += 1
                    reason = (_deep_get(rj, ["DETAILS", "TEXT"])
                              or _deep_get(rj, ["DETAILS", "ERROR"])
                              or resp.text[:200])
                    log(f"[{idx}/{total}] NACK — {reason}", "error")
                else:
                    success += 1
                    log(f"[{idx}/{total}] OK  ACCOUNT={d['ACCOUNT_CODE']}  "
                        f"QTY={d['QUANTITY']:,}  MARKET_TYPE={d['MARKET_TYPE']}", "success")
            else:
                failures += 1
                log(f"[{idx}/{total}] HTTP {resp.status_code} — {resp.text[:200]}", "error")

        if idx < total and not stop_event.is_set():
            if not _interruptible_sleep(delay_seconds, stop_event):
                log("Stopped during delay.", "warn")
                break

    log(f"Done — Success={success}  Failed={failures}  Total={total}",
        "success" if failures == 0 else "warn")
    log_queue.put({"type": "summary", "success": success, "failed": failures, "total": total})
