"""Bonds engine — adapted from deal_poster_url_auth_v3.py."""
from __future__ import annotations

import csv
import json
import queue
import random
import re
import string
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import requests

_TIMEOUT = object()


# ── checksum utils ────────────────────────────────────────────────────────────

def _sum_digits(n: int) -> int:
    return sum(int(d) for d in str(n))


def _cusip_val(ch: str) -> int:
    return int(ch) if ch.isdigit() else 10 + (ord(ch) - ord("A"))


def _cusip_check(base8: str) -> str:
    total = 0
    for i, ch in enumerate(base8):
        v = _cusip_val(ch)
        v *= 2 if i % 2 == 1 else 1
        total += _sum_digits(v)
    return str((10 - (total % 10)) % 10)


def _isin_check(isin11: str) -> str:
    conv = "".join(ch if ch.isdigit() else str(10 + (ord(ch) - ord("A"))) for ch in isin11)
    rev = conv[::-1]
    total = 0
    for i, ch in enumerate(rev):
        n = int(ch)
        n *= 2 if i % 2 == 0 else 1
        if n > 9:
            n = (n // 10) + (n % 10)
        total += n
    return str((10 - (total % 10)) % 10)


_BASE36 = string.digits + string.ascii_uppercase


def _figi_check(figi11: str) -> str:
    def v36(c):
        return int(c) if c.isdigit() else 10 + (ord(c) - ord("A"))
    rev = figi11[::-1]
    total = sum(_sum_digits(v36(ch) * (2 if i % 2 == 0 else 1)) for i, ch in enumerate(rev))
    return str((10 - (total % 10)) % 10)


# ── reference data ────────────────────────────────────────────────────────────

class RefData:
    def __init__(self, ref_dir: Path) -> None:
        self.ref_dir = ref_dir
        self.sectors: List[str] = []
        self.ratings: List[str] = []
        self.exchanges: List[str] = []
        self.amounts: List[int] = []
        self.ipts_ranges: List[Tuple[float, float]] = []
        self.countries: List[Tuple[str, str, str, str, str]] = []
        self.currencies: List[str] = []
        self.float_benchmarks: Dict[str, str] = {}
        self.issuers: List[Tuple[str, Optional[str]]] = []
        self._load_all()
        self._ensure_defaults()

    def _csv(self, name: str) -> List[Dict[str, str]]:
        for ext in (".csv", ".json"):
            p = self.ref_dir / f"{name}{ext}"
            if p.exists():
                try:
                    with p.open("r", encoding="utf-8-sig", newline="") as f:
                        return [{k.strip(): (v.strip() if isinstance(v, str) else v)
                                 for k, v in r.items()}
                                for r in csv.DictReader(f)]
                except Exception:
                    pass
        return []

    def _load_all(self) -> None:
        # sectors
        for r in self._csv("sectors"):
            if r.get("NAME"):
                self.sectors.append(r["NAME"])
        # ratings
        for r in self._csv("ratings"):
            if r.get("NAME"):
                self.ratings.append(r["NAME"])
        # exchanges
        for r in self._csv("exchanges"):
            if r.get("NAME"):
                self.exchanges.append(r["NAME"])
        # amounts
        for r in self._csv("amounts"):
            try:
                self.amounts.append(int(r.get("AMOUNT", "")))
            except Exception:
                pass
        # ipts
        for r in self._csv("ipts"):
            try:
                lo, hi = float(r["LOW"]), float(r["HIGH"])
                if lo <= hi:
                    self.ipts_ranges.append((lo, hi))
            except Exception:
                pass
        # countries
        for r in self._csv("countries"):
            iso = (r.get("ISO2") or "").strip()
            iss = (r.get("ISSUER_COUNTRY") or "").strip()
            coi = (r.get("COUNTRY_OF_ISSUE") or "").strip()
            risk = (r.get("RISK_COUNTRY") or "").strip()
            ccy = (r.get("CURRENCY_CODE") or "").strip()
            if iso and iss and coi and risk and ccy:
                self.countries.append((iso, iss, coi, risk, ccy))
        # currencies
        for r in self._csv("currencies"):
            code = (r.get("CODE") or r.get("CURRENCY") or "").strip()
            if code:
                self.currencies.append(code)
        # float benchmarks
        for r in self._csv("float_benchmarks"):
            c, b = r.get("CODE"), r.get("BENCHMARK")
            if c and b:
                self.float_benchmarks[c.strip().upper()] = b.strip()
        # issuers
        for r in self._csv("issuers"):
            name = r.get("NAME")
            ticker = (r.get("TICKER") or "").strip() or None
            if name:
                self.issuers.append((name.strip(), ticker))

    def _ensure_defaults(self) -> None:
        if not self.sectors:
            self.sectors = ["Utilities", "Industrials", "Information Technology", "Energy",
                            "Consumer Discretionary", "Health Care", "Financials", "Materials"]
        if not self.ratings:
            self.ratings = ["A3/A-/A-", "Baa2/BBB/BBB-", "Ba1/BB+/BB+",
                            "Ba2/BB/BB-", "B1/B+/B+(EXP)"]
        if not self.exchanges:
            self.exchanges = ["MUNICH", "Euronext", "LSE", "NYSE", "NASDAQ"]
        if not self.countries:
            self.countries = [
                ("US", "US", "US", "US", "USD"), ("GB", "GB", "GB", "GB", "GBP"),
                ("CH", "CH", "CH", "CH", "CHF"), ("JP", "JP", "JP", "JP", "JPY"),
                ("CA", "CA", "CA", "CA", "CAD"), ("AU", "AU", "AU", "AU", "AUD"),
                ("FR", "FR", "FR", "FR", "EUR"), ("DE", "DE", "DE", "DE", "EUR"),
            ]
        if not self.currencies:
            self.currencies = ["USD", "EUR", "GBP"]
        if not self.amounts:
            self.amounts = [100_000_000, 150_000_000, 200_000_000, 300_000_000,
                            350_000_000, 400_000_000, 500_000_000]
        if not self.ipts_ranges:
            self.ipts_ranges = [(2.75, 3.00), (6.75, 7.00), (7.00, 7.25), (7.25, 7.50)]
        if not self.float_benchmarks:
            self.float_benchmarks = {"USD": "SOFR", "EUR": "€STR", "GBP": "SONIA",
                                     "CHF": "SARON", "JPY": "TONA"}


# ── generators ────────────────────────────────────────────────────────────────

_PREFIXES = ["NOVA", "APEX", "ZENITH", "VERTEX", "ORION", "ATLAS", "QUANTUM",
             "NIMBUS", "HELIOS", "ARCADIA", "FALCON", "UNITED", "ASTRION"]
_SUFFIXES = ["CAPITAL", "HOLDINGS", "GROUP", "INDUSTRIES", "FINANCIAL",
             "PARTNERS", "GLOBAL", "VENTURES", "SYSTEMS"]
_MODIFIERS = [" & CO", " INTERNATIONAL", " ASIA", " EUROPE", " LIMITED", " INC"]


def _gen_company() -> str:
    return f"{random.choice(_PREFIXES)} {random.choice(_SUFFIXES)}{random.choice(_MODIFIERS)}".strip()


def _gen_ticker(name: str) -> str:
    letters = "".join(c for c in name.upper() if c.isalpha())
    base = (letters[:3] + letters[-3:]) or "CO"
    t = base[:6]
    while len(t) < 4:
        t += random.choice(string.ascii_uppercase)
    return t


def _gen_cusip() -> str:
    base8 = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return base8 + _cusip_check(base8)


def _gen_isin(reg: str, iso: str, cusip: str) -> str:
    if reg.replace(" ", "").lower() == "144a":
        root = "US" + cusip
    else:
        body = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(9))
        root = iso + body
    return root + _isin_check(root)


