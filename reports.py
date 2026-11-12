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
PARSER_VERSION = 5
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
                          "year_only": bool(end) and not re.search(r"[a-z]{3,9}\.?\s+\d|\d\s*[a-z]{3,9}|\d{4}\s*[/-]\s*\d{2}", title.lower()),
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
    # remove unit markers that contain digits so they aren't read as values: (Rs.'000), Rs. 000, "Note 12"
    line = re.sub(r"\(?(?:rs|lkr)\.?\s*['’`]?\s*0{3}\)?", " ", line, flags=re.I)
    line = re.sub(r"\((?:rs|lkr)\.?\s*(?:cts?\.?|mn|bn)?\)", " ", line, flags=re.I)
    line = re.sub(r"\bnote\s*\d+\b", " ", line, flags=re.I)
    return line


ALLOWED_WORDS = {"rs", "lkr", "cts", "ct", "rs.", "mn", "bn", "basic", "diluted", "and", "per", "share", "x", "times"}
WORD = re.compile(r"[A-Za-z][A-Za-z'’]{2,}")


def _values_after(s: str, start: int, per_share: bool) -> list[tuple[float, bool, bool]]:
    """Numbers after position `start`, stopping at the next text label (another column's row)."""
    tail = _clean_label_line(s[start:])
    seen_num, cut = False, len(tail)
    for m in re.finditer(r"[A-Za-z][A-Za-z.'’]*|\(?-?\d[\d,]*(?:\.\d+)?\)?%?", tail):
        tok = m.group(0)
        if tok[0].isalpha() or (tok[0] == "(" and len(tok) > 1 and tok[1].isalpha()):
            if seen_num and len(tok.strip(".")) >= 3 and tok.lower().strip(".") not in ALLOWED_WORDS:
                cut = m.start(); break
        else:
            seen_num = True
    vals = [v for v in numbers(tail[:cut]) if not v[2]]  # drop % tokens
    if per_share:
        vals = [v for v in vals if not (not v[1] and 1990 <= v[0] <= 2040)]  # years
    # a leading small integer is a note reference when real values (decimals / thousands) follow
    if len(vals) >= 3 and not vals[0][1] and 0 < abs(vals[0][0]) < 100 and (vals[1][1] or abs(vals[1][0]) >= 1000):
        vals = vals[1:]
    return vals


class Row(tuple):
    """(current, prior) with provenance: .src line text, .idx line number, .all every value on the row."""
    def __new__(cls, cur, prior, src, idx, all_vals, layout):
        r = super().__new__(cls, (cur, prior))
        r.src, r.idx, r.all, r.layout = src, idx, all_vals, layout
        return r


def _is_chg(chg, cur, prior, per_share=True):
    """Is `chg` the printed % change from prior to cur?  Tolerance = rounding of the printed figures only."""
    if not prior or not cur or chg is None:
        return False
    real = (cur / prior - 1) * 100
    h = 0.005 if per_share else 0.5            # rounding of the cur/prior figures
    tol = (0.5 if float(chg).is_integer() else 0.05) + 100 * abs(cur / prior) * (h / abs(cur) + h / abs(prior))
    return abs(real - chg) <= tol and abs(real) >= 0.5


def choose_layout(v: list[float], per_share: bool = True) -> tuple[int, int, bool]:
    """Pick (offset, step) so current=v[k], prior=v[k+step].  Returns (k, step, self_checked).

    Self-check: reports print a % change column; if it matches cur/prior it confirms the columns.
      [cur, prior, chg%, ...] -> (0, 1)    [cur, chg%, prior, ...] -> (0, 2)   with a leading extra value -> k=1
    """
    for k in (0, 1):
        if len(v) > k + 2 and _is_chg(v[k + 2], v[k], v[k + 1], per_share):
            return k, 1, True
        if len(v) > k + 2 and _is_chg(v[k + 1], v[k], v[k + 2], per_share):
            return k, 2, True
    return 0, 1, False


def row_values(text: str, labels: list[str], per_share: bool, skip_if: tuple = (), midline: bool = False,
               sublabel: str | None = None) -> Row | None:
    """First row whose label matches; values are read only to the right of the label."""
    pats = [re.compile((r"" if midline else r"^\s*[-•*]?\s*") + lab + r"\b", re.I) for lab in labels]
    lines = text.splitlines()
    skip = tuple(skip_if) + ("usd", "us$", "us dollar", "$")
    for i, raw in enumerate(lines):
        m = next((mm for p in pats for mm in [p.search(raw)] if mm), None)
        if not m or any(s in raw.lower() for s in skip):
            continue
        vals, src = _values_after(raw, m.end(), per_share), raw
        j = i
        while len(vals) < 2 and j + 1 < len(lines) and j < i + 3:  # numbers wrapped onto following lines
            j += 1
            nxt = lines[j]
            if any(s in nxt.lower() for s in skip):
                break
            sm = re.search(sublabel, nxt, re.I) if sublabel else None
            if sm:
                vals = _values_after(nxt, sm.end(), per_share)
            else:
                first_word = WORD.search(_clean_label_line(nxt))
                first_num = NUM.search(nxt)
                if first_word and (not first_num or first_word.start() < first_num.start()) \
                        and first_word.group(0).lower() not in ALLOWED_WORDS:
                    break  # next line is another row's label
                vals = _values_after(nxt, 0, per_share)
            src = raw.strip() + " ⏎ " + nxt
        if len(vals) >= 2:
            v = [x[0] for x in vals][:10]
            k, step, ok = choose_layout(v, per_share)
            if len(v) > k + step:
                return Row(v[k], v[k + step], re.sub(r"\s{2,}", "  ", src.strip())[:240], i, v, [k, step, ok])
    return None


