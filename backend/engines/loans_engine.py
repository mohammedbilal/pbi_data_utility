"""Loans engine — adapted from PBI_LOAN_JSON_PUBLISHER_V1."""
from __future__ import annotations

import csv
import json
import os
import queue
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from faker import Faker

from engines.email_builder import build_email
from engines.loan_utils.cusip import generate_cusip, generate_roll_cusips
from engines.loan_utils.dates import generate_deal_dates, generate_tranche_dates
from engines.outlook_sender import OutlookUnavailable, send_via_outlook
from engines.url_utils import join_url

fake = Faker()

SUB_ASSET_CLASS = "TLB"
TRADE_DESK = "Lev Loans"
DRYRUN_MASTER_LOAN_ID = "DRYRUN-MASTER-LOAN-ID"

COUNTRIES_FALLBACK = [
    ("United Kingdom", "GB", "GBP", "SONIA"),
    ("United States of America", "US", "USD", "SOFR"),
    ("Norway", "NO", "EUR", "ESTR"),
    ("Austria", "AT", "EUR", "ESTR"),
    ("France", "FR", "EUR", "ESTR"),
    ("Italy", "IT", "EUR", "ESTR"),
    ("Switzerland", "CH", "CHF", "TONA"),
    ("Australia", "AU", "AUD", "AONIA"),
    ("Canada", "CA", "CAD", "CORRA"),
    ("Singapore", "SG", "SGD", "SORA"),
]

BOND_SENIORITY = ["1st Lien", "2nd Lien", "Senior Secured", "Senior Unsecured",
                  "Senior Preferred", "Senior Subordinated"]
FITCH_RATINGS = ["A", "A+", "A-", "AA", "B", "B+", "BB", "BB+", "BBB", "BBB-", "CCC"]
MOODYS_RATINGS = ["A2", "A1", "A3", "Aa2", "B2", "B1", "Ba2", "Ba1", "Baa2", "Baa3"]
SP_RATINGS = ["A", "A+", "A-", "AA", "B", "B+", "BB", "BB+", "BBB", "BBB-"]
OUTLOOKS = ["Negative Outlook", "Positive Outlook", "Stable", "No Outlook"]
ISSUE_SIZES = [750_000_000, 100_000_000, 150_000_000, 200_000_000, 225_000_000,
               250_000_000, 300_000_000, 350_000_000, 400_000_000]
COUPON_TYPES = ["Float", "Fixed"]
BANKS = ["Deutsche Bank", "Barclays", "Goldman Sachs", "Morgan Stanley", "J.P. Morgan",
         "Bank of America", "Citigroup", "HSBC", "Jefferies", "RBC Capital Markets"]
SPONSORS = ["Silver Lake Partners", "Blackstone", "KKR", "Apollo Global Management",
            "Carlyle Group", "TPG Capital", "Bain Capital", "CVC Capital Partners"]
INDUSTRY_SECTORS = ["Technology", "Healthcare", "Industrials", "Consumer", "Energy",
                    "Telecommunications", "Financials", "Materials", "Utilities"]
LOAN_REASONS = [
    "Refinancing existing debt and funding strategic acquisitions",
    "General corporate purposes",
    "Dividend recapitalization",
    "Leveraged buyout financing",
]
USE_OF_PROCEEDS = [
    "Refinance existing term loan and support acquisition financing",
    "Repay revolving credit facility and fund growth initiatives",
    "General corporate purposes including working capital",
]
TENORS = ["5Y", "6Y", "7Y", "8Y"]
SETTLEMENT_PERIODS = ["T+3", "T+5", "T+7"]
MIN_INCREMENTS = [1000, 2000, 5000]
MIN_PIECES = [10000, 25000, 50000]