def _gen_figi() -> str:
    core8 = "".join(random.choice(_BASE36) for _ in range(8))
    root = "BBG" + core8
    return root + _figi_check(root)


def _epoch_ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


def _future_dates(tenor: int, freq: str) -> dict:
    now = datetime.now(timezone.utc)
    settle = now + timedelta(days=random.randint(7, 30))
    f = freq.lower()
    first = settle + timedelta(days=182 if "semi" in f else 91 if "quarter" in f else 365)
    mat = settle + timedelta(days=int(365.25 * tenor))
    return {
        "TRANCHE_SETTLEMENT_DATE": _epoch_ms(settle),
        "FIRST_COUPON_DATE": _epoch_ms(first),
        "MATURITY_DATE": _epoch_ms(mat),
    }


def _make_id_bundle(reg_type: str, iso: str, ticker: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    reg = reg_type.strip().lower()
    if reg == "reg s":
        body = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(9))
        root = iso + body
        out["SEC_REGS_ISIN"] = root + _isin_check(root)
        out["SEC_REGS_CUSIP"] = _gen_cusip()
        out["SEC_REGS_FIGI"] = _gen_figi()
        out["SEC_REGS_TICKER"] = ticker
    elif reg == "144a":
        c = _gen_cusip()
        out["SEC_144A_ISIN"] = _gen_isin("144a", iso, c)
        out["SEC_144A_CUSIP"] = c
        out["SEC_144A_FIGI"] = _gen_figi()
        out["SEC_144A_TICKER"] = ticker
    else:  # 144A/Reg S or fallback
        body = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(9))
        root = iso + body
        out["SEC_REGS_ISIN"] = root + _isin_check(root)
        out["SEC_REGS_CUSIP"] = _gen_cusip()
        out["SEC_REGS_FIGI"] = _gen_figi()
        out["SEC_REGS_TICKER"] = ticker
        c = _gen_cusip()
        out["SEC_144A_ISIN"] = _gen_isin("144a", iso, c)
        out["SEC_144A_CUSIP"] = c
        out["SEC_144A_FIGI"] = _gen_figi()
        out["SEC_144A_TICKER"] = ticker
    return out


