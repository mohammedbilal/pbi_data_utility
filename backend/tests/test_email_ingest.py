"""Assertions for ``email_ingest`` (spec 19.4, 19.5 — build step A1).

Run it directly; no pytest, no new dependency:

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_email_ingest.py

Exits non-zero on the first failure count, so it is usable as a gate.

**This suite is committed on purpose.** Every earlier assertion suite in this
project (16, 50, 167, 224 assertions, per implementation.md) was written in a
scratchpad and deleted, which left every historical verification claim resting on
code that no longer exists. The corpus half skips cleanly when
``backend/expected/samples/`` is absent — that directory is gitignored, so a fresh
clone runs the unit half and reports the corpus half as skipped rather than failing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_ingest as ei  # noqa: E402

PASS = FAIL = SKIP = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n         got  {got!r}\n         want {want!r}")


def ok(label: str, cond: bool) -> None:
    check(label, bool(cond), True)


# ── check digits ──────────────────────────────────────────────────────────────
# The valid values are taken from the ten real samples. The invalid ones are built
# by mutating a real check digit at runtime rather than hardcoded, so they cannot
# accidentally be somebody's real identifier.

def _bad_digit(code: str) -> str:
    return code[:-1] + str((int(code[-1]) + 1) % 10)


print("check digits")
for isin in ("US90331HPV95", "US87165BBA08", "US65339KEF30", "US86562MEM82",
             "USU8531HAA87", "US449786BM36"):
    ok(f"ISIN {isin} valid", ei.isin_check(isin))
    ok(f"ISIN {isin} rejects a wrong digit", not ei.isin_check(_bad_digit(isin)))

for cusip in ("90331HPV9", "87165BBA0", "65339KEF3", "86562MEM8", "N45780DA3"):
    ok(f"CUSIP {cusip} valid", ei.cusip_check(cusip))
    ok(f"CUSIP {cusip} rejects a wrong digit", not ei.cusip_check(_bad_digit(cusip)))

ok("CINS (letter-prefixed) validates on the CUSIP algorithm", ei.cusip_check("N45780DA3"))
ok("wrong length is not a CUSIP", not ei.cusip_check("90331HPV"))
ok("wrong length is not an ISIN", not ei.isin_check("US90331HPV9"))
ok("empty is not an identifier", not ei.isin_check("") and not ei.cusip_check(""))

# The formatting break that started all this: a real CUSIP split by whitespace.
ok("'86562M EN6' validates once whitespace is squeezed", ei.cusip_check("86562M EN6"))
ok("'86562M EP1' validates once whitespace is squeezed", ei.cusip_check("86562M EP1"))


# ── false positives that the guards must keep out ─────────────────────────────
# Every string here was actually accepted by an earlier draft of the extractor,
# measured against the real samples. They are the regression tests that matter:
# a false identifier pairs an email to somebody else's deal (19.5).

print("false-positive guards")
for prose, why in (
    ("maturity date August 15, 2031", "prose words joined across whitespace"),
    ("and January 15, 2057 the notes", "'AND' + month, first two letters were a live country code"),
    ("settles 2029 May 20 in London", "three numeric-ish fragments"),
    ("repayment of indebtedness", "12-letter word starting with the country code IN"),
    ("with registration rights", "12-letter word starting with the country code RE"),
    ("global coordinating bank", "12-letter word starting with the country code CO"),
):
    found = ei.extract_identifiers(prose)
    check(f"no ISIN from {why}", found.isins, [])
    check(f"no CUSIP from {why}", found.cusips, [])
    check(f"nothing reported as rejected for {why}", found.rejected, [])

# Punctuation must not fuse: a tenor beside a spread is not an identifier.
check("T+120-125 beside 11NC10 yields nothing",
      ei.extract_identifiers("11NC10 FXD: T+120-125").isins, [])

# A FIGI is shaped like an ISIN beginning BB (Barbados) and must not leak into
# the ISIN lane on a lucky check digit.
figi = ei.extract_identifiers("Sec Figi BBG022CJ0BP6 for the tranche")
check("FIGI recognised as a FIGI", figi.figis, ["BBG022CJ0BP6"])
check("FIGI absent from the ISIN lane", figi.isins, [])


# ── the consumption regression ────────────────────────────────────────────────
# A whitespace-tolerant single regex pass consumed the text it scanned, so a match
# starting at the label 'ISIN' swallowed the first identifier on the line and
# finditer never revisited the overlapping start. Four of Format 8's five tranches
# were found and the fifth was silently lost — the worst failure mode available,
# because nothing reports a loss.

print("consumption regression (the silent loss)")
line = "ISIN US86562MEM82 US86562MEL00 US86562MEN65"
got = ei.extract_identifiers(line)
check("a labelled line yields every ISIN on it, including the first",
      got.isins, ["US86562MEL00", "US86562MEM82", "US86562MEN65"])
cus = "Cusip 86562MEM8 86562MEL0 86562M EN6"
check("a labelled CUSIP line yields all three, one of them whitespace-broken",
      ei.extract_identifiers(cus).cusips, ["86562MEL0", "86562MEM8", "86562MEN6"])


# ── derivation, tickers, structure ────────────────────────────────────────────
print("derivation and tickers")
d = ei.extract_identifiers("ISIN US90331HPV95 only")
ok("the CUSIP embedded in a US ISIN is derived", "90331HPV9" in d.cusips)
nd = ei.extract_identifiers("ISIN US90331HPV95 only", derive_cusip_from_isin=False)
check("derivation is switchable off", nd.cusips, [])

t = ei.extract_identifiers("Issuer/Ticker Sumitomo Mitsui Financial Group Inc ( SUMIBK )")
check("a parenthesised ticker is picked up", t.tickers, ["SUMIBK"])
rating = ei.extract_identifiers("Moody's (Exp): A2 / Stable, active BofA (B&D)")
check("(Exp) and (B&D) are not tickers", rating.tickers, [])

ok("matchable is true with an identifier", ei.extract_identifiers("US90331HPV95").matchable)
ok("matchable is false without one", not ei.extract_identifiers("no codes here").matchable)


# ── html -> text ──────────────────────────────────────────────────────────────
print("html to text")
cells = ei.html_to_text("<table><tr><td>USD Benchmark</td><td>USD Benchmark</td></tr></table>")
ok("adjacent cells do not run together", "BenchmarkUSD" not in cells)
ok("both cell values survive", cells.count("USD Benchmark") == 2)
check("entities are unescaped", ei.html_to_text("<p>3&nbsp;&amp;&nbsp;5</p>").replace("\xa0", " "),
      "3 & 5")
ok("script content is dropped",
   "alert" not in ei.html_to_text("<div>keep<script>alert(1)</script></div>"))
check("empty input is empty", ei.html_to_text(""), "")


# ── the real corpus ───────────────────────────────────────────────────────────
# Counts verified by hand against the ten files on 2026-08-10. Skipped when the
# gitignored samples directory is absent.

EXPECTED = {                    # file stem -> (isins, cusips, ticker present)
    "Format 1": (8, 8, True),   "Format 2": (3, 3, True),
    "Format 3": (1, 1, True),   "Format 4": (0, 0, False),
    "Format 5": (3, 3, True),   "Format 6": (1, 1, True),
    "Format 7": (10, 10, True), "Format 8": (5, 5, True),
    "Format 9": (4, 4, True),   "Format 10": (2, 2, False),
}

#: Overridable so the skip path is testable, and so a sample set kept outside the
#: repo can still be run against.
SAMPLES = Path(os.environ.get("PBI_SAMPLES_DIR")
               or Path(__file__).resolve().parent.parent / "expected" / "samples")
print("real corpus")
if not SAMPLES.is_dir() or not any(SAMPLES.glob("*.msg")):
    SKIP += 1
    print(f"  SKIP  {SAMPLES} absent (gitignored) — corpus assertions not run")
else:
    total_i = total_c = total_r = 0
    for stem, (n_isin, n_cusip, has_ticker) in EXPECTED.items():
        path = SAMPLES / f"{stem}.msg"
        if not path.exists():
            SKIP += 1
            print(f"  SKIP  {stem}.msg absent")
            continue
        parsed = ei.parse_email(path)
        ids = parsed.identifiers
        total_i += len(ids.isins)
        total_c += len(ids.cusips)
        total_r += len(ids.rejected)
        check(f"{stem}: ISIN count", len(ids.isins), n_isin)
        check(f"{stem}: CUSIP count", len(ids.cusips), n_cusip)
        check(f"{stem}: a ticker was found", bool(ids.tickers), has_ticker)
        check(f"{stem}: nothing rejected", ids.rejected, [])
        ok(f"{stem}: body was read", bool(parsed.body_text))
        ok(f"{stem}: every ISIN revalidates", all(ei.isin_check(x) for x in ids.isins))
        ok(f"{stem}: every CUSIP revalidates", all(ei.cusip_check(x) for x in ids.cusips))

    check("corpus total ISINs", total_i, 37)
    check("corpus total CUSIPs", total_c, 37)
    check("corpus rejects nothing", total_r, 0)

    # Format 4 states no identifier at all and must say so rather than inventing one.
    f4 = ei.parse_email(SAMPLES / "Format 4.msg")
    ok("Format 4 is flagged unmatchable", not f4.identifiers.matchable)
    ok("Format 4 warns that a manual pin is needed",
       any("manual pin" in w for w in f4.warnings))

    # Every sample must round-trip through the dict form the router will return.
    for stem in EXPECTED:
        path = SAMPLES / f"{stem}.msg"
        if path.exists():
            as_dict = ei.parse_email(path).as_dict()
            ok(f"{stem}: as_dict carries the identifier lanes",
               set(as_dict["identifiers"]) >= {"isins", "cusips", "tickers"})


# ── unsupported input ─────────────────────────────────────────────────────────
print("unsupported input")
try:
    ei.parse_email("nope.pst")
    check("a .pst raises ValueError", "no exception", "ValueError")
except ValueError as exc:
    ok("a .pst raises ValueError naming the accepted types", "accepted" in str(exc))
except Exception as exc:                                    # pragma: no cover
    check("a .pst raises ValueError", type(exc).__name__, "ValueError")

print()
print(f"passed {PASS} | failed {FAIL} | skipped {SKIP}")
sys.exit(1 if FAIL else 0)
