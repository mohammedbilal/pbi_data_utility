"""Read a saved email file into the fields an upload run needs (spec 19.4, 19.5).

Two jobs, both **pure** apart from reading the file the caller names:

* ``parse_email`` — ``.msg`` / ``.eml`` / ``.html`` / ``.txt`` -> subject, sent
  timestamp, sender, HTML body and a plain-text rendering of it.
* ``extract_identifiers`` — the ISINs, CUSIPs and FIGIs an email states, each
  **validated by its check digit**.

Nothing here touches the store, the network or the application database.

Why the check digit is not optional (19.5): a false ISIN pairs the email with
somebody else's deal, which is worse than not pairing it at all. Since every
candidate is validated, the *patterns* are allowed to be loose — and they need to
be, because real broker mail breaks identifiers across whitespace. The SUMIBK
sample prints ``86562M EN6``, which is a perfectly good CUSIP that fails as a raw
token and validates once its internal space is removed.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

#: Extensions ``parse_email`` accepts. ``.pst`` is deliberately absent (19.15).
SUPPORTED_SUFFIXES = (".msg", ".eml", ".html", ".htm", ".txt")


# ── check digits ──────────────────────────────────────────────────────────────
# Both schemes are mod-10 with alternating weights; they differ in how a letter
# becomes a number, so they get one function each rather than a shared one with a
# flag. Neither raises: an unparseable candidate is simply not an identifier.

def isin_check(value: str) -> bool:
    """True if ``value`` is a structurally valid ISIN including its check digit.

    Two letters of country code, nine of NSIN, one check digit. Letters expand to
    their base-36 value ('A' -> 10) before a Luhn pass over the digit string.
    """
    s = _squeeze(value)
    if len(s) != 12 or not s[:2].isalpha() or not s[11].isdigit():
        return False
    if not all(c.isalnum() for c in s):
        return False
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in s[:11])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:                      # double every second digit from the right
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(s[11])


def cusip_check(value: str) -> bool:
    """True if ``value`` is a valid 9-character CUSIP (or CINS) with check digit.

    CINS — the international form, letter-prefixed like ``N4579DDA3`` — shares the
    algorithm, so Reg S legs validate through the same path.
    """
    s = _squeeze(value)
    if len(s) != 9 or not s[8].isdigit():
        return False
    total = 0
    for i, c in enumerate(s[:8]):
        if c.isdigit():
            v = int(c)
        elif c.isalpha():
            v = ord(c) - ord("A") + 10
        elif c == "*":
            v = 36
        elif c == "@":
            v = 37
        elif c == "#":
            v = 38
        else:
            return False
        if i % 2:                           # weight 2 on every second position
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(s[8])


def _squeeze(value: str) -> str:
    """Upper-case with **all** whitespace removed — the form a check digit sees.

    This is the SUMIBK fix: ``'86562M EN6'`` -> ``'86562MEN6'``. Applied to every
    candidate, so a formatting break can never cost an identifier.
    """
    return re.sub(r"\s+", "", (value or "").upper())


def _candidates(text: str):
    """Yield ``(candidate, fragments)`` for every token and every run of 2–3
    whitespace-adjacent tokens.

    Tokens must be separated by **whitespace only** to be joined, so ``T+120-125``
    never fuses across its punctuation. Each candidate is proposed independently of
    the others, which is the point (see ``_TOKEN_RE``).
    """
    toks = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
    for i, (tok, _start, end) in enumerate(toks):
        yield tok, [tok]
        parts, prev_end = [tok], end
        for j in range(i + 1, min(i + 3, len(toks))):
            nxt, nstart, nend = toks[j]
            if text[prev_end:nstart].strip():        # more than whitespace between
                break
            parts = parts + [nxt]
            prev_end = nend
            yield "".join(parts), parts


def _fragments_ok(parts: List[str]) -> bool:
    """Whether a candidate split across whitespace is credible.

    Permitting internal whitespace is what recovers ``'86562M EN6'``, but it also
    lets a pattern run across two prose words — and about one in ten such strings
    passes a check digit by luck, which is exactly the false identifier that would
    pair an email to somebody else's deal. Measured on the ten real samples, the
    unguarded version accepted ``'date August 15'`` and ``'and January 15'`` as
    ISINs and ``'2029 May 20'`` as a CUSIP.

    The rule: **every fragment must carry a digit.** Both halves of a broken CUSIP
    do; ``DATE`` and ``AND`` do not.
    """
    if len(parts) == 1:
        return True
    if len(parts) > 3:
        return False
    return all(any(ch.isdigit() for ch in p) for p in parts)


#: ISO 3166-1 alpha-2, plus ``XS`` for Euroclear/Clearstream international issues.
#: A second, independent guard on the ISIN lane: the first two characters of an
#: ISIN are a country code, so a prose collision has to clear this *and* the check
#: digit. Retired codes are deliberately absent — ``AN`` (Netherlands Antilles) is
#: what let ``'and January 15'`` through.
_ISO_COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM
BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX
CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG
GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR
IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV
LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE
NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF
WS XS YE YT ZA ZM ZW
""".split())