def _normalize_url(u: str) -> str:
    return re.sub(r"(?<!:)//+", "/", u)


# ── build functions (require RefData) ────────────────────────────────────────

def _build_tranche(ref: RefData, issuer: str, ticker: str,
                   country: Tuple, sector: str, rating: str, exchange: str,
                   tenor: int, reg: str, freq: str,
                   coupon_type_override: Optional[str] = None,
                   force_currency: Optional[str] = None) -> Dict[str, Any]:
    iso, issuer_cty, cty_issue, risk, _ = country
    ccy = force_currency or random.choice(ref.currencies)
    figi_core = _gen_figi()
    key = f"BBG:{figi_core}-F"
    dates = _future_dates(tenor, freq)
    coupon_type = coupon_type_override or random.choice(["Fixed", "Float"])
    lo, hi = random.choice(ref.ipts_ranges)
    if coupon_type == "Fixed":
        ipts = f"{lo:.2f}%-{hi:.2f}%"
    else:
        benchmark = ref.float_benchmarks.get(ccy, f"{ccy} O/N")
        ipts = f"{benchmark} + {random.randint(60, 180)}bps"

    det: Dict[str, Any] = {
        "DATASOURCE_ID": "BBG",
        "ISSUANCE_DATA_KEY": key,
        "ISSUER_TICKER": ticker,
        "ISSUER_NAME": issuer,
        "PRELIMINARY_SECURITY_TENOR": f"{tenor}Y",
        "MATURITY_DATE": dates["MATURITY_DATE"],
        "CURRENCY_CODE": ccy,
        "ISSUER_COUNTRY": issuer_cty,
        "TRANCHE_CURRENCY": ccy,
        "ISSUER_RATING": rating,
        "TRANCHE_STATUS": random.choice(["Announced", "Update", "Allocated"]),
        "IS_PERPETUAL": "FALSE",
        "IS_ROADSHOW": "FALSE",
        "ISSUER_SECTOR": sector,
        "ASSET_CLASS_TYPE": "Corporate",
        "BOND_CLASS": random.choice(["Investment Grade", "High Yield"]),
        "COUPON_FREQUENCY": freq,
        "COUPON_TYPE": coupon_type,
        "MINIMUM_PIECE": random.choice(["50000", "10000", "100000", "200000"]),
        "MINIMUM_INCREMENT": random.choice(["100", "1000", "10000", "100000"]),
        "EXCH_NAMES": exchange,
        "DAY_COUNT": random.choice(["Actual/Actual", "ACT/360", "30/360"]),
        "IPTS": ipts,
        "CURRENT_LEVEL": "",
        "COUNTRY_OF_ISSUE": cty_issue,
        "RISK_COUNTRY": risk,
        "BND_BANK": random.choice(["ANZ", "Barclays", "BNP Paribas", "CitiBank", "HSBC"]),
        "BOOKRUNNERS": random.choice([
            "Barclays/BNP Paribas/Credit Agricole CIB",
            "Goldman Sachs/J.P. Morgan",
            "Deutsche Bank/Morgan Stanley",
            "BofA/Mizuho/Goldman Sachs",
        ]),
        "BOND_SENIORITY": random.choice([
            "Senior Unsecured", "Secured", "Senior Preferred",
            "Junior Subordinated", "Senior Non-Preferred"
        ]),
        "EMERGING_MKT_IND": random.choice(["FALSE", "TRUE"]),
        "TRANCHE_SETTLEMENT_DATE": dates["TRANCHE_SETTLEMENT_DATE"],
        "FIRST_COUPON_DATE": dates["FIRST_COUPON_DATE"],
        "TOTAL_ISSUED_AMOUNT": str(random.choice(ref.amounts)),
        "REGISTRATION_TYPE": reg,
    }
    det.update(_make_id_bundle(reg, iso, ticker))
    return {"MESSAGE_TYPE": "EVENT_NEW_ISSUANCE_DATA", "SERVICE_NAME": "ISSUANCE_EVENT_HANDLER",
            "DETAILS": det}


