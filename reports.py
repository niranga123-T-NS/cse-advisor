"""Read figures out of CSE quarterly (interim) and annual report PDFs with plain text matching.

There is no standard format, so this is deliberately conservative:
  * it prefers per-share figures (EPS, net assets per share, dividend per share), which need no units,
  * it measures growth from the "current vs. same period last year" columns every report prints side by side,
    which also needs no units,
  * every value is sanity-checked later against the share price (see fundamentals.py) and dropped if implausible.
Anything it cannot find is left as None rather than guessed.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import shutil
import subprocess
import tempfile

import requests

import cse_client as cse

CDN = "https://cdn.cse.lk/"
PARSER_VERSION = 1
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


# ------------------------------------------------------------------ listing

def list_reports(symbol: str) -> dict:
    """{'quarterly': [...], 'annual': [...]} newest first; each item has id, url, title, end (YYYY-MM-DD)."""
    j = cse.post("financials", {"symbol": symbol}) or {}
    out = {}
    for key, kind in (("infoQuarterlyData", "quarterly"), ("infoAnnualData", "annual")):
        items = []
        for it in j.get(key) or []:
            path = it.get("path") or ""
            if not path.lower().endswith(".pdf"):
                continue
            title = (it.get("fileText") or "").strip()
            end = period_end(title) or _ms_date(it.get("manualDate"))
            items.append({"id": str(it.get("id")), "url": CDN + path.lstrip("/"), "title": title, "end": end,
                          "amended": "amend" in title.lower(), "uploaded": it.get("uploadedDate") or 0})
        # one report per period end; prefer amended / later upload
        best = {}
        for x in items:
            k = x["end"] or x["id"]
            cur = best.get(k)
            if cur is None or (x["amended"], str(x["uploaded"])) > (cur["amended"], str(cur["uploaded"])):
                best[k] = x
        out[kind] = sorted(best.values(), key=lambda x: x["end"] or "", reverse=True)
    return out


def _ms_date(ms) -> str | None:
    try:
        return dt.datetime.fromtimestamp(float(ms) / 1000, tz=dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def period_end(title: str) -> str | None:
    """'Quarter ended 30th September 2026' / 'six months ended June 30,2025' / 'Annual Report 2024/25' -> date."""
    t = title.lower().replace(",", " ")
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?\s+(\d{4})", t)
    if m and m.group(2)[:3] in MONTHS:
        return _date(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
    m = re.search(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})", t)
    if m and m.group(1)[:3] in MONTHS:
        return _date(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
    m = re.search(r"(20\d\d|19\d\d)\s*[/-]\s*(\d{2,4})", t)   # 2024/25 -> FY ending March 2025
    if m:
        y2 = int(m.group(2))
        y2 = y2 + 2000 if y2 < 100 else y2
        return f"{y2}-03-31"
    m = re.search(r"\b(20\d\d|19\d\d)\b", t)                 # 'Annual Report 2024' -> Dec year end
    if m:
        return f"{m.group(1)}-12-31"
    return None


def _date(y, mo, d):
    try:
        return dt.date(y, mo, min(d, 31)).isoformat()
    except ValueError:
        return dt.date(y, mo, 28).isoformat()


# ------------------------------------------------------------------ PDF text

def fetch_pdf(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 (personal CSE research tool)"})
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        print(f"   download failed: {e}")
        return None


def pdf_pages(data: bytes) -> int:
    if shutil.which("pdfinfo"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data); f.flush()
            out = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True, timeout=60).stdout
            m = re.search(r"Pages:\s+(\d+)", out)
            if m:
                return int(m.group(1))
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


def pdf_text(data: bytes, first: int, last: int) -> str:
    """Text of pages first..last (1-based), keeping the column layout where possible."""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data); f.flush()
            try:
                return subprocess.run(["pdftotext", "-layout", "-f", str(first), "-l", str(last), f.name, "-"],
                                      capture_output=True, text=True, timeout=180).stdout
            except subprocess.TimeoutExpired:
                return ""
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(data))
        return "\n".join((rd.pages[i].extract_text() or "") for i in range(first - 1, min(last, len(rd.pages))))
    except Exception:
        return ""


# ------------------------------------------------------------------ number helpers

NUM = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?")


def numbers(s: str) -> list[tuple[float, bool, bool]]:
    """All numbers in a line as (value, has_decimal, is_percent). (1,234) -> -1234."""
    out = []
    for tok in NUM.findall(s):
        pct = tok.endswith("%")
        t = tok.rstrip("%")
        neg = t.startswith("(") and t.endswith(")")
        t = t.strip("()")
        if t.count(",") and not re.fullmatch(r"-?\d{1,3}(,\d{2,3})+(\.\d+)?", t):
            continue
        try:
            v = float(t.replace(",", ""))
        except ValueError:
            continue
        out.append((-v if neg else v, "." in t, pct))
    return out


def _clean_label_line(line: str) -> str:
    # drop things that look like numbers but aren't values: years in labels, "Rs.", "(Rs.)", note refs like "Note 12"
    line = re.sub(r"\((?:rs|lkr)\.?\s*(?:'000|mn|bn)?\)", " ", line, flags=re.I)
    line = re.sub(r"\bnote\s*\d+\b", " ", line, flags=re.I)
    return line


def row_values(text: str, labels: list[str], per_share: bool, skip_if: tuple = ()) -> tuple[float, float] | None:
    """Find the first line starting with one of `labels` that carries >= 2 values; return (current, prior)."""
    pats = [re.compile(r"^\s*[-•*]?\s*" + lab + r"\b", re.I) for lab in labels]
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if not any(p.search(raw) for p in pats):
            continue
        if skip_if and any(s in raw.lower() for s in skip_if):
            continue
        line = _clean_label_line(raw)
        vals = numbers(line)
        # label may wrap onto the next line, numbers on that one
        if len(vals) < 2 and i + 1 < len(lines):
            vals = numbers(_clean_label_line(lines[i + 1]))
        vals = [v for v in vals if not v[2]]  # ignore % columns
        if per_share:
            # drop a leading small integer note reference when decimals follow ("Earnings per share 14  3.45  2.10")
            if len(vals) >= 3 and not vals[0][1] and abs(vals[0][0]) <= 60 and vals[1][1]:
                vals = vals[1:]
        else:
            vals = [v for v in vals if not (not v[1] and abs(v[0]) < 100 and len(vals) > 2)]  # note refs
        if len(vals) >= 2:
            return vals[0][0], vals[1][0]
    return None


def period_months(text: str) -> int | None:
    """Length of the first column's period, judged from the column headers above the first statement row."""
    words = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "3": 3, "6": 6, "9": 9, "12": 12}
    lines = text.splitlines()
    anchor = next((i for i, l in enumerate(lines)
                   if re.match(r"^\s*(revenue|turnover|gross income|interest income|total revenue|gross written premium)\b", l, re.I)
                   and len(numbers(l)) >= 2), None)
    window = lines[max(0, anchor - 40):anchor] if anchor is not None else lines[:200]
    for l in window:
        ll = l.lower()
        m = re.search(r"\b(three|six|nine|twelve|3|6|9|12)\s*months?\b", ll)
        y = re.search(r"\byear\s+ended\b|\b12\s*months\b", ll)
        q = re.search(r"\bquarter\s+ended\b", ll)
        hits = []
        if m: hits.append((m.start(), words[m.group(1)]))
        if y: hits.append((y.start(), 12))
        if q: hits.append((q.start(), 3))
        if hits:
            return min(hits)[1]
    return None


