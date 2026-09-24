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

import expected_store
from engines.email_builder import build_email
from engines.expected_writer import build_expected
from engines.outlook_sender import OutlookUnavailable, send_via_outlook

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


_VOWELS = "AEIOU"


def _ticker_stem(name: str) -> str:
    """The issuer-derived base of a ticker — real-world shape, no randomness.

    A ticker belongs to an *issuer*, so it is built from that issuer's name and
    nothing else: the first word gives its leading letter plus its next
    consonants, and every later word adds its initial. `BREOTASOLUTIONS
    INDUSTRIES` -> `BRTSI`, `FINACOVENTURES RESOURCES` -> `FNCVR`.
    """
    words = [w for w in re.split(r"[^A-Za-z]+", name.upper()) if w]
    if not words:
        return "CO"
    head = words[0]
    stem = head[0] + "".join(c for c in head[1:] if c not in _VOWELS)
    if len(stem) < 4:                       # vowel-heavy first word (e.g. AURA)
        stem = head
    base = (stem[:4] + "".join(w[0] for w in words[1:]))[:5]
    # A short name ("3M Co") can leave 2 letters; top it up from the name's own
    # letters so the ticker still looks like one.
    letters = "".join(c for c in name.upper() if c.isalpha())
    for ch in letters:
        if len(base) >= 4:
            break
        if ch not in base:
            base += ch
    return base or "CO"


def _ticker_variants(name: str):
    """The stem, then progressively different spellings of it.

    Only reached when the stem is already taken — a second `… RESOURCES` issuer
    has to differ somehow, and a re-spelling of the name beats a random string
    because it still reads as that issuer's ticker.
    """
    letters = "".join(c for c in name.upper() if c.isalpha()) or "CO"
    base = _ticker_stem(name)
    yield base
    core = base[:3] if len(base) > 3 else base
    for ch in letters[1:] + string.ascii_uppercase:
        yield (core + ch)[:5]
    while True:                             # exhausted the alphabet — give up gracefully
        yield (core + random.choice(string.ascii_uppercase)
               + random.choice(_BASE36))[:5]


class _TickerMinter:
    """Hands out one ticker per issuer, unique across every deal it sees.

    Per-deal rather than per-run (this replaced decision D8's single run-wide
    ticker): a ticker identifies an issuer, so two issuers sharing one made the
    emails wrong *and* broke row matching — `comparators._key_ticker` only
    matches when exactly one row carries the ticker, so every deal after the
    first fell through to the weaker keys.

    Run isolation now comes from the run's ticker *set* (`util_run.ticker`),
    which `db_reader.fetch_rows` turns into an `IN` clause. `taken` is seeded
    with the tickers earlier runs already used, so that set stays unambiguous
    across runs too.
    """

    def __init__(self, taken=None) -> None:
        self.taken = {str(t).strip().upper() for t in (taken or ()) if str(t).strip()}
        self.minted: List[str] = []

    def mint(self, name: str, preferred: Optional[str] = None) -> str:
        """The issuer's own ticker if it has one and it is free, else a minted one.

        `preferred` is the ticker `reference/bonds/issuers.csv` already pairs
        with this issuer — 4005 issuers, 4005 distinct tickers. That file is the
        source of truth; deriving one from the name is the *fallback*, for an
        issuer with no reference ticker (or a name invented by `_gen_company`),
        and for the case where the reference ticker is already spoken for
        because an earlier run used the same issuer.
        """
        cand = (preferred or "").strip().upper()
        if cand and cand not in self.taken:
            self.taken.add(cand)
            self.minted.append(cand)
            return cand
        for cand in _ticker_variants(name):
            if cand and cand not in self.taken:
                self.taken.add(cand)
                self.minted.append(cand)
                return cand
        raise RuntimeError("unreachable — _ticker_variants never ends")


def _gen_ticker(name: str) -> str:
    return _ticker_stem(name)


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
    if coupon_type == "Prelim":
        # Prelim carries no pricing at all — the app cannot assign the issuance.
        ipts = ""
    elif coupon_type == "Fixed":
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