def _pick_issuer(ref: RefData) -> Tuple[str, str]:
    if ref.issuers:
        name, tick = random.choice(ref.issuers)
        return name, tick or _gen_ticker(name)
    name = _gen_company()
    return name, _gen_ticker(name)


def _build_single(ref: RefData, force_currency: Optional[str] = None) -> Dict[str, Any]:
    issuer, ticker = _pick_issuer(ref)
    sector = random.choice(ref.sectors)
    rating = random.choice(ref.ratings)
    exchange = random.choice(ref.exchanges)
    country = random.choice(ref.countries)
    tenor = random.choice([3, 5, 7, 10])
    reg = random.choice(["Reg S", "144A", "144A/Reg S"])
    freq = random.choice(["Annual", "Semi-Annual"])
    return _build_tranche(ref, issuer, ticker, country, sector, rating, exchange,
                          tenor, reg, freq, force_currency=force_currency)


def _build_multi(ref: RefData, n: int, force_currency: Optional[str] = None) -> List[Dict[str, Any]]:
    issuer, ticker = _pick_issuer(ref)
    sector = random.choice(ref.sectors)
    rating = random.choice(ref.ratings)
    exchange = random.choice(ref.exchanges)
    country = random.choice(ref.countries)
    coupon_type = random.choice(["Fixed", "Float"])
    TENOR_POOL = [2, 3, 4, 5, 5.5, 6, 7, 7.5, 8, 9, 10, 12, 12.5, 15, 20, 30]
    tenors = random.sample(TENOR_POOL, min(n, len(TENOR_POOL)))
    if n > len(TENOR_POOL):
        tenors += [TENOR_POOL[-1] + i + 1 for i in range(n - len(TENOR_POOL))]
    return [_build_tranche(ref, issuer, ticker, country, sector, rating, exchange,
                           int(tenors[i]), random.choice(["Reg S", "144A"]),
                           random.choice(["Annual", "Semi-Annual"]), coupon_type,
                           force_currency=force_currency)
            for i in range(n)]


def _parse_tranches_spec(value: Any, multi_count: int) -> List[int]:
    if value is None:
        return [3] * max(multi_count, 0)
    if isinstance(value, int):
        return [value] * max(multi_count, 0)
    if isinstance(value, str):
        try:
            value = [int(p.strip()) for p in value.split(",") if p.strip()]
        except ValueError:
            return [3] * max(multi_count, 0)
    if isinstance(value, (list, tuple)):
        nums = []
        for x in value:
            try:
                nums.append(int(x))
            except Exception:
                continue
        if not nums:
            return [3] * max(multi_count, 0)
        return [nums[i] if i < len(nums) else nums[-1] for i in range(max(multi_count, 0))]
    return [3] * max(multi_count, 0)


# ── entry point ────────────────────────────────────────────────────────────────