class DataGenerator:
    def __init__(self, issuers_path: str, currencies_path: str) -> None:
        self.issuers = self._load_issuers(issuers_path)
        self.countries = self._load_countries(currencies_path)

    @staticmethod
    def _load_issuers(path: str) -> List[Dict]:
        if not os.path.exists(path):
            return [{"issuer_name": "TEST ISSUER CORP", "ticker": "TIC"}]
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("issuer_name") and r.get("ticker")]
        return rows or [{"issuer_name": "TEST ISSUER CORP", "ticker": "TIC"}]

    @staticmethod
    def _load_countries(path: str) -> List[Dict]:
        if not os.path.exists(path):
            return [{"country_name": n, "country_code": c, "currency": cur, "float_benchmark": b}
                    for (n, c, cur, b) in COUNTRIES_FALLBACK]
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("country_code")]
        return rows or [{"country_name": n, "country_code": c, "currency": cur, "float_benchmark": b}
                        for (n, c, cur, b) in COUNTRIES_FALLBACK]

    def available_currencies(self) -> List[str]:
        return sorted({c["currency"].upper() for c in self.countries})

    def _generate_deal_details(self, force_currency: Optional[str] = None) -> Tuple[Dict, Dict]:
        issuer = random.choice(self.issuers)
        if force_currency:
            matches = [c for c in self.countries
                       if c["currency"].upper() == force_currency.upper()]
            country = random.choice(matches) if matches else random.choice(self.countries)
        else:
            country = random.choice(self.countries)
        country_code = country["country_code"]
        deal_dates, price_date = generate_deal_dates()
        deal = {
            "ANNOUNCEMENT_DT": deal_dates["ANNOUNCEMENT_DT"],
            "MEETING_DATE": deal_dates["MEETING_DATE"],
            "COMMIT_DATE": deal_dates["COMMIT_DATE"],
            "PRICE_DATE": deal_dates["PRICE_DATE"],
            "ISSUER_NAME": issuer["issuer_name"],
            "ISSUER_TICKER": issuer["ticker"],
            "ISSUER_COUNTRY": country_code,
            "ISSUER_BUSINESS_DESCRIPTION": fake.catch_phrase(),
            "ISSUER_FITCH": random.choice(FITCH_RATINGS),
            "ISSUER_MOODYS": random.choice(MOODYS_RATINGS),
            "ISSUER_SP": random.choice(SP_RATINGS),
            "OUTLOOK_FITCH": random.choice(OUTLOOKS),
            "OUTLOOK_MOODYS": random.choice(OUTLOOKS),
            "OUTLOOK_SP": random.choice(OUTLOOKS),
            "INDUSTRY_SECTOR": random.choice(INDUSTRY_SECTORS),
            "SPONSOR": random.choice(SPONSORS),
            "BND_BANK": random.choice(BANKS),
            "BOOK_RUNNERS": "/".join(random.sample(BANKS, k=random.randint(2, 3))),
            "LEAD_BROKER": random.choice(BANKS),
            "CREDIT_ANALYST": fake.email(),
            "LOAN_REASON": random.choice(LOAN_REASONS),
            "USE_OF_PROCEEDS": random.choice(USE_OF_PROCEEDS),
            "IS_ROADSHOW": False,
            "LOAN_STATUS": random.choice(["ACTIVE", "PENDING"]),
            "SUB_ASSET_CLASS": SUB_ASSET_CLASS,
            "TRADE_DESK": TRADE_DESK,
        }
        context = {
            "country_code": country_code,
            "currency": country["currency"],
            "benchmark": country["float_benchmark"],
            "price_date": price_date,
        }
        return deal, context

    def _generate_tranche_details(self, context: Dict, currency: str) -> Dict:
        country_code = context["country_code"]
        coupon_type = random.choice(COUPON_TYPES)
        issue_size = random.choice(ISSUE_SIZES)
        final_size = int(issue_size * (1 + random.choice([-1, 1]) * random.uniform(0.03, 0.05)))
        spread = random.randint(1, 100) if coupon_type == "Float" else 0
        yield_val = round(random.uniform(4.0, 8.0), 2)
        final_spread = max(0, spread + random.randint(-5, 5))
        final_yield = round(yield_val + random.uniform(-0.2, 0.2), 2)
        tenor = random.choice(TENORS)
        t_dates = generate_tranche_dates(context["price_date"], int(tenor[:-1]))
        return {
            "ISSUE_SIZE": issue_size,
            "FINAL_SIZE": final_size,
            "COUPON_TYPE": coupon_type,
            "SPREAD": str(spread),
            "YIELD": str(yield_val),
            "FINAL_SPREAD": str(final_spread),
            "FINAL_YIELD": str(final_yield),
            "FINAL_OID": str(random.randint(1, 5)),
            "ORIGINAL_ISSUE_DISCOUNT": str(random.randint(1, 5)),
            "TENOR": tenor,
            "MATURITY_DATE": t_dates["MATURITY_DATE"],
            "TRANCHE_FITCH": random.choice(FITCH_RATINGS),
            "TRANCHE_MOODYS": random.choice(MOODYS_RATINGS),
            "TRANCHE_SP": random.choice(SP_RATINGS),
            "TRANCHE_CURRENCY": currency,
            "TRANCHE_SETTLEMENT_DATE": t_dates["TRANCHE_SETTLEMENT_DATE"],
            "TRANCHE_SETTLEMENT_PERIOD": random.choice(SETTLEMENT_PERIODS),
            "BOND_SENIORITY": random.choice(BOND_SENIORITY),
            "COV_LITE": random.choice([True, False]),
            "NON_CASHLESS_ROLL": random.choice([True, False]),
            "REPRICING": random.choice([True, False]),
            "MINIMUM_INCREMENT": random.choice(MIN_INCREMENTS),
            "MINIMUM_PIECE": random.choice(MIN_PIECES),
            "ROLL_CUSIP": generate_roll_cusips(country_code),
            "SECURITIES": [{"CUSIP": generate_cusip(country_code),
                            "LOAN_SECURITY_STATUS": random.choice(["PENDING", "ACTIVE"]),
                            "LX_ID": "LN" + "".join(random.choice("0123456789") for _ in range(8))}],
        }

    def generate_deal(self, tranches: int = 1, currency: Optional[str] = None) -> List[Dict]:
        deal, context = self._generate_deal_details(force_currency=currency)
        multi_currency = currency is None and tranches > 1 and random.randint(1, 5) == 1
        payloads = []
        for _ in range(tranches):
            if currency:
                tranche_currency = currency.upper()
            elif multi_currency:
                tranche_currency = random.choice(self.countries)["currency"]
            else:
                tranche_currency = context["currency"]
            details = {**deal, **self._generate_tranche_details(context, tranche_currency)}
            payloads.append({
                "MESSAGE_TYPE": "EVENT_CREATE_NEW_LOAN_ISSUANCE",
                "SERVICE_NAME": "ISSUANCE_EVENT_HANDLER",
                "DETAILS": details,
            })
        return payloads


