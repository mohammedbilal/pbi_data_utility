"""Assertions for the labelling form — build step B2 (spec 19.6, 19.23).

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_label_form.py

No pytest. The prefill section is mostly **regressions**: every value asserted there
is one an earlier draft got wrong against the ten real samples.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_ingest                      # noqa: E402
import expected_store                    # noqa: E402
import label_form                        # noqa: E402
from engines import expected_writer      # noqa: E402

PASS = FAIL = SKIP = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n         got  {got!r}\n         want {want!r}")


def ok(label, cond):
    check(label, bool(cond), True)


MAPS = expected_store.effective_map("bonds")
SAMPLES = Path(os.environ.get("PBI_SAMPLES_DIR")
               or Path(__file__).resolve().parent.parent / "expected" / "samples")


# ── the form model ───────────────────────────────────────────────────────────
print("form model")
groups = label_form.build_form(MAPS)
by_grain = {g["grain"]: g for g in groups}
check("three grains", sorted(by_grain), ["deal", "security", "tranche"])
total = sum(len(g["fields"]) for g in groups)
ok(f"the default form is short enough to fill ({total} fields)", 20 <= total <= 45)

tranche = {f["column"]: f for f in by_grain["tranche"]["fields"]}
deal = {f["column"]: f for f in by_grain["deal"]["fields"]}

# Closed vocabularies must be pick-lists, or normalization is in play (19.6).
check("registration_type offers the app's eight values",
      deal["registration_type"]["options"],
      ["144A", "144A/Reg S", "Global/IPO", "Other", "Reg S", "Sec Exempt",
       "Sec Registered", "Unknown"])
ok("use_of_proceeds is a pick-list", len(deal["use_of_proceeds"]["options"]) >= 3)
ok("esg_type is a pick-list", bool(tranche["esg_type"]["options"]))
ok("a free-text field has no options", not tranche["ipts"]["options"])
check("is_perpetual is a bool", tranche["is_perpetual"]["normalizer"], "bool")

# 19.6: a matching key must be on the form even though it is not scored, or
# pairing degrades silently.
ok("preliminary_security_tenor is present", "preliminary_security_tenor" in tranche)
ok("...and is flagged as pairing-only, not scored",
   tranche["preliminary_security_tenor"]["matching_only"]
   and not tranche["preliminary_security_tenor"]["scored"])
ok("scored fields are marked scored", tranche["coupon_type"]["scored"])

# Hints exist where real emails use different words than the columns do (19.18).
ok("coupon_type carries a hint", bool(tranche["coupon_type"]["hint"]))
ok("is_perpetual carries a hint", bool(tranche["is_perpetual"]["hint"]))
ok("length limits are exposed", (deal["issuer_name"]["max_length"] or 0) > 0)

full = label_form.build_form(MAPS, full=True)
ok("`show all` offers more fields than the default",
   sum(len(g["fields"]) for g in full) > total)
ok("`show all` keeps the curated fields first",
   [f["column"] for f in full[0]["fields"]][:3]
   == list(label_form.DEFAULT_FIELDS["deal"])[:3])


# ── prefill: the defects found against the real samples ──────────────────────
print("prefill regressions")

# The rating regex ended in \b, so after matching `A+` the position sat between
# two non-word characters and the match backtracked to a bare `A` — silently
# dropping every +/- suffix.
r = label_form._interpret("issuer_rating",
                          "Moody's (Exp): A2/ Stable S&P (Exp): A+/ Stable "
                          "Fitch (Exp): AA-/ Stable", [])
vals = dict(r)
check("rating suffixes survive", vals.get("issuer_rating"), "A2/A+/AA-")
check("the outlook is split out", vals.get("rating_outlook"), "Stable")

# A size row carries the currency as well as (or instead of) an amount. The
# currency is the part that matters most: it is one of the three components of
# matching key 2, and without it an expected row cannot be paired at all.
sized = dict(label_form._interpret("total_issued_amount", "USD", []))
check("a size with no digits yields no amount", sized.get("total_issued_amount"), None)
check("...but does yield the currency", sized.get("currency_code"), "USD")
notes = []
placeholder = dict(label_form._interpret("total_issued_amount", "USD Benchmark", notes))
check("a placeholder yields no amount", placeholder.get("total_issued_amount"), None)
check("...but still yields the currency", placeholder.get("currency_code"), "USD")
ok("...and says why there is no amount", any("placeholder" in n for n in notes))
check("a symbol is read as its ISO code",
      dict(label_form._interpret("total_issued_amount", "C$ Benchmark", [])
           ).get("currency_code"), "CAD")
check("C$ is not mistaken for USD",
      dict(label_form._interpret("total_issued_amount", "C$ 500mm", [])
           ).get("currency_code"), "CAD")
check("a real amount is kept",
      dict(label_form._interpret("total_issued_amount", "$500mm", [])).get(
          "total_issued_amount"), "$500mm")

# The Format row has two possible destinations, decided by the value (19.19.2).
check("3a2 fills the sub-category, not the registration type",
      label_form._interpret("registration_type", "3a2", []),
      [("regulation_subcategory", "3(a)2")])
check("SEC Registered fills the registration type",
      label_form._interpret("registration_type", "SEC Registered", []),
      [("registration_type", "Sec Registered")])
check("a hyphenated spelling still maps",
      label_form._interpret("registration_type", "SEC-Registered", []),
      [("registration_type", "Sec Registered")])
check("a compound format splits across both columns",
      sorted(label_form._interpret("registration_type",
                                   "144A/REGS with Registation Rights", [])),
      [("registration_type", "144A/Reg S"),
       ("regulation_subcategory", "With Reg Rights")])

# The B&D marker must come off the list, or list_set reads the same bank twice.
bd = dict(label_form._interpret("bookrunners", "Active: USB(B&D), JPM, RBCCM", []))
check("the B&D marker is stripped from the list", bd.get("bookrunners"), "USB, JPM, RBCCM")
check("the B&D bank gets its own column", bd.get("bnd_bank"), "USB")

# Perpetual: no date, and the flag instead (19.18).
check("Perpetual sets the flag, not a date",
      label_form._interpret("maturity_date", "Perpetual", []), [("is_perpetual", True)])
check("a real maturity is a date",
      dict(label_form._interpret("maturity_date", "May 20, 2029", [])).get("maturity_date"),
      "May 20, 2029")

# `3-NC2` is a 3-year tranche, non-call 2 — the non-call part has no scored column.
nc = dict(label_form._interpret("preliminary_security_tenor", "3-NC2", []))
check("a callable tenor yields its years", nc.get("preliminary_security_tenor"), "3")
check("...and implies a call", nc.get("call_indicator"), True)

# The app's own rule (19.18): the phrase anywhere wins.
check("use of proceeds follows the app's rule",
      dict(label_form._interpret(
          "use_of_proceeds",
          "General Corporate Purposes and an amount equivalent to the net proceeds "
          "will finance the Eligible Green Loan Portfolio", [])).get("use_of_proceeds"),
      "GENERAL CORPORATE PURPOSES")
notes = []
label_form._interpret("use_of_proceeds", "To repay commercial paper", notes)
ok("an unmatched use of proceeds asks the human", any("choose" in n for n in notes))

check("issuer and ticker split out of one cell",
      sorted(label_form._interpret("issuer_name", "US Bancorp ( USB )", [])),
      [("issuer_name", "US Bancorp"), ("issuer_ticker", "USB")])


# ── prefill against the real corpus ──────────────────────────────────────────
print("prefill on the real emails")
if not SAMPLES.is_dir() or not any(SAMPLES.glob("*.msg")):
    SKIP += 1
    print(f"  SKIP  {SAMPLES} absent (gitignored)")
else:
    # Tranche counts the prefill should reach. F8 is the deliberately messy sample
    # whose tranche values are space-separated on one line with spaces inside the
    # values — unsplittable by any rule, which is the whole argument for D10. It is
    # expected to under-count, and that is recorded rather than hidden.
    EXPECTED_TRANCHES = {"Format 1": 8, "Format 2": 3, "Format 3": 1, "Format 4": 5,
                         "Format 5": 3, "Format 6": 1, "Format 7": 5, "Format 8": 1,
                         "Format 9": 2, "Format 10": 2}
    for stem, want in EXPECTED_TRANCHES.items():
        path = SAMPLES / f"{stem}.msg"
        if not path.exists():
            SKIP += 1
            continue
        parsed = email_ingest.parse_email(path)
        s = label_form.suggest(parsed.body_html, parsed.body_text,
                               parsed.identifiers.as_dict())
        check(f"{stem}: tranches found", len(s["tranches"]), want)
        ok(f"{stem}: suggestions are a dict of columns", isinstance(s["deal"], dict))
        # Nothing may be suggested for a column that is not on the form.
        known = {f["column"] for g in label_form.build_form(MAPS, full=True)
                 for f in g["fields"]}
        stray = ({k for k in s["deal"]} | {k for t in s["tranches"] for k in t}) - known
        check(f"{stem}: no suggestion outside the form", sorted(stray), [])

    # The multi-issuer email must say so rather than merging two deals silently.
    usb = email_ingest.parse_email(SAMPLES / "Format 2.msg")
    s = label_form.suggest(usb.body_html, usb.body_text, usb.identifiers.as_dict())
    ok("the two-issuer email warns that it is more than one deal",
       any("more than" in n and "one deal" in n for n in s["notes"]))
    check("its ticker is picked up", s["deal"].get("issuer_ticker"), "USB")
    ok("its ratings keep their suffixes", "+" in (s["deal"].get("issuer_rating") or ""))

    # Everything the prefill suggests must survive projection — a suggestion the
    # writer would reject is worse than no suggestion.
    for stem in ("Format 1", "Format 2", "Format 7"):
        path = SAMPLES / f"{stem}.msg"
        if not path.exists():
            continue
        parsed = email_ingest.parse_email(path)
        s = label_form.suggest(parsed.body_html, parsed.body_text,
                               parsed.identifiers.as_dict())
        built = expected_writer.from_label(
            label_form.label_from_form(s["deal"], s["tranches"], []), maps=MAPS)
        ok(f"{stem}: the suggestion projects cleanly",
           built["counts"]["tranches"] == len(s["tranches"]))
        ok(f"{stem}: no unknown-column warning",
           not any("not a column" in w for w in built["warnings"]))
        ok(f"{stem}: it yields scoreable fields", len(built["rendered_fields"]) >= 5)

# ── the perfect-agent round trip ─────────────────────────────────────────────
# Label the emails, export the expected lane, import it straight back as `actual`,
# and compare. A perfect agent must score 100%: anything less is the harness's own
# fault, not the agent's.
#
# This is the single most valuable assertion here. The first version of it scored
# **5%** — row matching was failing on 20 of 22 rows because nothing suggested a
# currency, and currency is one of the three parts of matching key 2 (18.7). Without
# this test the first real comparison would have reported ~5% and looked like a
# catastrophically bad agent.
print("perfect-agent round trip")
if not SAMPLES.is_dir() or not any(SAMPLES.glob("*.msg")):
    SKIP += 1
    print("  SKIP  samples absent")
else:
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    paths = sorted(SAMPLES.glob("*.msg"), key=lambda p: (len(p.name), p.name))[:3]
    run_id = None
    try:
        run_id = client.post(
            "/api/compare/uploads",
            files=[("files", (p.name, p.read_bytes(), "application/vnd.ms-outlook"))
                   for p in paths],
            data={"asset_class": "bonds"}).json()["util_run_id"]

        detail = client.get(f"/api/compare/runs/{run_id}").json()
        for entry in detail["run"]["params"]["upload"]["emails"]:
            email_id = entry["util_email_id"]
            form = client.get(
                f"/api/compare/uploads/{run_id}/emails/{email_id}/label").json()
            s = form["suggestions"]
            label = {"deals": [{
                "deal": s["deal"],
                "tranches": [{"columns": t, "securities": []} for t in s["tranches"]],
            }]}
            saved = client.put(
                f"/api/compare/uploads/{run_id}/emails/{email_id}/label",
                json={"label": label})
            check(f"{entry['file']}: label saved", saved.status_code, 200)

        # Every labelled tranche must carry the parts of a matching key, or the
        # rows cannot be paired no matter how good the agent is.
        rows = client.get(f"/api/compare/export/{run_id}"
                          f"?format=csv&lane=expected&table=issuance_data").text
        ok("expected rows carry a currency", "currency_code" in rows.split("\n")[0])

        uploads = []
        for table in ("issuance_deal", "issuance_data", "issuance", "issuance_security"):
            csv_text = client.get(f"/api/compare/export/{run_id}"
                                  f"?format=csv&lane=expected&table={table}").text
            if csv_text.strip():
                uploads.append(("files", (f"{table}.csv", csv_text.encode(), "text/csv")))
        imported = client.post(f"/api/compare/runs/{run_id}/import",
                               files=uploads, data={"lane": "actual"}).json()
        ok("the expected lane re-imports as actual", imported["total"] > 0)

        result = client.post(f"/api/compare/runs/{run_id}/compare", json={}).json()
        scores = result["scores"]
        check("a perfect agent scores 100%", scores["accuracy"], 1.0)
        check("every row pairs", scores["rows_missing"], 0)
        check("no row is unexpected", scores["rows_unexpected"], 0)
        check("no field mismatches", scores["verdicts"]["mismatch"], 0)
        check("no field is missing", scores["verdicts"]["missing"], 0)
        ok("fields were actually compared", scores["fields_matched"] > 50)
        # The denominator is the labelled set, not all 141 compared columns (19.7).
        ok("the denominator is the labelled fields only",
           scores["fields_compared"] <= scores["fields_matched"] * 3)

        # Coverage must not be scored as accuracy (18.7). Every field of an
        # expected row that paired with nothing is trivially `missing`, so counting
        # them says "the agent is bad" when the truth is "the agent never saw this".
        # Measured on real agent output: 27.5% with them counted, 54.6% without.
        check("a perfect run has no unmatched-row fields",
              scores["fields_in_unmatched_rows"], 0)

        # ── B5 · the provenance split (D17) ──────────────────────────────
        split = scores.get("by_provenance") or {}
        check("the score is split three ways", sorted(split),
              ["derived", "extracted", "stamped"])
        ok("extracted is the largest bucket",
           split["extracted"]["compared"] > split["derived"]["compared"])
        ok("the buckets sum to the headline",
           sum(b["compared"] for b in split.values()) == scores["fields_compared"])
        ok("a perfect agent is perfect in every bucket",
           all(b["accuracy"] in (None, 1.0) for b in split.values()))

        diff = client.get(f"/api/compare/runs/{run_id}/diff").json()
        ok("every diff row carries a provenance",
           all(r.get("provenance") in ("extracted", "derived", "stamped")
               for r in diff["rows"]))
        ok("derived fields are actually tagged as such",
           any(r["provenance"] == "derived" for r in diff["rows"]))
        # A tranche-level registration_type is derived by the app, confirmed by the
        # user — the canonical example of a field a prompt change cannot move.
        reg = [r for r in diff["rows"]
               if r.get("column_name") == "registration_type"
               and r.get("table") in ("issuance", "issuance_data")]
        if reg:
            check("tranche registration_type reads as derived",
                  reg[0]["provenance"], "derived")

        # ── B4 · labels survive the store ────────────────────────────────
        # Export, delete the run entirely, upload the same emails again, restore.
        # The score must come back identical, or the export is not a backup.
        document = client.get(f"/api/compare/uploads/{run_id}/labels").json()
        check("every labelled email is exported", len(document["labels"]), len(paths))
        ok("the export is versioned", document.get("version") == 1)
        ok("entries match on identifiers, not row ids",
           any((e["match"]["isins"] or e["match"]["cusips"]) for e in document["labels"]))

        client.delete(f"/api/compare/runs/{run_id}")
        run_id = client.post(
            "/api/compare/uploads",
            files=[("files", (p.name, p.read_bytes(), "application/vnd.ms-outlook"))
                   for p in paths],
            data={"asset_class": "bonds"}).json()["util_run_id"]

        restored = client.post(f"/api/compare/uploads/{run_id}/labels/import",
                               json=document).json()
        check("every label is placed in the new run", len(restored["applied"]), len(paths))
        check("nothing is skipped", restored["skipped"], [])

        uploads = []
        for table in ("issuance_deal", "issuance_data", "issuance", "issuance_security"):
            csv_text = client.get(f"/api/compare/export/{run_id}"
                                  f"?format=csv&lane=expected&table={table}").text
            if csv_text.strip():
                uploads.append(("files", (f"{table}.csv", csv_text.encode(), "text/csv")))
        client.post(f"/api/compare/runs/{run_id}/import",
                    files=uploads, data={"lane": "actual"})
        after = client.post(f"/api/compare/runs/{run_id}/compare", json={}).json()["scores"]
        check("the restored run scores identically", after["accuracy"], scores["accuracy"])
        check("...over the same number of fields",
              after["fields_compared"], scores["fields_compared"])
        check("...with the same matches", after["fields_matched"], scores["fields_matched"])

        # An entry that belongs to no email here must be reported, not dropped.
        stray = {"version": 1, "labels": [{
            "file": "nowhere.msg", "subject": "not in this run",
            "match": {"isins": ["US0000000000"], "cusips": []},
            "label": {"deals": [{"deal": {"issuer_ticker": "ZZZ"}, "tranches": []}]}}]}
        odd = client.post(f"/api/compare/uploads/{run_id}/labels/import",
                          json=stray).json()
        check("an unplaceable label is reported", len(odd["skipped"]), 1)
        check("...and nothing is applied", odd["applied"], [])

        # An upload run's expected lane may be imported (B4); a capture run's may not.
        refused = client.post(f"/api/compare/runs/{run_id}/import",
                              files=[("files", ("x.csv", b"issuer_ticker\nAAA\n",
                                                "text/csv"))],
                              data={"lane": "nonsense"})
        ok("an unknown lane is refused", refused.status_code >= 400)
    finally:
        if run_id:
            client.delete(f"/api/compare/runs/{run_id}")


# ── coverage is not accuracy (18.7) ──────────────────────────────────────────
# Two expected rows, one of which the application never created. Every field of the
# unpaired row is trivially `missing`; counting them would report a coverage gap as
# an extraction failure. Measured on real agent output before this was fixed: a run
# of ten emails of which the agent had processed two scored 27.5%, and 54.6% once the
# eight it never saw stopped counting.
print("coverage is not accuracy")
EXPECTED = {
    "issuance_data": [
        {"issuer_ticker": "AAA", "currency_code": "USD",
         "preliminary_security_tenor": "5", "coupon_type": "Fixed",
         "bond_seniority": "Sr Unsecured Note", "util_deal_seq": 1,
         "util_tranche_seq": 1, "util_match_key": "AAA|USD|5"},
        {"issuer_ticker": "AAA", "currency_code": "USD",
         "preliminary_security_tenor": "10", "coupon_type": "Fixed",
         "bond_seniority": "Sr Unsecured Note", "util_deal_seq": 1,
         "util_tranche_seq": 2, "util_match_key": "AAA|USD|10"},
    ],
    "issuance": [], "issuance_deal": [], "issuance_security": [],
}
ACTUAL = {
    "issuance_data": [
        {"issuer_ticker": "AAA", "currency_code": "USD",
         "preliminary_security_tenor": "5", "coupon_type": "Fixed",
         "bond_seniority": "Sr Unsecured Note"},
    ],
    "issuance": [], "issuance_deal": [], "issuance_security": [],
}
RENDERED = ["issuer_ticker", "currency_code", "coupon_type", "bond_seniority",
            "preliminary_security_tenor"]

import comparators                                     # noqa: E402
res = comparators.compare_run(EXPECTED, ACTUAL, maps=MAPS, rendered_fields=RENDERED)
sc = res["scores"]
check("the matched row still scores perfectly", sc["accuracy"], 1.0)
ok("the unmatched row's fields are excluded", sc["fields_in_unmatched_rows"] > 0)
check("and it is reported as an unmatched row", sc["rows_missing"], 1)
check("one row did match", sc["rows_matched"], 1)
# compare_run returns findings already converted to dicts.
_findings = [f if isinstance(f, dict) else f.as_dict() for f in res["findings"]]
ok("the diff still shows the unmatched row's fields",
   any(not f["row_matched"] for f in _findings))
ok("every finding says whether its row matched",
   all("row_matched" in f for f in _findings))

# Without the fix this scored well under 100% purely because of the second row.
ok("all matched-row findings agree",
   all(f["verdict"] == "match" for f in _findings if f["row_matched"]))

# Month-name dates — what every real email prints. Before this, an expectation
# stated in the email's own words fell back to text and could never equal the
# epoch-milliseconds the app stores: both date columns scored 0 of 11 on the first
# real comparison, reading as an agent that cannot get a single date right. Fixing
# the normalizer rather than the prefill is deliberate — a hand-typed date must work.
print("month-name dates")
import comparators as _C
for text, want in (("May 20, 2029", "2029-05-20"), ("Jul 07, 2032", "2032-07-07"),
                   ("June 12, 2029", "2029-06-12"), ("12 June 2029", "2029-06-12"),
                   ("Sept. 1, 2031", "2031-09-01"), ("1st October 2031", "2031-10-01")):
    check(f"{text!r} parses", str(_C.normalize(text, "date_day").value), want)
ok("a non-date still falls back to text",
   isinstance(_C.normalize("not a date", "date_day").value, str))
# The whole point: the email's wording must equal the application's epoch value.
_iso = _C.normalize("2029-05-20", "date_day")
_named = _C.normalize("May 20, 2029", "date_day")
ok("the email's wording equals the ISO form", _C.canon_equal(_named, _iso))

print()
print(f"passed {PASS} | failed {FAIL} | skipped {SKIP}")
sys.exit(1 if FAIL else 0)