UNIT_PATS = [(re.compile(r"(?:rs|lkr)\.?\s*['’`]?\s*0{3}\b|in thousands|['’]000", re.I), 1e3),
             (re.compile(r"(?:rs|lkr)\.?\s*(?:mn|million|mio)\b|in millions", re.I), 1e6),
             (re.compile(r"(?:rs|lkr)\.?\s*(?:bn|billion)\b", re.I), 1e9)]


def unit_before(text: str, idx: int) -> float | None:
    """Money unit stated in the column headers above line `idx` (Rs.'000 / Rs. Mn / ...)."""
    lines = text.splitlines()
    for l in reversed(lines[max(0, idx - 80):idx + 1]):
        for pat, mult in UNIT_PATS:
            if pat.search(l):
                return mult
    return None


def period_months(text: str) -> int | None:
    """Length of the first column's period: the column-header line nearest above the first statement row,
    leftmost period phrase on it."""
    words = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "3": 3, "6": 6, "9": 9, "12": 12,
             "03": 3, "06": 6, "09": 9}
    lines = text.splitlines()
    anchor = next((i for i, l in enumerate(lines)
                   if re.match(r"^\s*(revenue|turnover|gross income|interest income|total revenue|gross written premium|revenue from contracts)\b", l, re.I)
                   and len(numbers(l)) >= 2), None)
    if anchor is None:
        return None
    for l in reversed(lines[max(0, anchor - 25):anchor]):
        ll = l.lower()
        hits = [(m.start(), words[m.group(1)]) for m in re.finditer(r"\b(three|six|nine|twelve|0?3|0?6|0?9|12)\s*months?\b", ll)]
        hits += [(m.start(), 12) for m in re.finditer(r"\b(?:year|twelve months)\s+(?:ended|to)\b", ll)]
        hits += [(m.start(), 3) for m in re.finditer(r"\bquarter\s+(?:ended|to)\b", ll)]
        if hits:
            return min(hits)[1]
    return None


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


def _pair(row, key):
    if not row:
        return {}
    return {key: row[0], key + "_prior": row[1], "src_" + key: row.src, "vals_" + key: row.all, "layout_" + key: row.layout}


EPS_SUB = r"\bbasic\b(?:\s*\(?rs\.?\)?)?"


NOT_PROFIT = ("equity attributable", "total equity", "comprehensive", "equity holders' funds", "shareholders' funds",
              "reserves", "capital")


def profit_attributable(text: str):
    """The 'owners of the parent' line inside the 'Profit ... attributable to' block of the income statement
    (not the balance-sheet equity line, and not the comprehensive-income block)."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        ll = l.lower()
        if re.search(r"profit.{0,40}attributable|attributable to\s*:?\s*$", ll) and "comprehensive" not in ll:
            block = "\n".join(lines[i:i + 6])
            r = row_values(block, PROFIT_ATTR, False, midline=True, skip_if=NOT_PROFIT)
            if r:
                r.idx += i
                return r
    return row_values(text, PROFIT_ATTR, False, midline=True, skip_if=NOT_PROFIT)


def parse_interim(text: str) -> dict:
    eps = row_values(text, EPS, True, skip_if=("diluted",), sublabel=EPS_SUB) or row_values(text, EPS, True, sublabel=EPS_SUB)
    rev = row_values(text, REVENUE, False)
    prof = profit_attributable(text) or row_values(text, PROFIT, False, skip_if=NOT_PROFIT)
    nav = row_values(text, NAVPS, True)
    out = {"months": period_months(text), "revenue_growth": _growth(rev), "profit_growth": _growth(prof),
           **_pair(eps, "eps"), **_pair(rev, "revenue"), **_pair(prof, "profit"), **_pair(nav, "navps")}
    if prof:
        out["unit"] = unit_before(text, prof.idx)
    return out


def parse_annual(text: str) -> dict:
    eps = row_values(text, EPS, True, skip_if=("diluted",), sublabel=EPS_SUB) or row_values(text, EPS, True, sublabel=EPS_SUB)
    dps = row_values(text, DPS, True, skip_if=("paid", "proposed dividend of"))
    nav = row_values(text, NAVPS, True)
    return {**_pair(eps, "eps"), **_pair(dps, "dps"), **_pair(nav, "navps")}


def _growth(pair):
    if not pair:
        return None
    cur, prior = pair
    if cur is None or prior is None:
        return None
    if prior == 0 or (cur < 0 and prior < 0):
        return None
    if prior < 0 < cur:
        return None  # loss to profit: growth % is meaningless
    g = (cur / prior - 1) * 100
    if g == 0:
        return None  # identical figures almost always means the wrong row was read
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