# ── identifier extraction ─────────────────────────────────────────────────────
# Loose patterns, tight validation. Internal whitespace is permitted inside a
# candidate and stripped before checking; the check digit is what rejects prose.

#: Candidates are built by tokenising, **not** by one whitespace-tolerant regex
#: pass. A regex permissive enough to rejoin ``'86562M EN6'`` also *consumes* what
#: it scanned, and ``finditer`` never revisits an overlapping start: on the SUMIBK
#: sample a match beginning at the label ``ISIN`` swallowed the first identifier of
#: the line, so four of that email's five tranches were found and the fifth was
#: silently lost. Tokenising proposes every candidate independently.
_TOKEN_RE = re.compile(r"[A-Z0-9]+")
#: A FIGI is ``BBG`` + 8 alphanumerics + a check digit, and is *shaped* like an
#: ISIN beginning with the country code ``BB`` (Barbados). Its own check digit is
#: a different scheme, so ~1 in 10 FIGIs would pass the ISIN test by luck. They
#: are matched explicitly and excluded from the ISIN lane rather than gambling.
_FIGI_RE = re.compile(r"\b(BBG[A-Z0-9]{8}\d)\b")

#: All-caps parenthesised tokens that are never tickers. ``(Exp)`` is excluded by
#: the case requirement, ``(B&D)`` and ``(Y/N)`` by the character class.
_TICKER_STOPLIST = frozenset({
    "EXP", "ICMA", "ISDA", "TEFRA", "SEC", "USD", "EUR", "GBP", "CAD", "JPY",
    "CHF", "AUD", "REGS", "IPT", "IPTS", "UOP", "ESG", "TBD", "NA", "N A",
    "AM", "PM", "ET", "BST", "GMT", "EST", "AREA", "MM", "BN", "CB", "PLC",
    # Seen in the ten real samples as parenthesised all-caps that are not tickers.
    "WNG", "T", "B&D", "I", "II", "III", "IV", "V", "A", "X", "N",
})
_TICKER_RE = re.compile(r"\(\s*([A-Z][A-Z0-9.\-]{0,8})\s*\)")


@dataclass
class Identifiers:
    """What one email states, deduplicated and check-digit clean."""
    isins: List[str] = field(default_factory=list)
    cusips: List[str] = field(default_factory=list)
    figis: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    #: Candidates that looked like an identifier and failed their check digit.
    #: Surfaced rather than dropped: a real one broken by OCR or a typo shows up
    #: here, and the user can pin it by hand (19.5).
    rejected: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, List[str]]:
        return {"isins": self.isins, "cusips": self.cusips, "figis": self.figis,
                "tickers": self.tickers, "rejected": self.rejected}

    @property
    def matchable(self) -> bool:
        """Whether this email can be paired at all without a manual pin (19.8)."""
        return bool(self.isins or self.cusips)


