"""Interest capture engine — adapted from capture_interest.py."""
from __future__ import annotations

import json
import queue
import random
import string
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from engines.url_utils import join_url, normalize_host

DEFAULT_AUTH_PATH = "/sm/event-login-auth"
DEFAULT_INTEREST_PATH = "/gwf//event-interest-capture"

_TIMEOUT = object()  # sentinel for queue timeout


# ── helpers ─────────────────────────────────────────────────────────────────

def _deep_get(data: Dict[str, Any], path: Sequence[str]) -> Optional[Any]:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


# Moved to engines/url_utils.py on 2026-09-24 so the other six engines could use it
# too — this engine was the only one that ever honoured a scheme in host_name.
_normalize_host = normalize_host
_join_url = join_url


@dataclass
class QuantityRanges:
    true_min: int
    true_max: int
    false_min: int
    false_max: int


@dataclass
class SizingRules:
    min_piece: Optional[int] = None
    increment_size: Optional[int] = None


def _random_alpha_code() -> str:
    return "".join(random.choices(string.ascii_uppercase, k=random.choice([3, 4])))


def _random_numeric_code() -> str:
    return "".join(random.choices(string.digits, k=4))


def _random_strategy_code() -> str:
    return "".join(random.choices(string.ascii_uppercase, k=random.randint(4, 6)))


def _jittered(base: int, minimum: int, maximum: int, pct: float) -> int:
    spread = max(0.0, pct) / 100.0
    raw = int(round(base * (1 + random.uniform(-spread, spread))))
    return max(minimum, min(maximum, raw))