def _login(host: str, username: str, password: str, verify_ssl: bool) -> str:
    auth_url = join_url(host, "/sm/event-login-auth")
    body = {"MESSAGE_TYPE": "TXN_LOGIN_AUTH", "SERVICE_NAME": "AUTH_MANAGER",
            "DETAILS": {"USER_NAME": username, "PASSWORD": password}}
    headers = {"Content-Type": "application/json", "SOURCE_REF": "12345",
               "User-Agent": "PostmanRuntime/7.54.0"}
    try:
        resp = requests.post(auth_url, json=body, headers=headers, timeout=30, verify=verify_ssl)
    except requests.RequestException as exc:
        raise RuntimeError(f"Auth connection error: {exc}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Auth failed (HTTP {resp.status_code}): {resp.text[:300]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("Auth response was not valid JSON")
    token = data.get("SESSION_AUTH_TOKEN")
    if not token:
        raise RuntimeError(f"No SESSION_AUTH_TOKEN in auth response: {data}")
    return token


def _publish(url: str, headers: Dict, payload: Dict, verify_ssl: bool) -> Tuple[str, Any]:
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30, verify=verify_ssl)
    except requests.RequestException as exc:
        return "HTTP_ERROR", {"error": str(exc)}
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if isinstance(data, dict) and data.get("MESSAGE_TYPE") == "EVENT_NACK":
        return "NACK", data
    if resp.status_code >= 400:
        return "HTTP_ERROR", {"status_code": resp.status_code, "body": data}
    return "ACK", data


