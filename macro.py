"""Interest rates and the rupee, for the Market page.

Sources, all collected in GitHub Actions (this repo's own workspace cannot reach them):
  - USD/LKR, daily: Yahoo Finance (LKR=X), unofficial.
  - T-bill yields (weighted average at each weekly primary auction, 91/182/364 days): the Public Debt Management
    Office's auction results on treasury.gov.lk, published there since December 2025, when the office took the work
    over from the Central Bank. (The Central Bank's own site refuses connections from GitHub's servers.)
  - Policy rate: data/macro/policy.csv, kept by hand from the Central Bank's announcements (it changes a few times
    a year at most). The last confirmed date is in LAST_CONFIRMED below.

  python macro.py probe        save what treasury.gov.lk serves, to check the parser
  python macro.py fetch        collect new auction results and the rupee
  python macro.py build        write site/data/macro.json (also called from build_site.py)
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
DIR = ROOT / "data" / "macro"
LIST = "https://www.treasury.gov.lk/web/government-securities/section/auction%20result"
LAST_CONFIRMED = ("2026-09-30", "https://www.themorning.lk/articles/Z6l8pwZbnUf6sNQ7Twsw")    # policy rate last checked as unchanged
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in r.pages[:6])


def probe():
    """Save what treasury.gov.lk serves, to find how its list of auction results is published."""
    out = DIR / "raw"
    out.mkdir(parents=True, exist_ok=True)
    s0 = requests.Session()
    for name, u in (("page", "https://www.treasury.gov.lk/web/public-debt-management-office"),
                    ("file", "https://www.treasury.gov.lk/api/file/4a4e9baf-a75c-4d58-893f-d25dc7e3e43c"),
                    ("home", "https://www.treasury.gov.lk/")):
        try:
            r = s0.get(u, headers=UA, timeout=60)
            print("PROBE", name, r.status_code, r.headers.get("content-type"), len(r.content))
            if r.content[:4] == b"%PDF":
                (out / f"{name}.txt").write_text(pdf_text(r.content))
            else:
                (out / f"{name}.html").write_bytes(r.content[:400000])
                for js in re.findall(r'src="([^"]+\.js)"', r.text)[:12]:
                    jsu = js if js.startswith("http") else "https://www.treasury.gov.lk/" + js.lstrip("/")
                    try:
                        jr = s0.get(jsu, headers=UA, timeout=60)
                        apis = sorted(set(re.findall(r'["\'`](/?api/[A-Za-z0-9_/\-${}.?=&]+)', jr.text)))
                        print("JS", jsu, jr.status_code, len(jr.text), apis[:60])
                    except requests.RequestException as ex:
                        print("JS", jsu, repr(ex)[:100])
        except requests.RequestException as ex:
            print("PROBE", name, "error", repr(ex)[:200])


def fx(s: requests.Session) -> list[list]:
    """Daily USD/LKR closes from Yahoo Finance."""
    for attempt in range(4):
        try:
            r = s.get("https://query1.finance.yahoo.com/v8/finance/chart/LKR=X", headers={**UA, "Accept": "application/json"}, timeout=40,
                      params={"period1": 0, "period2": int(time.time()), "interval": "1d"})
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                q = res["indicators"]["quote"][0]["close"]
                rows = [[dt.datetime.fromtimestamp(t + 19800, dt.timezone.utc).strftime("%Y-%m-%d"), round(c, 3)] for t, c in zip(res["timestamp"], q) if c]
                DIR.mkdir(parents=True, exist_ok=True)
                (DIR / "usdlkr.json").write_text(json.dumps(rows, separators=(",", ":")))
                return rows
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            pass
        time.sleep(10 * (attempt + 1))
    return []


# ---------------------------------------------------------------- T-bill auctions

def listing(s: requests.Session) -> list[tuple[str, str]]:
    """(file url, title) for every auction result on the debt office's list, following its pages."""
    out, seen = [], set()
    for page in range(1, 30):
        url = LIST + (f"?page={page}" if page > 1 else "")
        try:
            r = s.get(url, headers=UA, timeout=60)
        except requests.RequestException:
            break
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        blob = m.group(1) if m else r.text
        blob = blob.encode().decode("unicode_escape", errors="ignore")
        found = 0
        for href, title in re.findall(r'<a href="(https?://www\.treasury\.gov\.lk/api/file/[0-9a-f-]{36})"[^>]*>(.*?)</a>', blob, re.S):
            t = re.sub(r"<[^>]+>|&nbsp;", " ", title)
            if href not in seen:
                seen.add(href); found += 1
                out.append((href, " ".join(t.split())))
        print(f"list page {page}: {found} new links")
        if not found:
            break
    return out