def extract_identifiers(text: str, *, derive_cusip_from_isin: bool = True) -> Identifiers:
    """Every valid identifier in ``text``.

    ``derive_cusip_from_isin`` keeps the CUSIP embedded in a US ISIN even when the
    email prints only the ISIN — ``US90331HPV95`` carries ``90331HPV9`` — because
    an extra exact matching key costs nothing and some formats print one and not
    the other. The derived value is a real identifier, not a guess.
    """
    text = text or ""
    isins: Set[str] = set()
    cusips: Set[str] = set()
    figis: Set[str] = set()
    rejected: Set[str] = set()

    upper = text.upper()
    for cand, parts in _candidates(upper):
        size = len(cand)
        if size not in (9, 12):
            continue
        if len(parts) > 1 and not _fragments_ok(parts):
            continue
        if size == 12:
            if _FIGI_RE.fullmatch(cand):
                figis.add(cand)
            elif cand[:2] not in _ISO_COUNTRIES:
                continue
            elif isin_check(cand):
                isins.add(cand)
            elif len(parts) == 1 and cand[-1].isdigit():
                # Identifier-shaped and single-token, but the digit is wrong: a
                # typo worth surfacing. The trailing-digit test is what keeps
                # ordinary prose out — ``INDEBTEDNESS`` and ``REGISTRATION`` are
                # twelve characters whose first two are valid country codes (IN,
                # RE), so nothing before this point excludes them. A failed
                # multi-token join stays quiet: it is almost always two abutting
                # prose numbers.
                rejected.add(cand)
        elif cusip_check(cand):
            cusips.add(cand)
        elif len(parts) == 1 and any(c.isdigit() for c in cand):
            rejected.add(cand)

    if derive_cusip_from_isin:
        for i in isins:
            nsin = i[2:11]
            if cusip_check(nsin):
                cusips.add(nsin)

    tickers = []
    for m in _TICKER_RE.finditer(text):
        t = m.group(1).strip()
        if (t and t == t.upper() and 1 < len(t) <= 9
                and t not in _TICKER_STOPLIST and not t.isdigit()
                and t not in tickers):
            tickers.append(t)

    # A rejected candidate that turned out to be a valid identifier under the
    # other scheme is not a rejection at all.
    clean = sorted(r for r in rejected if r not in isins and r not in cusips)
    return Identifiers(sorted(isins), sorted(cusips), sorted(figis), tickers, clean)


# ── html -> text ──────────────────────────────────────────────────────────────

_BLOCK_TAGS = r"(?:br|/?p|/?div|/?tr|/?table|/?h[1-6]|/?li|/?ul|/?ol)"
_CELL_TAGS = r"(?:/?td|/?th)"


def html_to_text(source: str) -> str:
    """A plain-text rendering that keeps cell and row boundaries.

    Only structure matters here, so this is a stripper rather than a parser: what
    it must not do is let two table cells run together. ``USD Benchmark`` beside
    ``USD Benchmark`` becoming ``USD BenchmarkUSD Benchmark`` would corrupt both
    identifier extraction and anything a human reads off the text lane.
    """
    if not source:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", source)
    text = re.sub(rf"(?i)<{_CELL_TAGS}\b[^>]*>", "\t", text)
    text = re.sub(rf"(?i)<{_BLOCK_TAGS}\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    # A tab wins over any newline beside it. Real Outlook HTML indents its markup,
    # so a cell arrives as "\t\n  value\n" — collapsing whitespace around the
    # newline first would eat the tab, and with it the cell boundary. The unit test
    # for this used a minimal table with no whitespace between its tags and so
    # never saw the problem; the ten real samples did.
    text = re.sub(r"[ \t]*\n[ \t]*\t[ \t\n]*", "\t", text)
    text = re.sub(r"\t[ \t]*\n[ \t]*", "\t", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", "  ", text)
    return text.strip()


_TR_RE = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr\s*>")
_TD_RE = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]\s*>")


def html_tables(source: str) -> List[List[str]]:
    """Rows of cells from every HTML table, as plain text.

    Most of these emails are one table whose first column is the field label and
    whose remaining columns are one tranche each, so the **row** is the unit that
    matters: ``label, value, value, value``. A flat text rendering cannot carry that
    reliably — a cell may hold several paragraphs (``Moody's (Exp): A2`` and
    ``Stable`` are two ``<p>`` inside one cell), and once a cell's internal newlines
    are indistinguishable from row breaks the label can no longer be tied to its
    values.

    Deliberately a small regex reader rather than a parser dependency: it only has to
    find ``<tr>`` and ``<td>``, and malformed markup should yield fewer rows rather
    than raise.
    """
    rows: List[List[str]] = []
    for block in _TR_RE.findall(source or ""):
        cells = [re.sub(r"\s+", " ", html_to_text(cell)).strip()
                 for cell in _TD_RE.findall(block)]
        if any(cells):
            rows.append(cells)
    return rows


# ── parsing one file ──────────────────────────────────────────────────────────

@dataclass
class ParsedEmail:
    """One email file, in the shape ``util_email`` wants (19.5)."""
    source_name: str
    subject: str = ""
    sender: str = ""
    sent_at: Optional[datetime] = None
    body_html: str = ""
    body_text: str = ""
    identifiers: Identifiers = field(default_factory=Identifiers)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name, "subject": self.subject,
            "sender": self.sender,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "body_html": self.body_html, "body_text": self.body_text,
            "identifiers": self.identifiers.as_dict(), "warnings": self.warnings,
        }