def _fetch_master_loan_id(deal_query_url: str, headers: Dict,
                          issuer_ticker: str, verify_ssl: bool) -> str:
    body = {"DETAILS": {"maxRows": 10000, "maxView": 10000,
                        "CRITERIA_MATCH": f"ISSUER_TICKER=='{issuer_ticker}'"}}
    try:
        resp = requests.post(deal_query_url, json=body, headers=headers, timeout=30, verify=verify_ssl)
    except requests.RequestException as exc:
        raise RuntimeError(f"ALL_LOAN_DEAL query failed: {exc}")
    if resp.status_code >= 400:
        raise RuntimeError(f"ALL_LOAN_DEAL HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("ALL_LOAN_DEAL response was not JSON")
    rows = data.get("ROW") or []
    if not rows:
        raise RuntimeError(f"No deals for ISSUER_TICKER={issuer_ticker}")
    newest = max(rows, key=lambda r: r.get("CREATED_ON") or -1)
    mid = newest.get("MASTER_LOAN_ID")
    if not mid:
        raise RuntimeError(f"No MASTER_LOAN_ID in row: {newest}")
    return mid


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_event.is_set():
            return False
        time.sleep(min(0.1, end - time.monotonic()))
    return True


# ── entry point ────────────────────────────────────────────────────────────────

def run_loans(params: Dict[str, Any], env: Dict[str, Any],
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

    ref_dir = Path(params.get("ref_dir", "C:\\python\\PBI_LOAN_JSON_PUBLISHER_V1\\reference_data"))
    issuers_path = str(ref_dir / "issuers.csv")
    currencies_path = str(ref_dir / "currencies.csv")

    log(f"Loading reference data from {ref_dir} ...")
    gen = DataGenerator(issuers_path, currencies_path)
    log(f"Reference data ready — {len(gen.issuers)} issuers, "
        f"{len(gen.available_currencies())} currencies.")

    single = int(params.get("single", 3))
    multi = int(params.get("multi", 0))
    delay = float(params.get("delay", 15))
    deal_query_wait = float(params.get("deal_query_wait", 6))
    dry_run = bool(params.get("dry_run", False))
    currency = (params.get("currency") or "").upper() or None

    # ── Email options ─────────────────────────────────────────────────────────
    # email_mode: "off" (POST only) | "both" (POST + email) | "email_only"
    email_mode = str(params.get("email_mode", "off") or "off")
    email_format = params.get("email_format")
    email_cfg = params.get("email", {}) or {}
    send_email = email_mode in ("both", "email_only")
    do_post = (email_mode != "email_only") and not dry_run

    def emit_email(payloads: List[Dict], tag: str) -> bool:
        """Build + send one broker email for a loan deal. True on success."""
        try:
            subject, html, _meta = build_email("loans", payloads, email_format)
            note = send_via_outlook(subject, html,
                                    email_cfg.get("recipient", ""),
                                    email_cfg.get("save_copy_dir") or None)
            log(f"{tag} ✉ Email {note}", "success")
            return True
        except OutlookUnavailable as exc:
            log(f"{tag} ✉ Email skipped — {exc}", "warn")
            return False
        except Exception as exc:
            log(f"{tag} ✉ Email failed — {exc}", "error")
            return False
    tranches_per_multi_raw = params.get("tranches_per_multi", [2, 3])
    if isinstance(tranches_per_multi_raw, int):
        tranches_per_multi = [tranches_per_multi_raw]
    else:
        tranches_per_multi = list(tranches_per_multi_raw)

    if currency and currency not in gen.available_currencies():
        log(f"Unknown currency '{currency}'. Available: {', '.join(gen.available_currencies())}", "error")
        return

    # ── Build all deal payloads ──────────────────────────────────────────────
    deals: List[List[Dict]] = []
    for _ in range(single):
        deals.append(gen.generate_deal(tranches=1, currency=currency))
    for i in range(multi):
        n = tranches_per_multi[i % len(tranches_per_multi)] if tranches_per_multi else 2
        deals.append(gen.generate_deal(tranches=n, currency=currency))

    total_posts = sum(len(d) for d in deals)
    log(f"Plan: {single} single + {multi} multi deals = {total_posts} POST(s). dry_run={dry_run}")
    if email_mode != "off":
        log(f"Email mode = {email_mode} (format={email_format or 'default'}, "
            f"recipient={email_cfg.get('recipient') or 'UNSET'}).", "warn")

    # ── Email-only fast path (no auth, no POST — one email per deal) ──────────
    if email_mode == "email_only":
        ok = err = 0
        for deal_idx, payloads in enumerate(deals, start=1):
            if stop_event.is_set():
                log("Stopped.", "warn")
                break
            borrower = payloads[0]["DETAILS"].get("ISSUER_NAME", "?")
            log(f"[D{deal_idx}] {borrower} — {len(payloads)} tranche(s)")
            if emit_email(payloads, f"[D{deal_idx}]"):
                ok += 1
            else:
                err += 1
        log(f"Done — Emails sent={ok}  failed={err}  deals={len(deals)}",
            "success" if err == 0 else "warn")
        log_queue.put({"type": "summary", "success": ok, "failed": err,
                       "total": ok + err})
        return

    # ── Auth ─────────────────────────────────────────────────────────────────
    token = None
    if do_post:
        log(f"Authenticating as {username} ...")
        try:
            token = _login(host, username, password, verify_ssl)
        except RuntimeError as exc:
            log(str(exc), "error")
            return
        log("Authenticated.")

    if stop_event.is_set():
        log("Stopped.", "warn")
        return

    # ── Post events ──────────────────────────────────────────────────────────
    # Built only when they will be used: a dry run must work with no host configured.
    publish_url = join_url(host, "/gwf//EVENT_CREATE_NEW_LOAN_ISSUANCE") if not dry_run else ""
    deal_query_url = join_url(host, "/gwf/ALL_LOAN_DEAL") if not dry_run else ""
    pub_headers = {
        "Content-Type": "application/json", "Accept": "*/*",
        "SOURCE_REF": "12345", "Cache-Control": "no-cache",
        "User-Agent": "PostmanRuntime/7.48.0",
        "SESSION_AUTH_TOKEN": token or "",
        "Connection": "keep-alive",
    }

    counts = {"ACK": 0, "NACK": 0, "HTTP_ERROR": 0, "DRY_RUN": 0}
    first = True

    for deal_idx, payloads in enumerate(deals, start=1):
        if stop_event.is_set():
            log("Stopped.", "warn")
            break

        is_multi = len(payloads) > 1
        master_loan_id = None
        n_tranches = len(payloads)

        for t_idx, payload in enumerate(payloads, start=1):
            if stop_event.is_set():
                log(f"Stopped at deal {deal_idx} tranche {t_idx}.", "warn")
                break

            if t_idx > 1 and master_loan_id:
                payload["DETAILS"]["MASTER_LOAN_ID"] = master_loan_id

            if not first and not dry_run and delay:
                if not _interruptible_sleep(delay, stop_event):
                    log("Stopped during delay.", "warn")
                    break
            first = False

            issuer = payload["DETAILS"].get("ISSUER_NAME", "?")
            ticker = payload["DETAILS"].get("ISSUER_TICKER", "?")

            if dry_run:
                status = "DRY_RUN"
                log(f"[D{deal_idx}/T{t_idx}/{n_tranches}] DRY RUN — {issuer} ({ticker})")
                log(json.dumps(payload, indent=2))
            else:
                status, resp_data = _publish(publish_url, pub_headers, payload, verify_ssl)
                if status == "ACK":
                    log(f"[D{deal_idx}/T{t_idx}/{n_tranches}] ACK — {issuer} ({ticker})")
                elif status == "NACK":
                    errs = resp_data.get("ERROR", [])
                    if errs:
                        reasons = [e.get("TEXT", str(e)) for e in errs]
                    else:
                        reasons = [str(resp_data)]
                    log(f"[D{deal_idx}/T{t_idx}/{n_tranches}] NACK ({len(reasons)} error(s)) — "
                        + reasons[0], "error")
                    for extra in reasons[1:]:
                        log(f"[D{deal_idx}/T{t_idx}/{n_tranches}]      also — {extra}", "error")
                else:
                    log(f"[D{deal_idx}/T{t_idx}/{n_tranches}] HTTP_ERROR — {resp_data}", "error")

            counts[status] = counts.get(status, 0) + 1

            if is_multi and t_idx == 1 and not stop_event.is_set():
                if dry_run:
                    master_loan_id = DRYRUN_MASTER_LOAN_ID
                elif status == "ACK":
                    log(f"Waiting {deal_query_wait}s before ALL_LOAN_DEAL query ...")
                    if not _interruptible_sleep(deal_query_wait, stop_event):
                        break
                    try:
                        master_loan_id = _fetch_master_loan_id(
                            deal_query_url, pub_headers, ticker, verify_ssl
                        )
                        log(f"Resolved MASTER_LOAN_ID={master_loan_id}")
                    except RuntimeError as exc:
                        log(f"Deal query failed: {exc} — skipping remaining tranches.", "error")
                        break
                else:
                    log(f"Tranche 1 returned {status}; skipping remaining tranches.", "error")
                    break

        if email_mode == "both" and not stop_event.is_set():
            emit_email(payloads, f"[D{deal_idx}]")

    log(f"Done — ACK={counts.get('ACK',0)}  NACK={counts.get('NACK',0)}  "
        f"HTTP_ERROR={counts.get('HTTP_ERROR',0)}  DRY_RUN={counts.get('DRY_RUN',0)}",
        "success" if counts.get("NACK", 0) == 0 and counts.get("HTTP_ERROR", 0) == 0 else "warn")
    log_queue.put({"type": "summary",
                   "success": counts.get("ACK", 0) + counts.get("DRY_RUN", 0),
                   "failed": counts.get("NACK", 0) + counts.get("HTTP_ERROR", 0),
                   "total": sum(counts.values())})