def parse_tbill(text: str) -> dict | None:
    t = " ".join(text.split())
    m = re.search(r"TREASURY BILLS? AUCTION HELD ON (\d{1,2} [A-Za-z]+ \d{4})", t, re.I)
    if not m:
        return None
    try:
        day = dt.datetime.strptime(m.group(1).title(), "%d %B %Y").date().isoformat()
    except ValueError:
        return None
    out = {"date": day}
    for days in ("91", "182", "364"):
        y = re.search(rf"\b{days} LKA[0-9A-Z]{{9}} ([\d, \-]+?) (\d{{1,2}}\.\d{{2}}) (\d{{1,2}}\.\d{{2}}|-)", t)
        if y and 3 <= float(y.group(2)) <= 35:
            out[days] = float(y.group(2))
    return out if len(out) > 1 else None


def fetch():
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / "tbill.json"
    have = json.loads(f.read_text()) if f.exists() else {"auctions": {}, "seen": []}
    s = requests.Session()
    links = listing(s)
    new = 0
    for href, title in links:
        if href in have["seen"] or not re.search(r"bill", title, re.I) or re.search(r"bond", title, re.I):
            continue
        try:
            r = s.get(href, headers=UA, timeout=60)
        except requests.RequestException:
            continue
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            continue
        have["seen"].append(href)
        a = parse_tbill(pdf_text(r.content))
        if a:                                              # phase II notices carry no table and are skipped
            have["auctions"][a["date"]] = {**{k: v for k, v in a.items() if k != "date"}, "src": href}
            new += 1
            print("auction", a)
        time.sleep(0.5)
    f.write_text(json.dumps(have, indent=1, sort_keys=True))
    print(f"{len(links)} links, {new} new auctions, {len(have['auctions'])} in all")
    rows = fx(s)
    print("fx rows", len(rows), rows[-1:] if rows else None)


# ---------------------------------------------------------------- for the site

def _usd() -> list[list]:
    """Daily USD/LKR, with one-day spikes that reverse the next day removed (a known fault of this feed)."""
    f = DIR / "usdlkr.json"
    if not f.exists():
        return []
    rows = [r for r in json.loads(f.read_text()) if r[1] and r[1] > 20]
    keep = []
    for i, r in enumerate(rows):
        if 0 < i < len(rows) - 1:
            a, b, c = rows[i - 1][1], r[1], rows[i + 1][1]
            if abs(b / a - 1) > 0.05 and abs(c / a - 1) < 0.02:
                continue
        keep.append(r)
    return keep


def build(signals: list[dict] | None = None) -> dict | None:
    usd = _usd()
    tb = json.loads((DIR / "tbill.json").read_text())["auctions"] if (DIR / "tbill.json").exists() else {}
    pol = []
    if (DIR / "policy.csv").exists():
        import csv
        pol = [[r["date"], float(r["opr"]), r["note"], r["source"]] for r in csv.DictReader((DIR / "policy.csv").open())]
    if not usd and not tb and not pol:
        return None
    out = {"made": dt.date.today().isoformat(), "policy": pol, "policy_checked": LAST_CONFIRMED[0], "policy_checked_src": LAST_CONFIRMED[1],
           "tbill": [[d, a.get("91"), a.get("182"), a.get("364")] for d, a in sorted(tb.items())]}
    if usd:                                                   # weekly closes keep the file small
        wk, last = [], None
        for d, v in usd:
            w = dt.date.fromisoformat(d).isocalendar()[:2]
            if w != last:
                wk.append([d, v]); last = w
            else:
                wk[-1] = [d, v]
        out["usd"] = wk
        def ago(days):
            cut = (dt.date.fromisoformat(usd[-1][0]) - dt.timedelta(days=days)).isoformat()
            p = next((v for d, v in reversed(usd) if d <= cut), None)
            return round((usd[-1][1] / p - 1) * 100, 1) if p else None
        out["usd_chg"] = {"1 month": ago(30), "1 year": ago(365), "5 years": ago(1826), "10 years": ago(3652)}
    L = {}
    if out["tbill"]:
        d, a, b, c = out["tbill"][-1]
        L.update(tb_date=d, tb91=a, tb182=b, tb364=c)
        yr = [r for r in out["tbill"] if r[0] <= (dt.date.fromisoformat(d) - dt.timedelta(days=360)).isoformat()]
        if yr and yr[-1][3] and c:
            L["tb364_year_ago"] = yr[-1][3]
    if pol:
        L.update(opr=pol[-1][1], opr_since=pol[-1][0])
    if usd:
        L.update(usd=usd[-1][1], usd_date=usd[-1][0])
    out["latest"] = L
    if signals:                                               # the share market's own yields, from checked figures
        rated = [x for x in signals if x.get("score") is not None]
        dys = sorted(x["div_yield"] for x in rated if x.get("div_yield"))
        pes = sorted(x["pe"] for x in rated if x.get("pe") and x["pe"] > 0)
        med = lambda v: v[len(v) // 2] if v else None
        if dys:
            out["market"] = {"div_yield": round(med(dys), 2), "div_n": len(dys), "earn_yield": round(100 / med(pes), 2) if pes else None, "pe": round(med(pes), 1) if pes else None,
                             "pe_n": len(pes), "above_tbill": sum(1 for v in dys if L.get("tb364") and v > L["tb364"])}
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "probe":
        probe()
    elif cmd == "fetch":
        fetch()
    else:
        print(json.dumps(build(), indent=1)[:2000])
