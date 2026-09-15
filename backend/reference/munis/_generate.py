"""One-off builder for `backend/reference/munis/` — spec §21.5.

    cd backend
    .\\.venv\\Scripts\\python.exe reference\\munis\\_generate.py

Deterministic, idempotent, **checked-in output**. Like the securitized builder it is
*never called at runtime* — the engine only reads the CSVs, and every file is optional
with a hardcoded fallback.

Sources
-------
* ``master/LIST_LOOKUP_TROWE-209_MUNIS_INSERT.csv`` — the platform's own drop-down
  lists. Only four of them exist (Munis Deal Status / Tax Status / Sector / Series
  Status); they are transcribed **verbatim** and are the only values in
  `vocabularies.csv` marked ``SOURCE=lookup``.
* ``master/MUNIS_DATA.csv`` — 95 real deals / 237 series / 2,142 maturities. Every
  other vocabulary, and the whole of `issuers.csv`, is *observed frequency* from this
  file, so the generator reproduces the real joint distribution rather than a uniform
  draw over a guessed list. Rows marked ``SOURCE=observed``.

The two sources disagree in places and the disagreement is data, not a bug to fix
(§21.5.4): the lookup spells it ``Tax-Exempt`` while 1,408 of 1,421 observed rows spell
it ``Tax Exempt``, and the lookup's ``General Purposes`` is ``General Purpose`` in the
data. The engine emits the **lookup** spelling — it is what the platform validates
against — so the observed spellings are folded into it here, preserving the weights.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER = HERE.parent.parent.parent / "master"
LOOKUP = MASTER / "LIST_LOOKUP_TROWE-209_MUNIS_INSERT.csv"
DATA = MASTER / "MUNIS_DATA.csv"

NULL = {"NULL", "", "None"}

# The lookup's spelling wins on the wire; the data's spelling is the alias (§21.5.4).
CANON = {
    "Tax Exempt": "Tax-Exempt",
    "General Purpose": "General Purposes",
    "Fixed Rate": "Fixed",
}

_NOISE = re.compile(r"\s+")


def canon(v: str) -> str:
    return CANON.get(v, v)


def clean(v: str) -> str:
    return "" if v is None or v.strip() in NULL else v.strip()


def rows(path: Path) -> list:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write(name: str, header: list, body: list) -> None:
    with (HERE / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(body)
    print("  %-22s %5d rows" % (name, len(body)))


# -- vocabularies --------------------------------------------------------------

LOOKUP_FIELDS = {
    "Munis Deal Status": "DEAL_STATUS",
    "Munis Tax Status": "TAX_STATUS",
    "Munis Sector": "SECTOR",
    "Munis Series Status": "SERIES_STATUS",
}

OBSERVED_FIELDS = {
    "DEAL_TYPE": "DEAL_TYPE",
    "WIRE_TYPE": "WIRE_TYPE",
    "SERIES_MONEY_TYPE": "SERIES_MONEY_TYPE",
    "SOURCE_OF_REPAYMENT": "SOURCE_OF_REPAYMENT",
    "INTEREST_TYPE": "INTEREST_TYPE",
    "ENHANCEMENT": "ENHANCEMENT",
    "SECURITY_TYPE": "SECURITY_TYPE",
    "PURPOSE": "PURPOSE",
    "STATE": "STATE",
    "MULTIPLES": "MULTIPLES",
    "RATING_OUTLOOK": "RATING_MOODYS_OUTLOOK",
}


def build_vocabularies(data: list) -> None:
    """The four platform lists verbatim, plus observed-frequency lists for the rest."""
    out = []

    # The export is one quoted blob with literal \n separators and a [34]{...} header.
    body = LOOKUP.read_text(encoding="utf-8-sig").strip().strip('"')
    seen_lookup = set()
    for ln in body.split("\\n"):
        parts = ln.split(",")
        if len(parts) < 4 or ln.startswith("["):
            continue
        field = LOOKUP_FIELDS.get(parts[0].strip())
        value = parts[2].strip()
        if not field or not value:  # the blank member of Tax Status / Sector is dropped
            continue
        seen_lookup.add((field, value))
        out.append([field, value, parts[1].strip(), 10, "lookup"])

    for field, col in OBSERVED_FIELDS.items():
        counts = Counter(canon(clean(r[col])) for r in data if clean(r[col]))
        for order, (value, n) in enumerate(counts.most_common()):
            if (field, value) not in seen_lookup:
                out.append([field, value, order, n, "observed"])

    write("vocabularies.csv", ["FIELD", "VALUE", "LIST_ORDER", "WEIGHT", "SOURCE"], out)


# -- issuers -------------------------------------------------------------------

def build_issuers(data: list) -> None:
    """One row per real deal — the join that keeps issuer-dependent fields consistent.

    Name, state, sector, purpose, tax status, ratings and repayment source travel
    together because in the source they *are* one issuer's profile (§21.6.1). Drawing
    them independently is what produces a Pennsylvania deal enhanced by a California
    bond insurer.
    """
    by_deal = defaultdict(list)
    for r in data:
        by_deal[r["DEAL_ID"]].append(r)

    seen = set()
    out = []
    for grp in by_deal.values():
        head = grp[0]
        name = _NOISE.sub(" ", clean(head["DEAL_DESCRIPTION"]))
        state = clean(head["STATE"])
        # Deals whose description is a stub ("2026", "25FEB2026") carry no issuer
        # identity, so they cannot seed one.
        if not name or not state or len(name) < 12 or name.lower() in seen:
            continue
        seen.add(name.lower())

        def mode(col, canonical=True):
            vals = [clean(r[col]) for r in grp if clean(r[col])]
            if not vals:
                return ""
            v = Counter(vals).most_common(1)[0][0]
            return canon(v) if canonical else v

        sizes = [int(float(r["DEAL_SIZE"])) for r in grp if clean(r["DEAL_SIZE"])]
        out.append([
            name, state,
            mode("DEAL_SECTOR"),
            mode("PURPOSE", canonical=False),
            mode("TAX_STATUS"),
            mode("DEAL_MOODYS_RATING", canonical=False),
            mode("DEAL_SP_RATING", canonical=False),
            mode("DEAL_FITCH_RATING", canonical=False),
            mode("SOURCE_OF_REPAYMENT", canonical=False),
            mode("SERIES_MONEY_TYPE"),
            mode("ENHANCEMENT", canonical=False),
            max(sizes) if sizes else 100_000_000,
        ])

    out.sort(key=lambda r: r[0])
    write("issuers.csv",
          ["NAME", "STATE", "SECTOR", "PURPOSE", "TAX_STATUS", "MOODYS", "SP", "FITCH",
           "SOURCE_OF_REPAYMENT", "MONEY_TYPE", "ENHANCEMENT", "TYPICAL_SIZE"], out)


# -- purpose -> sector ---------------------------------------------------------

def build_purposes(data: list) -> None:
    """PURPOSE is not free — each code lives under one sector (§21.6.2)."""
    pairs = defaultdict(Counter)
    for r in data:
        p, s = clean(r["PURPOSE"]), canon(clean(r["DEAL_SECTOR"]))
        # "Various" is an aggregate, not a sector a purpose can live under — taking it
        # as a mode gives PIT (personal income tax) a home sector of "Various", which
        # then contradicts every PIT issuer's own General Purposes sector.
        if p and s and s != "Various" and len(p) <= 4:
            pairs[p][s] += 1
    out = [[p, c.most_common(1)[0][0], sum(c.values())] for p, c in sorted(pairs.items())]
    write("purposes.csv", ["CODE", "SECTOR", "WEIGHT"], out)


# -- ratings ladder ------------------------------------------------------------

RATING_LADDER = [
    ("Aaa", "AAA", "AAA"), ("Aa1", "AA+", "AA+"), ("Aa2", "AA", "AA"),
    ("Aa3", "AA-", "AA-"), ("A1", "A+", "A+"), ("A2", "A", "A"),
    ("A3", "A-", "A-"), ("Baa1", "BBB+", "BBB+"), ("Baa2", "BBB", "BBB"),
    ("Baa3", "BBB-", "BBB-"),
]


def build_ratings(data: list) -> None:
    """Aligned triplets, so a series can be notched without crossing agency scales."""
    weights = Counter(clean(r["SERIES_MOODYS_RATING"]) for r in data
                      if clean(r["SERIES_MOODYS_RATING"]) not in ("", "NR", "Various"))
    write("ratings.csv", ["RANK", "MOODYS", "SP", "FITCH", "WEIGHT"],
          [[i + 1, m, s, f, weights.get(m, 0)]
           for i, (m, s, f) in enumerate(RATING_LADDER)])


# -- syndicate -----------------------------------------------------------------

def build_syndicate(data: list) -> None:
    """Underwriter firms, split out of the real semicolon-joined SYNDICATE strings."""
    counts = Counter()
    for r in data:
        for part in clean(r["SYNDICATE"]).split(";"):
            firm = _NOISE.sub(" ", part).strip(" .,")
            if 4 <= len(firm) <= 70 and not firm.isdigit():
                counts[firm] += 1
    write("syndicate.csv", ["NAME", "WEIGHT"],
          [[f, n] for f, n in counts.most_common() if n >= 2])


def build_bnd_banks(data: list) -> None:
    """BND_BANK in the payload — the lead manager, i.e. the first syndicate name."""
    counts = Counter()
    for r in data:
        firm = _NOISE.sub(" ", clean(r["SYNDICATE"]).split(";")[0]).strip(" .,")
        if 4 <= len(firm) <= 70:
            counts[firm] += 1
    write("bnd_banks.csv", ["NAME", "WEIGHT"], [[f, n] for f, n in counts.most_common()])


def build_call_features(data: list) -> None:
    """Real CALL_FEATURE_DESCRIPTION prose, kept whole — the engine prefers its own
    dated template and falls back to these for colour (§21.6.5)."""
    counts = Counter()
    for r in data:
        t = _NOISE.sub(" ", clean(r["CALL_FEATURE_DESCRIPTION"])).strip()
        if 8 <= len(t) <= 600:
            counts[t] += 1
    write("call_features.csv", ["TEXT", "WEIGHT"], [[t, n] for t, n in counts.most_common()])


def main() -> None:
    if not DATA.exists():
        raise SystemExit("missing source: %s" % DATA)
    data = rows(DATA)
    print("reference/munis/ from %d maturity rows:" % len(data))
    build_vocabularies(data)
    build_issuers(data)
    build_purposes(data)
    build_ratings(data)
    build_syndicate(data)
    build_bnd_banks(data)
    build_call_features(data)


if __name__ == "__main__":
    main()