def _pick_issuer(ref: RefData, minter: Optional[_TickerMinter] = None) -> Tuple[str, str]:
    """One issuer and its ticker.

    The reference CSV's ticker wins either way — the minter only guarantees no
    two deals end up sharing one, deriving a ticker from the name when the CSV
    has none to give.
    """
    if ref.issuers:
        name, tick = random.choice(ref.issuers)
    else:
        name, tick = _gen_company(), None
    if minter is not None:
        return name, minter.mint(name, preferred=tick)
    return name, tick or _gen_ticker(name)


def _build_single(ref: RefData, force_currency: Optional[str] = None,
                  minter: Optional[_TickerMinter] = None,
                  coupon_type: Optional[str] = None) -> Dict[str, Any]:
    issuer, ticker = _pick_issuer(ref, minter)
    sector = random.choice(ref.sectors)
    rating = random.choice(ref.ratings)
    exchange = random.choice(ref.exchanges)
    country = random.choice(ref.countries)
    tenor = random.choice([3, 5, 7, 10])
    reg = random.choice(["Reg S", "144A", "144A/Reg S"])
    freq = random.choice(["Annual", "Semi-Annual"])
    return _build_tranche(ref, issuer, ticker, country, sector, rating, exchange,
                          tenor, reg, freq, coupon_type,
                          force_currency=force_currency)