def run_bonds(params: Dict[str, Any], env: Dict[str, Any],
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
    creds = env.get("credentials", {}).get("bonds_loans", {})
    username = creds.get("username", "")
    password = creds.get("password", "")

    dry_run = bool(params.get("dry_run", False))
    force_ccy = (params.get("currency") or "").strip().upper() or None

    ref_dir = Path(params.get("ref_dir", "C:\\python\\LATEST_PBI_JSON\\reference"))
    log(f"Loading reference data from {ref_dir} ...")
    ref = RefData(ref_dir)
    log(f"Reference data ready — {len(ref.issuers)} issuers, {len(ref.currencies)} currencies.")
    if force_ccy:
        log(f"Forcing currency = {force_ccy} for all tranches.")

    # ── Auth ─────────────────────────────────────────────────────────────────
    token = None
    if dry_run:
        log("DRY RUN — generating payloads only, nothing will be sent.", "warn")
    else:
        auth_url = f"https://{host}/sm/event-login-auth"
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
            token = auth_resp.json().get("SESSION_AUTH_TOKEN") or \
                    auth_resp.json().get("DETAILS", {}).get("SESSION_AUTH_TOKEN")
        except Exception:
            token = None
        if not token:
            raise RuntimeError(f"No SESSION_AUTH_TOKEN in auth response")

        log("Authenticated.")

    if stop_event.is_set():
        log("Stopped.", "warn")
        return

    # ── Setup ─────────────────────────────────────────────────────────────────
    url = _normalize_url(f"https://{host}/gwf//event_new_issuance_data")
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "User-Agent": "PostmanRuntime/7.48.0",
        "SESSION_AUTH_TOKEN": token,
        "Connection": "keep-alive",
    }

    single = int(params.get("single", 0))
    multi = int(params.get("multi", 1))
    sleep_ms = int(params.get("sleep_ms", 100))
    tranches_plan = _parse_tranches_spec(params.get("tranches_per_multi", [3]), multi)

    total = ok = err = 0
    sess = requests.Session()

    def post_one(payload: Dict) -> Tuple[Optional[int], str]:
        if stop_event.is_set():
            return None, "stopped"
        try:
            r = sess.post(url, headers=headers, json=payload, timeout=30, verify=verify_ssl)
            try:
                body = json.dumps(r.json())
            except Exception:
                body = r.text
            return r.status_code, body
        except requests.RequestException as exc:
            return None, str(exc)

    def _sleep():
        time.sleep(sleep_ms / 1000)

    # ── Single-tranche deals ─────────────────────────────────────────────────
    for i in range(single):
        if stop_event.is_set():
            break
        payload = _build_single(ref, force_ccy)
        det = payload["DETAILS"]
        total += 1
        if dry_run:
            ok += 1
            log(f"[SINGLE {i+1}/{single}] DRY RUN | {det['ISSUER_NAME']} "
                f"{det['PRELIMINARY_SECURITY_TENOR']} {det['CURRENCY_CODE']}")
            log(json.dumps(payload, indent=2))
            continue
        status, body = post_one(payload)
        if status is None:
            if stop_event.is_set():
                log(f"[SINGLE {i+1}/{single}] Stopped", "warn")
                break
            err += 1
            log(f"[SINGLE {i+1}/{single}] Connection error — {body}", "error")
        elif 200 <= status < 300:
            ok += 1
            log(f"[SINGLE {i+1}/{single}] OK {status} | {det['ISSUER_NAME']} "
                f"{det['PRELIMINARY_SECURITY_TENOR']} {det['CURRENCY_CODE']}")
        else:
            err += 1
            log(f"[SINGLE {i+1}/{single}] FAILED {status} | {body[:200]}", "error")
        if i < single - 1 and not stop_event.is_set():
            _sleep()

    # ── Multi-tranche deals ──────────────────────────────────────────────────
    for j in range(multi):
        if stop_event.is_set():
            break
        n = tranches_plan[j] if j < len(tranches_plan) else tranches_plan[-1]
        tranches = _build_multi(ref, n, force_ccy)
        issuer = tranches[0]["DETAILS"]["ISSUER_NAME"]
        log(f"[MULTI {j+1}/{multi}] {issuer} — {n} tranches")
        for k, t in enumerate(tranches, 1):
            if stop_event.is_set():
                break
            det = t["DETAILS"]
            total += 1
            if dry_run:
                ok += 1
                log(f"  [T{k}/{n}] DRY RUN | {det['PRELIMINARY_SECURITY_TENOR']} "
                    f"{det['CURRENCY_CODE']}")
                log(json.dumps(t, indent=2))
                continue
            status, body = post_one(t)
            if status is None:
                if stop_event.is_set():
                    log(f"  [T{k}/{n}] Stopped", "warn")
                    break
                err += 1
                log(f"  [T{k}/{n}] Connection error — {body}", "error")
            elif 200 <= status < 300:
                ok += 1
                log(f"  [T{k}/{n}] OK {status} | {det['PRELIMINARY_SECURITY_TENOR']} "
                    f"{det['CURRENCY_CODE']}")
            else:
                err += 1
                log(f"  [T{k}/{n}] FAILED {status} | {body[:200]}", "error")
            if k < n and not stop_event.is_set():
                _sleep()
        if j < multi - 1 and not stop_event.is_set():
            _sleep()

    log(f"Done — Success={ok}  Failed={err}  Total={total}",
        "success" if err == 0 else "warn")
    log_queue.put({"type": "summary", "success": ok, "failed": err, "total": total})