# ------------------------------------------------------------------ parsers

REVENUE = ["revenue", "total revenue", "turnover", "gross income", "gross written premium", "net interest income",
           "revenue from contracts with customers"]
PROFIT_ATTR = [r"(?:owners|equity holders|shareholders) of the (?:parent|company|bank|group)", "owners of the parent",
               "equity holders of the parent", "equity holders of the company", "equity holders of the bank"]
PROFIT = [r"profit\s*/?\s*\(?loss\)?\s*for the (?:period|year|quarter)", r"profit for the (?:period|year|quarter)",
          r"net profit (?:for the (?:period|year)|after tax)", r"profit after tax(?:ation)?", r"profit\s*/?\s*\(?loss\)?\s*after tax"]
_EL = r"earnings(?:\s*/\s*\(?loss\)?)?\s*per (?:ordinary )?share"
EPS = [r"basic\s*(?:(?:and|/|&)\s*diluted)?\s*" + _EL, _EL + r"\s*(?:-|–)?\s*basic", _EL, r"basic eps", r"eps\b"]
NAVPS = [r"net assets? (?:value )?per (?:ordinary )?share", r"net asset value per share", r"nav per share",
         r"book value per share"]
DPS = [r"dividends? per (?:ordinary )?share", r"dps\b"]
ROE = [r"return on (?:average )?(?:shareholders'?\s*)?equity", r"roe\b"]


def parse_interim(text: str) -> dict:
    eps = row_values(text, EPS, per_share=True, skip_if=("diluted",)) or row_values(text, EPS, per_share=True)
    rev = row_values(text, REVENUE, per_share=False)
    prof = row_values(text, PROFIT_ATTR, per_share=False) or row_values(text, PROFIT, per_share=False)
    nav = row_values(text, NAVPS, per_share=True)
    return {
        "months": period_months(text),
        "eps": eps[0] if eps else None, "eps_prior": eps[1] if eps else None,
        "revenue_growth": _growth(rev), "profit_growth": _growth(prof),
        "profit_sign": None if not prof else (1 if prof[0] > 0 else -1),
        "navps": nav[0] if nav else None,
    }


def parse_annual(text: str) -> dict:
    eps = row_values(text, EPS, per_share=True, skip_if=("diluted",)) or row_values(text, EPS, per_share=True)
    dps = row_values(text, DPS, per_share=True, skip_if=("paid", "proposed dividend of"))
    nav = row_values(text, NAVPS, per_share=True)
    roe = row_values(text, ROE, per_share=True)
    return {"eps": eps[0] if eps else None, "eps_prior": eps[1] if eps else None,
            "dps": dps[0] if dps else None, "navps": nav[0] if nav else None,
            "roe": roe[0] if roe else None}


def _growth(pair):
    if not pair:
        return None
    cur, prior = pair
    if prior == 0 or (cur < 0 and prior < 0):
        return None
    if prior < 0 < cur:
        return None  # loss to profit: growth % is meaningless
    g = (cur / prior - 1) * 100
    return round(g, 1) if -95 <= g <= 1000 else None


def read_report(item: dict, kind: str) -> dict:
    data = fetch_pdf(item["url"])
    if not data:
        return {"ok": False}
    n = pdf_pages(data)
    if kind == "quarterly":
        text = pdf_text(data, 1, min(n or 20, 20))
        res = parse_interim(text)
    else:
        # highlights are near the front; multi-year summaries and statements near the back
        parts = [pdf_text(data, 1, min(n, 40))]
        if n > 40:
            parts.append(pdf_text(data, max(41, n - 120), n))
        res = parse_annual("\n".join(parts))
    res.update({"ok": True, "pages": n, "v": PARSER_VERSION})
    return res