def _build_multi(ref: RefData, n: int, force_currency: Optional[str] = None,
                 minter: Optional[_TickerMinter] = None,
                 coupon_type_override: Optional[str] = None) -> List[Dict[str, Any]]:
    # One issuer, one ticker, n tranches — the tranches of a deal share both.
    issuer, ticker = _pick_issuer(ref, minter)
    sector = random.choice(ref.sectors)
    rating = random.choice(ref.ratings)
    exchange = random.choice(ref.exchanges)
    country = random.choice(ref.countries)
    coupon_type = coupon_type_override or random.choice(["Fixed", "Float"])
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
    # Blank = today's behaviour: Fixed/Float drawn per tranche (single) or per
    # deal (multi). "Prelim" pins every tranche and blanks IPTS.
    coupon_type = (params.get("coupon_type") or "").strip().title() or None
    if coupon_type and coupon_type not in ("Fixed", "Float", "Prelim"):
        log(f"Unsupported coupon_type '{coupon_type}' — falling back to "
            f"random Fixed/Float", "warn")
        coupon_type = None

    # ── Email options ─────────────────────────────────────────────────────────
    # email_mode: "off" (POST only, today's behaviour) | "both" (POST + email)
    #             | "email_only" (generate & send emails, no auth/POST)
    email_mode = str(params.get("email_mode", "off") or "off")
    email_format = params.get("email_format")
    email_cfg = params.get("email", {}) or {}
    send_email = email_mode in ("both", "email_only")
    do_post = (email_mode != "email_only") and not dry_run

    # ── Expectation capture (spec §18) ────────────────────────────────────────
    # Every generated email also writes the database state it *should* produce
    # into the util_* mirror tables, so the email-parsing agent's output can be
    # diffed against ground truth later. SQLite only — no network, no
    # credentials — so it works in dry_run and email_only, and it must never
    # fail a run.
    compare_cfg = params.get("compare", {}) or {}
    capture_expected = bool(params.get(
        "capture_expected",
        bool(compare_cfg.get("auto_capture", True)) and email_mode != "off"))
    # Each deal gets its own issuer-derived ticker; the minter keeps them
    # distinct within the run and against the tickers earlier runs used, so the
    # run's ticker set still isolates its rows in the app DB.
    unique_ticker = bool(params.get("unique_ticker", capture_expected))
    minter: Optional[_TickerMinter] = None
    if unique_ticker:
        try:
            seed = expected_store.known_tickers()
        except Exception:
            seed = ()                       # store not reachable — never fail a run
        minter = _TickerMinter(seed)
    util_run_id: Optional[str] = None
    captured = {"deals": 0, "tranches": 0}

    def capture_deal(payloads: List[Dict], tag: str, subject: str, html: str,
                     meta: Dict[str, Any], send_status: str, send_note: str) -> None:
        """Write the expected rows for one email. Never raises."""
        if not (capture_expected and util_run_id):
            return
        try:
            captured["deals"] += 1
            deal_seq = captured["deals"]
            det0 = payloads[0].get("DETAILS", {})
            email_id = expected_store.add_email({
                "util_run_id": util_run_id, "util_deal_seq": deal_seq,
                "subject": subject, "body_html": html,
                "email_format": email_format or "", "recipient": email_cfg.get("recipient", ""),
                "send_status": send_status, "send_note": send_note,
                "rendered_fields": meta.get("rendered_fields") or [],
                "ticker": det0.get("ISSUER_TICKER", ""), "tranches": len(payloads),
            })
            result = build_expected(payloads, {
                **meta,
                "sent_at": datetime.now(timezone.utc),
                "email_format": email_format or "",
                "expected_datasource": compare_cfg.get("expected_datasource") or "LLM",
            }, deal_seq=deal_seq)
            warnings = list(result["warnings"])
            for table, rows in result["rows"].items():
                _, length_warnings = expected_store.insert_rows(
                    table, rows, util_run_id=util_run_id, util_source="expected",
                    util_asset_class="bonds", util_email_id=email_id)
                warnings.extend(length_warnings)
            counts = result["counts"]
            captured["tranches"] += counts["tranches"]
            log(f"  {tag} ⊞ Expected state captured — 1 deal · "
                f"{counts['tranches']} tranches · {counts['securities']} securities",
                "success")
            for w in warnings[:6]:
                log(f"  {tag} ⊞ {w}", "warn")
            if len(warnings) > 6:
                log(f"  {tag} ⊞ … and {len(warnings) - 6} more capture warning(s).", "warn")
        except Exception as exc:
            log(f"  {tag} ⊞ Expectation capture failed — {exc}", "warn")

    def emit_email(payloads: List[Dict], tag: str) -> bool:
        """Build + send one broker email for a deal. Returns True on success.

        The expectation is captured whether or not the send worked — an email
        that never left the machine still has a payload worth diffing, and the
        send outcome is recorded on util_email.send_status (spec §18.11).
        """
        try:
            subject, html, meta = build_email("bonds", payloads, email_format)
        except Exception as exc:
            log(f"  {tag} ✉ Email build failed — {exc}", "error")
            return False

        sent = False
        try:
            note = send_via_outlook(subject, html,
                                    email_cfg.get("recipient", ""),
                                    email_cfg.get("save_copy_dir") or None)
            send_status, send_note, sent = "sent", note, True
            log(f"  {tag} ✉ Email {note}", "success")
        except OutlookUnavailable as exc:
            send_status, send_note = "skipped", str(exc)
            log(f"  {tag} ✉ Email skipped — {exc}", "warn")
        except Exception as exc:
            send_status, send_note = "failed", str(exc)
            log(f"  {tag} ✉ Email failed — {exc}", "error")

        capture_deal(payloads, tag, subject, html, meta, send_status, send_note)
        return sent

    ref_dir = Path(params.get("ref_dir", "C:\\python\\LATEST_PBI_JSON\\reference"))
    log(f"Loading reference data from {ref_dir} ...")
    ref = RefData(ref_dir)
    log(f"Reference data ready — {len(ref.issuers)} issuers, {len(ref.currencies)} currencies.")
    if force_ccy:
        log(f"Forcing currency = {force_ccy} for all tranches.")
    if email_mode != "off":
        log(f"Email mode = {email_mode} (format={email_format or 'default'}, "
            f"recipient={email_cfg.get('recipient') or 'UNSET'}).", "warn")
    if minter is not None:
        log("Unique ticker per deal — each issuer keeps its own reference "
            "ticker; both lanes of a deal share it (spec §18.7).")

    if capture_expected:
        try:
            expected_store.set_store_path(compare_cfg.get("store_path") or None)
            expected_store.init_store()
            util_run_id = expected_store.add_run({
                "tool": "bonds", "util_asset_class": "bonds",
                "env": params.get("env_name", ""), "host": host,
                "email_format": email_format or "", "email_mode": email_mode,
                # Filled in at the end of the run — the tickers do not exist
                # until their deals are built.
                "ticker": "",
                "params": {k: v for k, v in params.items() if k != "email"},
            })
            log(f"⊞ Expectation capture ON — util_run {util_run_id} in "
                f"{expected_store.store_path()}")
        except Exception as exc:
            capture_expected = False
            log(f"⊞ Expectation capture disabled — store unavailable: {exc}", "warn")

    # ── Auth ─────────────────────────────────────────────────────────────────
    token = None
    if not do_post:
        if email_mode == "email_only":
            log("EMAIL ONLY — generating & sending emails, no auth or POST.", "warn")
        else:
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
        payload = _build_single(ref, force_ccy, minter, coupon_type)
        det = payload["DETAILS"]
        tag = f"[SINGLE {i+1}/{single}]"

        if email_mode == "email_only":
            total += 1
            log(f"{tag} {det['ISSUER_NAME']} ({det['ISSUER_TICKER']}) "
                f"{det['PRELIMINARY_SECURITY_TENOR']} {det['CURRENCY_CODE']}")
            if emit_email([payload], tag):
                ok += 1
            else:
                err += 1
            if i < single - 1 and not stop_event.is_set():
                _sleep()
            continue

        total += 1
        if dry_run:
            ok += 1
            log(f"{tag} DRY RUN | {det['ISSUER_NAME']} "
                f"{det['PRELIMINARY_SECURITY_TENOR']} {det['CURRENCY_CODE']}")
            log(json.dumps(payload, indent=2))
        else:
            status, body = post_one(payload)
            if status is None:
                if stop_event.is_set():
                    log(f"{tag} Stopped", "warn")
                    break
                err += 1
                log(f"{tag} Connection error — {body}", "error")
            elif 200 <= status < 300:
                ok += 1
                log(f"{tag} OK {status} | {det['ISSUER_NAME']} "
                    f"{det['PRELIMINARY_SECURITY_TENOR']} {det['CURRENCY_CODE']}")
            else:
                err += 1
                log(f"{tag} FAILED {status} | {body[:200]}", "error")
        if email_mode == "both":
            emit_email([payload], tag)
        if i < single - 1 and not stop_event.is_set():
            _sleep()

    # ── Multi-tranche deals ──────────────────────────────────────────────────
    for j in range(multi):
        if stop_event.is_set():
            break
        n = tranches_plan[j] if j < len(tranches_plan) else tranches_plan[-1]
        tranches = _build_multi(ref, n, force_ccy, minter, coupon_type)
        det0 = tranches[0]["DETAILS"]
        issuer = det0["ISSUER_NAME"]
        log(f"[MULTI {j+1}/{multi}] {issuer} ({det0['ISSUER_TICKER']}) — {n} tranches")

        if email_mode == "email_only":
            total += 1
            if emit_email(tranches, f"[MULTI {j+1}/{multi}]"):
                ok += 1
            else:
                err += 1
            if j < multi - 1 and not stop_event.is_set():
                _sleep()
            continue

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
            else:
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
        if email_mode == "both":
            emit_email(tranches, f"[MULTI {j+1}/{multi}]")
        if j < multi - 1 and not stop_event.is_set():
            _sleep()

    if capture_expected and util_run_id:
        try:
            # The run's ticker set — one per deal. db_reader turns this into the
            # IN clause that pulls back exactly this run's rows.
            tickers = ",".join(minter.minted) if minter else ""
            expected_store.update_run(util_run_id, deals=captured["deals"],
                                      tranches=captured["tranches"],
                                      ticker=tickers)
            log(f"⊞ Expectations for run {util_run_id} — {captured['deals']} deal(s), "
                f"{captured['tranches']} tranche(s)"
                + (f" · tickers {tickers}" if tickers else "") + ".")
        except Exception as exc:
            log(f"⊞ Could not finalise the capture run — {exc}", "warn")

    log(f"Done — Success={ok}  Failed={err}  Total={total}",
        "success" if err == 0 else "warn")
    log_queue.put({"type": "summary", "success": ok, "failed": err, "total": total})
