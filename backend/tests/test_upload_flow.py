"""Assertions for uploaded email sets — build steps A2, A3, B1 (spec 19.5–19.8).

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_upload_flow.py

No pytest. Committed, for the reason given in ``test_email_ingest.py``.

Three parts, each isolated from the things it should not need:

* **A2** goes through the real FastAPI app with ``TestClient`` and cleans up the
  run it creates. It needs the gitignored sample files and skips without them.
* **A3** asserts the *shape* of the SQL rather than hitting a database, so it runs
  anywhere and still proves the rails: SELECT-only, every value bound, a LIMIT, and
  no query at all when there is nothing to match on.
* **B1** is a pure function and needs nothing.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db_reader                              # noqa: E402
import expected_store                         # noqa: E402
from engines import expected_writer           # noqa: E402

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


# ── A3 · the identifier query (no database needed) ───────────────────────────
print("A3 · identifier query construction")
CFG = {"schema": "pbi", "table_prefix": "t_", "row_cap": 5000}

for table, kwargs, why in (
    ("issuance_security", dict(isins=["US90331HPV95"], cusips=["90331HPV9"]),
     "security matched on its own isin/cusip"),
    ("issuance", dict(isins=["US90331HPV95"], issuance_keys=["k1", "k2"]),
     "issuance matched by identifier or parent key"),
    ("issuance_data", dict(isins=["US90331HPV95"], deal_ids=["d1"]),
     "audit rows matched by identifier or deal_id"),
    ("issuance_deal", dict(deal_ids=["d1", "d2"]), "deal matched on first_deal_id"),
):
    sql, params = db_reader.build_identifier_select(table, CFG, **kwargs)
    ok(f"{why}: is a SELECT", sql.strip().upper().startswith("SELECT"))
    check(f"{why}: every value is bound", sql.count("%s"), len(params))
    ok(f"{why}: has a LIMIT", " LIMIT %s" in sql)
    ok(f"{why}: no write verb", not any(v in sql.upper() for v in
                                        (" INSERT", " UPDATE", " DELETE", " DROP",
                                         " ALTER", " TRUNCATE", " GRANT")))
    # The predicate only — `issuer_ticker` is an app column and so appears in the
    # SELECT list of three of the four tables. What must not appear is a ticker
    # *filter*, which is the predicate this path exists to avoid (19.8).
    predicate = sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""
    ok(f"{why}: no ticker filter", "issuer_ticker" not in predicate)

# The whole point of this path: neither predicate of the capture path appears.
sql, _ = db_reader.build_identifier_select("issuance", CFG, isins=["US90331HPV95"])
ok("no window predicate on the identifier path", "BETWEEN" not in sql.upper())

# Nothing to match on must produce no query rather than an unbounded scan.
check("no criteria yields no SQL",
      db_reader.build_identifier_select("issuance", CFG), ("", []))
check("blank and empty values are discarded, not bound",
      db_reader.build_identifier_select("issuance", CFG, isins=["", "  ", None]),
      ("", []))

# Duplicates collapse, and one value uses `=` rather than a one-element IN.
sql, params = db_reader.build_identifier_select(
    "issuance_security", CFG, isins=["US90331HPV95", "US90331HPV95"])
check("duplicate identifiers collapse", len(params), 2)      # 1 value + row cap
ok("a single value uses = not IN", "= %s" in sql and " IN (" not in sql)

# The deal table keys on first_deal_id — it has no deal_id of its own.
sql, _ = db_reader.build_identifier_select("issuance_deal", CFG, deal_ids=["d1"])
ok("deal keys on first_deal_id", "first_deal_id" in sql)
ok("deal is not matched on isin", "isin" not in sql)

# Asking with no identifiers at all is a clear refusal, not an empty fetch.
try:
    db_reader.fetch_rows_by_identifier(None, isins=[], cusips=[])
    check("empty identifier fetch raises", "no exception", "DbUnavailable")
except db_reader.DbUnavailable as exc:
    ok("empty identifier fetch explains itself", "pin an identifier" in str(exc))


# ── B1 · from_label ──────────────────────────────────────────────────────────
print("B1 · from_label")

# The real USB sample: ONE email, TWO legal issuers under one ticker, different
# formats and rankings (19.17). Multi-deal is normal here, not an edge case.
USB = {"deals": [
    {"deal": {"issuer_name": "US Bank NA/Cincinnati OH", "issuer_ticker": "USB",
              "use_of_proceeds": "General Corporate Purposes"},
     "tranches": [{"columns": {"currency_code": "USD", "coupon_type": "Fixed to Float",
                               "bond_seniority": "Senior Bank Note",
                               "regulation_subcategory": "3a2",
                               "preliminary_security_tenor": "3"},
                   "securities": [{"flavour": "144A", "isin": "US90331HPV95",
                                   "cusip": "90331HPV9"}]}]},
    {"deal": {"issuer_name": "US Bancorp", "issuer_ticker": "USB",
              "registration_type": "SEC Registered"},
     "tranches": [{"columns": {"currency_code": "USD", "is_esg": "false"},
                   "securities": [{"flavour": "144A", "isin": "US91159HJZ47"},
                                  {"flavour": "Reg S", "cusip": "90331HPW7"}]}]},
]}
r = expected_writer.from_label(USB, maps=MAPS)
check("two deals from one email", r["counts"]["deals"], 2)
check("two tranches", r["counts"]["tranches"], 2)
check("three security rows (one is a Reg S leg)", r["counts"]["securities"], 3)
check("a deal row per deal", len(r["rows"]["issuance_deal"]), 2)
check("a data row per tranche", len(r["rows"]["issuance_data"]), 2)
check("an issuance row per tranche", len(r["rows"]["issuance"]), 2)

data0 = r["rows"]["issuance_data"][0]
check("vocab translates the email's spelling", data0.get("regulation_subcategory"), "3(a)2")
check("prose maps to the app's code", data0.get("use_of_proceeds"),
      "GENERAL CORPORATE PURPOSES")
check("deal-grain values repeat onto the tranche row", data0.get("issuer_ticker"), "USB")

# t_issuance is a projection and lacks seven columns t_issuance_data has (18.2).
ok("issuer_name is written to issuance_data", "issuer_name" in data0)
ok("issuer_name is NOT written to issuance",
   "issuer_name" not in r["rows"]["issuance"][0])

secs = r["rows"]["issuance_security"]
check("security_type values are the app's two",
      sorted({s["security_type"] for s in secs}), ["144A", "REGS"])
check("registration_type on a security is its flavour",
      sorted({s["registration_type"] for s in secs}), ["144A", "Reg S"])
regs = [s for s in secs if s["security_type"] == "REGS"][0]
ok("a Reg S leg may carry a CUSIP and no ISIN",
   regs.get("cusip") and not regs.get("isin"))
ok("every security row has a match key", all(s.get("util_match_key") for s in secs))

# Metadata the store and the comparator need.
ok("deal seq is set on every row",
   all(row.get("util_deal_seq") for table in r["rows"] for row in r["rows"][table]))
ok("tranche seq is set on tranche rows",
   all(row.get("util_tranche_seq") for t in ("issuance_data", "issuance")
       for row in r["rows"][t]))

# D12: a blank field means the email did not state it, so it is excluded.
check("bool values coerce", r["rows"]["issuance_data"][1].get("is_esg"), False)
blank = expected_writer.from_label(
    {"deals": [{"deal": {"issuer_ticker": "AAA", "issuer_name": ""},
                "tranches": [{"columns": {"currency_code": "USD",
                                          "ipts": None, "bond_seniority": "  "}}]}]},
    maps=MAPS)
ok("a blank string is not written", "issuer_name" not in blank["rows"]["issuance_deal"][0])
ok("None is not written", "ipts" not in blank["rows"]["issuance_data"][0])
ok("whitespace is not written",
   "bond_seniority" not in blank["rows"]["issuance_data"][0])
ok("a blank column is absent from rendered_fields",
   "ipts" not in blank["rendered_fields"])

# 19.7: rendered_fields is the scoring denominator, so it must be exactly what
# was filled — no more (that would penalise the agent for silence) and no less.
ok("rendered_fields carries what was stated",
   {"currency_code", "coupon_type", "bond_seniority", "issuer_ticker"}
   <= set(r["rendered_fields"]))
ok("rendered_fields excludes what was not stated",
   "day_count" not in r["rendered_fields"])

# The tranche count is the one derived value a label can be trusted for.
check("db_number_of_tranches comes from the label's own shape",
      r["rows"]["issuance_deal"][0].get("db_number_of_tranches"), 1)

# A column that is not in the field map is reported, never silently dropped.
junk = expected_writer.from_label(
    {"deals": [{"deal": {"issuer_ticker": "AAA", "not_a_real_column": "x"},
                "tranches": []}]}, maps=MAPS)
ok("an unknown column is warned about",
   any("not_a_real_column" in w for w in junk["warnings"]))
ok("an unknown column is not written",
   "not_a_real_column" not in junk["rows"]["issuance_deal"][0])

# A free-text value outside a closed vocabulary is kept and flagged, so a wrong
# expectation announces itself instead of quietly scoring the agent down.
odd = expected_writer.from_label(
    {"deals": [{"deal": {"issuer_ticker": "AAA", "registration_type": "3a2"},
                "tranches": []}]}, maps=MAPS)
ok("an off-vocabulary value is flagged",
   any("vocab_map" in w for w in odd["warnings"]))
check("an off-vocabulary value is kept as stated",
      odd["rows"]["issuance_deal"][0].get("registration_type"), "3a2")

check("an empty label is refused politely",
      expected_writer.from_label({}, maps=MAPS)["counts"]["deals"], 0)
ok("from_label is pure — the store was never touched",
   expected_writer.from_label(USB, maps=MAPS)["counts"] == r["counts"])


# ── A2 · the upload endpoint (needs the sample files) ────────────────────────
print("A2 · upload endpoint")
if not SAMPLES.is_dir() or not any(SAMPLES.glob("*.msg")):
    SKIP += 1
    print(f"  SKIP  {SAMPLES} absent (gitignored) — endpoint assertions not run")
else:
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    paths = sorted(SAMPLES.glob("*.msg"), key=lambda p: (len(p.name), p.name))
    payload = [("files", (p.name, p.read_bytes(), "application/vnd.ms-outlook"))
               for p in paths]
    run_id = None
    try:
        resp = client.post("/api/compare/uploads", files=payload,
                           data={"label": "test_upload_flow", "asset_class": "bonds"})
        check("upload returns 200", resp.status_code, 200)
        body = resp.json()
        run_id = body.get("util_run_id")
        ok("a run id comes back", bool(run_id))
        check("one manifest entry per file", len(body["emails"]), len(paths))
        ok("most emails are matchable", body["matchable"] >= len(paths) - 2)
        ok("nothing is labelled yet", body["labelled"] == 0)

        # The marker that avoided a schema change (19.5).
        listed = client.get("/api/compare/runs?asset_class=bonds").json()["runs"]
        mine = [x for x in listed if x["util_run_id"] == run_id][0]
        check("the run is marked as an upload", mine["tool"], "upload")
        check("email_format says uploaded", mine["email_format"], "uploaded")
        check("emails are counted", mine["emails"], len(paths))
        check("no actual rows yet", mine["has_actual"], False)

        detail = client.get(f"/api/compare/runs/{run_id}").json()
        check("detail lists the emails", len(detail["emails"]), len(paths))
        check("nothing is rendered yet", detail["rendered_fields"], [])
        manifest = detail["run"]["params"]["upload"]["emails"]
        check("the manifest survives the round trip", len(manifest), len(paths))
        ok("the manifest carries identifiers",
           any(m["identifiers"]["isins"] for m in manifest))
        ok("saved files are on disk",
           len(list(Path(detail["run"]["params"]["upload"]["dir"]).glob("*"))) == len(paths))

        # Every email keeps its own sent date, not the time of upload.
        ok("emails keep their own timestamps",
           all(e.get("ts") for e in detail["emails"]))

        # An unlabelled run has nothing to score, and must say so rather than
        # reporting 0% (19.5).
        cmp_resp = client.post(f"/api/compare/runs/{run_id}/compare", json={})
        ok("comparing an unlabelled run is refused", cmp_resp.status_code >= 400)
        ok("the refusal explains itself",
           "nothing to compare" in cmp_resp.text.lower()
           or "no expected rows" in cmp_resp.text.lower())

        # A file type the parser does not accept is reported, not fatal.
        mixed = client.post("/api/compare/uploads",
                            files=[("files", ("notes.pst", b"x", "application/octet-stream")),
                                   ("files", (paths[0].name, paths[0].read_bytes(),
                                              "application/vnd.ms-outlook"))],
                            data={"asset_class": "bonds"})
        check("a mixed upload still succeeds", mixed.status_code, 200)
        mixed_body = mixed.json()
        check("only the readable file is kept", len(mixed_body["emails"]), 1)
        ok("the skipped file is reported",
           any("unsupported" in w for w in mixed_body["warnings"]))
        client.delete(f"/api/compare/runs/{mixed_body['util_run_id']}")

        # Uploading nothing readable at all is an error, not an empty run.
        bad = client.post("/api/compare/uploads",
                          files=[("files", ("x.pst", b"x", "application/octet-stream"))],
                          data={"asset_class": "bonds"})
        ok("an entirely unreadable upload is rejected", bad.status_code >= 400)

        # ── A4 · the contract the tab reads ──────────────────────────────
        # `npm run build` proves the JSX compiles; it cannot prove the component
        # reads a path the API actually returns. These assert the exact shapes
        # EmailCompareTab.jsx destructures, so a rename on either side fails here
        # instead of rendering an empty panel.
        print("A4 · frontend data contract")
        ok("run list exposes `tool` for the upload marker", "tool" in mine)
        ok("run list exposes `emails` count", "emails" in mine)
        ok("run list exposes `notes` for the row title", "notes" in mine)
        ok("detail exposes run.params.upload.emails",
           isinstance(detail.get("run", {}).get("params", {})
                      .get("upload", {}).get("emails"), list))
        entry = manifest[0]
        for key in ("util_email_id", "seq", "subject", "sent_at", "identifiers",
                    "matchable"):
            ok(f"manifest entry has `{key}`", key in entry)
        for key in ("isins", "cusips", "tickers"):
            ok(f"manifest identifiers have `{key}`", key in entry["identifiers"])
        stored_email = detail["emails"][0]
        for key in ("util_email_id", "subject", "body_html", "send_note",
                    "rendered_fields"):
            ok(f"stored email row has `{key}`", key in stored_email)
        # The manifest and the stored rows are joined on util_email_id in the tab.
        ok("manifest ids join to the stored email rows",
           {m["util_email_id"] for m in manifest}
           == {e["util_email_id"] for e in detail["emails"]})
        ok("a body is present to render in the iframe", bool(stored_email["body_html"]))
        check("rendered_fields is a list (empty until labelled)",
              stored_email["rendered_fields"], [])
        # Deleting a run must take its saved files with it. It did not, and 41
        # orphaned upload directories accumulated in a single session of testing —
        # the store knows nothing about the filesystem, so nothing was cleaning up.
        upload_dir = Path(detail["run"]["params"]["upload"]["dir"])
        ok("the upload directory exists while the run does", upload_dir.is_dir())
    finally:
        if run_id:
            body = client.delete(f"/api/compare/runs/{run_id}").json()
            deleted = body["deleted"]
            check("cleanup removed the emails", deleted["util_email"], len(paths))
            check("cleanup removed the run", deleted["util_run"], 1)
            ok("cleanup removed the saved files too", body.get("files_removed", 0) > 0)
            ok("...and the directory itself",
               not (Path(__file__).resolve().parent.parent / "expected" / "uploads"
                    / run_id).exists())

print()
print(f"passed {PASS} | failed {FAIL} | skipped {SKIP}")
sys.exit(1 if FAIL else 0)