def parse_email(path: Any, *, name: Optional[str] = None) -> ParsedEmail:
    """Read one email file. Raises ``ValueError`` on an unsupported suffix.

    A file that parses but yields no body is returned with a warning rather than
    an exception — one unreadable email must not fail an upload of thirty.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported email file type {suffix!r} "
                         f"(accepted: {', '.join(SUPPORTED_SUFFIXES)})")
    out = ParsedEmail(source_name=name or p.name)

    if suffix == ".msg":
        _read_msg(p, out)
    elif suffix == ".eml":
        _read_eml(p, out)
    else:
        raw = p.read_bytes().decode("utf-8", errors="replace")
        if suffix == ".txt":
            out.body_text = raw.strip()
            out.body_html = "<pre>" + _html.escape(raw) + "</pre>"
        else:
            out.body_html = raw
        out.subject = out.subject or p.stem

    if not out.body_text and out.body_html:
        out.body_text = html_to_text(out.body_html)
    if not out.body_html and out.body_text:
        out.body_html = "<pre>" + _html.escape(out.body_text) + "</pre>"
    if not out.body_text:
        out.warnings.append("no readable body")

    # Both lanes are searched: a table renders differently in each, and an
    # identifier broken in one is often intact in the other.
    out.identifiers = extract_identifiers(
        "\n".join(x for x in (out.body_text, html_to_text(out.body_html), out.subject) if x))
    if not out.identifiers.matchable:
        out.warnings.append("no ISIN or CUSIP found — needs a manual pin to be matched")
    return out


def _read_msg(p: Path, out: ParsedEmail) -> None:
    """Outlook ``.msg`` via extract-msg — offline, no Outlook process (D15)."""
    try:
        import extract_msg                       # local: absent -> a clear warning
    except ImportError:                          # pragma: no cover
        out.warnings.append("extract-msg is not installed — cannot read .msg files")
        return
    msg = extract_msg.Message(str(p))
    try:
        out.subject = (msg.subject or "").strip()
        out.sender = (msg.sender or "").strip()
        out.sent_at = _as_datetime(msg.date)
        out.body_html = _as_text(msg.htmlBody)
        out.body_text = (msg.body or "").strip()
        if getattr(msg, "attachments", None):
            out.warnings.append(f"{len(msg.attachments)} attachment(s) ignored (19.15)")
    finally:
        try:
            msg.close()
        except Exception:                        # pragma: no cover
            pass


def _read_eml(p: Path, out: ParsedEmail) -> None:
    msg = message_from_bytes(p.read_bytes())
    out.subject = (msg.get("Subject") or "").strip()
    out.sender = (msg.get("From") or "").strip()
    raw_date = msg.get("Date")
    if raw_date:
        try:
            out.sent_at = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            out.warnings.append(f"unparseable Date header {raw_date!r}")
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            body = part.get_payload(decode=True)
        except Exception:                        # pragma: no cover
            continue
        if not body:
            continue
        text = body.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html" and not out.body_html:
            out.body_html = text
        elif ctype == "text/plain" and not out.body_text:
            out.body_text = text.strip()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