def _quantize(target: int, minimum: int, maximum: int, sizing: Optional[SizingRules]) -> int:
    clamped = max(minimum, min(maximum, int(target)))
    if not sizing or sizing.min_piece is None or sizing.increment_size is None:
        return clamped
    mp, inc = sizing.min_piece, sizing.increment_size
    if mp <= 0 or inc <= 0:
        return clamped
    min_n = (mp + inc - 1) // inc
    grid_min = min_n * inc
    effective_min = max(minimum, grid_min)
    last_valid = (maximum // inc) * inc
    if last_valid < effective_min:
        raise ValueError(
            f"No valid quantity: range=[{minimum},{maximum}] min_piece={mp} increment={inc}"
        )
    nearest_n = round(clamped / inc)
    aligned = nearest_n * inc
    return max(effective_min, min(last_valid, aligned))


def _allocate_true_counts(true_count: int, false_count: int) -> List[int]:
    if false_count <= 0:
        return []
    allocs = [0] * false_count
    if true_count <= 0:
        return allocs
    if true_count >= false_count:
        for i in range(false_count):
            allocs[i] = 1
        for _ in range(true_count - false_count):
            allocs[random.randrange(false_count)] += 1
    else:
        for idx in random.sample(range(false_count), true_count):
            allocs[idx] = 1
    return allocs


def _rebalance(quantities: List[int], target: int, minimum: int, maximum: int,
               sizing: Optional[SizingRules]) -> List[int]:
    if not quantities:
        return quantities
    step = sizing.increment_size if sizing and sizing.increment_size else 1000
    adjusted = list(quantities)
    for _ in range(4):
        total = sum(adjusted)
        if total <= 0:
            break
        scale = target / total
        adjusted = [_quantize(int(round(v * scale)), minimum, maximum, sizing) for v in adjusted]
        diff = target - sum(adjusted)
        if abs(diff) < step:
            break
        direction = 1 if diff > 0 else -1
        safety = len(adjusted) * 30
        ptr = 0
        while abs(diff) >= step and safety > 0:
            idx = ptr % len(adjusted)
            ptr += 1
            safety -= 1
            candidate = _quantize(adjusted[idx] + direction * step, minimum, maximum, sizing)
            delta = candidate - adjusted[idx]
            if direction > 0 and delta <= 0:
                continue
            if direction < 0 and delta >= 0:
                continue
            adjusted[idx] = candidate
            diff -= delta
    return adjusted


def _generate_quantities(
    true_count: int, false_count: int, ranges: QuantityRanges,
    closeness_pct: float, sizing: Optional[SizingRules], mismatch_pct: float
) -> Tuple[List[int], List[int], List[int]]:
    if true_count < 0 or false_count < 0:
        raise ValueError("Counts cannot be negative")
    if true_count == 0 and false_count == 0:
        return [], [], []

    false_qtys: List[int] = []
    for _ in range(false_count):
        raw = random.randint(ranges.false_min, ranges.false_max)
        false_qtys.append(_quantize(raw, ranges.false_min, ranges.false_max, sizing))

    if false_count == 0:
        return [_quantize(random.randint(ranges.true_min, ranges.true_max),
                          ranges.true_min, ranges.true_max, sizing)
                for _ in range(true_count)], [], []

    allocs = _allocate_true_counts(true_count, false_count)
    true_qtys: List[int] = []
    for fq, slots in zip(false_qtys, allocs):
        if slots <= 0:
            continue
        if slots == 1:
            parts = [fq]
        else:
            ws = [random.uniform(0.7, 1.3) for _ in range(slots)]
            ws_total = sum(ws)
            parts = [int(round(fq * (w / ws_total))) for w in ws]
        for part in parts:
            j = _jittered(part, ranges.true_min, ranges.true_max, closeness_pct)
            true_qtys.append(_quantize(j, ranges.true_min, ranges.true_max, sizing))

    while len(true_qtys) < true_count:
        raw = random.randint(ranges.true_min, ranges.true_max)
        true_qtys.append(_quantize(raw, ranges.true_min, ranges.true_max, sizing))
    if len(true_qtys) > true_count:
        true_qtys = true_qtys[:true_count]

    false_total = sum(false_qtys)
    ratio = max(0.0, mismatch_pct) / 100.0
    factor = random.uniform(1 - ratio, 1 + ratio)
    target = int(round(false_total * factor))

    min_t = _quantize(ranges.true_min, ranges.true_min, ranges.true_max, sizing)
    max_t = _quantize(ranges.true_max, ranges.true_min, ranges.true_max, sizing)
    target = max(min_t * true_count, min(max_t * true_count, target))

    true_qtys = _rebalance(true_qtys, target, ranges.true_min, ranges.true_max, sizing)
    return true_qtys, false_qtys, allocs


def _is_modelled_value(flag: bool, as_string: bool) -> Any:
    return ("True" if flag else "False") if as_string else flag


def _build_events(tranche_name, defaults, true_count, false_count, ranges,
                  closeness_pct, modelled_as_string, sizing, mismatch_pct, username) -> List[Dict]:
    true_qtys, false_qtys, allocs = _generate_quantities(
        true_count, false_count, ranges, closeness_pct, sizing, mismatch_pct
    )
    true_events = [{"DETAILS": {**defaults, "TRANCHE_NAME": tranche_name,
                                "QUANTITY": q, "ACCOUNT_CODE": _random_numeric_code(),
                                "IS_MODELLED": _is_modelled_value(True, modelled_as_string),
                                "USER_NAME": username}}
                   for q in true_qtys]
    false_events = [{"DETAILS": {**defaults, "TRANCHE_NAME": tranche_name,
                                 "QUANTITY": q, "ACCOUNT_CODE": _random_alpha_code(),
                                 "IS_MODELLED": _is_modelled_value(False, modelled_as_string),
                                 "USER_NAME": username}}
                    for q in false_qtys]

    events: List[Dict] = []
    true_idx = 0
    for fi, fe in enumerate(false_events):
        events.append(fe)
        for _ in range(allocs[fi] if fi < len(allocs) else 0):
            if true_idx < len(true_events):
                events.append(true_events[true_idx])
                true_idx += 1
    while true_idx < len(true_events):
        events.append(true_events[true_idx])
        true_idx += 1
    return events


def _build_strategy_events(tranche_name, defaults, strategy_count, accounts_count,
                            ranges, closeness_pct, modelled_as_string, sizing, username) -> List[Dict]:
    allocs = _allocate_true_counts(accounts_count, strategy_count)
    events: List[Dict] = []
    for si in range(strategy_count):
        code = _random_strategy_code()
        sq = _quantize(random.randint(ranges.false_min, ranges.false_max),
                       ranges.false_min, ranges.false_max, sizing)
        events.append({"DETAILS": {**defaults, "TRANCHE_NAME": tranche_name,
                                   "QUANTITY": sq, "ACCOUNT_CODE": None,
                                   "IS_MODELLED": _is_modelled_value(False, modelled_as_string),
                                   "STRATEGY": code, "USER_NAME": username}})
        n = allocs[si]
        if n <= 0:
            continue
        target = _jittered(sq, ranges.true_min * n, ranges.true_max * n, closeness_pct)
        init = [_quantize(random.randint(ranges.true_min, ranges.true_max),
                          ranges.true_min, ranges.true_max, sizing) for _ in range(n)]
        for q in _rebalance(init, target, ranges.true_min, ranges.true_max, sizing):
            events.append({"DETAILS": {**defaults, "TRANCHE_NAME": tranche_name,
                                       "QUANTITY": q, "ACCOUNT_CODE": _random_numeric_code(),
                                       "IS_MODELLED": _is_modelled_value(True, modelled_as_string),
                                       "STRATEGY": code, "USER_NAME": username}})
    return events


# ── main engine entry point ──────────────────────────────────────────────────

def run_interest(params: Dict[str, Any], env: Dict[str, Any],
                 stop_event: threading.Event, log_queue: queue.Queue) -> None:
    try:
        _run(params, env, stop_event, log_queue)
    except Exception as exc:
        log_queue.put({"type": "log", "level": "error", "msg": f"Fatal error: {exc}"})
    finally:
        log_queue.put(None)


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_event.is_set():
            return False
        time.sleep(min(0.1, end - time.monotonic()))
    return True


def _run(params: Dict[str, Any], env: Dict[str, Any],
         stop_event: threading.Event, log_queue: queue.Queue) -> None:

    def log(msg: str, level: str = "info") -> None:
        log_queue.put({"type": "log", "level": level, "msg": msg})

    host = env.get("host_name", "")
    verify_ssl = env.get("verify_ssl", True)
    creds = env.get("credentials", {}).get("interest", {})
    username = creds.get("username", "")
    password = creds.get("password", "")
    dry_run = bool(params.get("dry_run", False))

    # ── Auth ────────────────────────────────────────────────────────────────
    session_token = refresh_token = None
    if dry_run:
        log("DRY RUN — generating events only, nothing will be sent.", "warn")
    else:
        auth_url = _join_url(host, DEFAULT_AUTH_PATH)
        log(f"Authenticating as {username} ...")
        try:
            auth_resp = requests.post(
                auth_url,
                headers={"Content-Type": "application/json", "SOURCE_REF": "12345",
                         "USER_NAME": username, "PASSWORD": password},
                json={"MESSAGE_TYPE": "TXN_LOGIN_AUTH", "SERVICE_NAME": "AUTH_MANAGER",
                      "DETAILS": {"USER_NAME": username, "PASSWORD": password}},
                timeout=30,
                verify=verify_ssl,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Auth connection error: {exc}")

        if auth_resp.status_code >= 400:
            raise RuntimeError(f"Auth failed (HTTP {auth_resp.status_code}): {auth_resp.text[:300]}")

        try:
            auth_json = auth_resp.json()
        except Exception:
            auth_json = {}

        def _extract(paths):
            for path in paths:
                v = _deep_get(auth_json, path)
                if v:
                    return str(v)
            return None

        session_token = _extract([["DETAILS", "SESSION_AUTH_TOKEN"], ["SESSION_AUTH_TOKEN"], ["sessionAuthToken"]])
        refresh_token = _extract([["DETAILS", "REFRESH_AUTH_TOKEN"], ["REFRESH_AUTH_TOKEN"], ["refreshAuthToken"]])

        if not session_token or not refresh_token:
            raise RuntimeError(f"Could not extract tokens from auth response: {auth_json}")

        log("Authenticated. Tokens acquired.")

    if stop_event.is_set():
        log("Stopped.", "warn")
        return

    # ── Build events ────────────────────────────────────────────────────────
    tranche_name = params.get("tranche_name", "")
    true_count = int(params.get("is_modelled_true", 9))
    false_count = int(params.get("is_modelled_false", 5))
    closeness_pct = float(params.get("pair_closeness_pct", 2.0))
    mismatch_pct = float(params.get("total_mismatch_pct", 0.0))
    delay_seconds = float(params.get("delay_seconds", 4))
    is_strategy = bool(params.get("strategy", False))
    modelled_as_string = bool(params.get("is_modelled_as_string", True))

    qr = params.get("quantity_ranges", {})
    ranges = QuantityRanges(
        true_min=int(qr.get("true_min", 100000)),
        true_max=int(qr.get("true_max", 1200000)),
        false_min=int(qr.get("false_min", 600000)),
        false_max=int(qr.get("false_max", 1600000)),
    )
    sz = params.get("sizing", {})
    sizing: Optional[SizingRules] = None
    if sz:
        sizing = SizingRules(
            min_piece=int(sz["min_piece"]) if sz.get("min_piece") is not None else None,
            increment_size=int(sz["increment_size"]) if sz.get("increment_size") is not None else None,
        )

    defaults = dict(params.get("event_defaults", {
        "MARKET_TYPE": "Market", "ORDER_LIMIT": None, "TRADE_DESK": None,
        "ENTITY": "TRPA", "COMMENT": "V15 Or vs St",
        "REGISTRATION_TYPE": None, "REGULATION_SUBCATEGORY": None,
    }))

    if is_strategy:
        strategy_count = int(params.get("strategy_count", false_count))
        accounts_count = int(params.get("accounts_count", true_count))
        events = _build_strategy_events(
            tranche_name, defaults, strategy_count, accounts_count,
            ranges, closeness_pct, modelled_as_string, sizing, username
        )
    else:
        events = _build_events(
            tranche_name, defaults, true_count, false_count, ranges,
            closeness_pct, modelled_as_string, sizing, mismatch_pct, username
        )

    # ── Summary of planned totals ────────────────────────────────────────────
    planned_true = sum(e["DETAILS"]["QUANTITY"] for e in events
                       if str(e["DETAILS"].get("IS_MODELLED", "")).lower() == "true")
    planned_false = sum(e["DETAILS"]["QUANTITY"] for e in events
                        if str(e["DETAILS"].get("IS_MODELLED", "")).lower() == "false")
    log(f"Planned | IS_MODELLED=False={planned_false:,}  IS_MODELLED=True={planned_true:,}  "
        f"DELTA={planned_true - planned_false:+,}")

    # ── Post events ─────────────────────────────────────────────────────────
    interest_url = _join_url(host, DEFAULT_INTEREST_PATH)
    interest_headers = {
        "Content-Type": "application/json",
        "SOURCE_REF": str(uuid.uuid4()),
        "USER_NAME": username,
        "SESSION_AUTH_TOKEN": session_token,
        "REFRESH_AUTH_TOKEN": refresh_token,
    }

    total = len(events)
    success = failures = 0

    for idx, event in enumerate(events, start=1):
        if stop_event.is_set():
            log(f"Stopped at [{idx}/{total}]", "warn")
            break
        if dry_run:
            success += 1
            d = event["DETAILS"]
            strat = f" STRATEGY={d['STRATEGY']}" if "STRATEGY" in d else ""
            log(f"[{idx}/{total}] DRY RUN  IS_MODELLED={d['IS_MODELLED']}  "
                f"ACCOUNT={d['ACCOUNT_CODE']}  QTY={d['QUANTITY']:,}{strat}")
            log(json.dumps(event, indent=2))
            continue
        try:
            resp = requests.post(interest_url, headers=interest_headers,
                                 json=event, timeout=30, verify=verify_ssl)
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
                    reason = _deep_get(rj, ["DETAILS", "TEXT"]) or resp.text[:200]
                    log(f"[{idx}/{total}] NACK — {reason}", "error")
                else:
                    success += 1
                    d = event["DETAILS"]
                    strat = f" STRATEGY={d['STRATEGY']}" if "STRATEGY" in d else ""
                    log(f"[{idx}/{total}] OK  IS_MODELLED={d['IS_MODELLED']}  "
                        f"ACCOUNT={d['ACCOUNT_CODE']}  QTY={d['QUANTITY']:,}{strat}")
            else:
                failures += 1
                log(f"[{idx}/{total}] HTTP {resp.status_code}", "error")

        if idx < total and not stop_event.is_set():
            if not _interruptible_sleep(delay_seconds, stop_event):
                log("Stopped during delay.", "warn")
                break

    log(f"Done — Success={success}  Failed={failures}  Total={total}",
        "success" if failures == 0 else "warn")
    log_queue.put({"type": "summary", "success": success, "failed": failures, "total": total})
