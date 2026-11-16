"""Interest rates and the rupee, for the Market page.

Sources, all collected in GitHub Actions (this repo's own workspace cannot reach them):
  - USD/LKR, daily: Yahoo Finance (LKR=X), unofficial.
  - Policy rate, T-bill yields (primary market auction, 91/182/364 days) and the prime lending rate (AWPR):
    the Central Bank's Weekly Economic Indicators (WEI), a PDF published every Friday at a predictable address.

  python macro.py probe        save the extracted text of recent WEIs to data/macro/raw/ (to check the parser)
  python macro.py fetch [from] collect WEIs (default: the last 8 weeks; 'from' as YYYY-MM-DD backfills) and the rupee
  python macro.py build        write site/data/macro.json from what has been collected
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
WEI = "https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/statistics/wei/WEI_{d}_e.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in r.pages[:6])


def get_wei(day: dt.date, s: requests.Session) -> bytes | None:
    try:
        r = s.get(WEI.format(d=day.strftime("%Y%m%d")), headers=UA, timeout=60)
    except requests.RequestException:
        return None
    return r.content if r.status_code == 200 and r.content[:4] == b"%PDF" else None


def probe():
    out = DIR / "raw"
    out.mkdir(parents=True, exist_ok=True)
    s, got, day = requests.Session(), 0, dt.date.today()
    for back in range(0, 120):
        d = day - dt.timedelta(days=back)
        if d.weekday() not in (3, 4, 5):          # WEIs carry Friday's date; sometimes Thursday before a holiday
            continue
        raw = get_wei(d, s)
        print(d, "found" if raw else "-")
        if raw:
            (out / f"WEI_{d:%Y%m%d}.txt").write_text(pdf_text(raw))
            got += 1
            if got >= 3:
                break
    for d in ("2018-03-02", "2021-06-04", "2024-03-01"):               # older layouts too
        dd = dt.date.fromisoformat(d)
        for k in range(0, 7):
            raw = get_wei(dd + dt.timedelta(days=k), s)
            if raw:
                (out / f"WEI_{dd + dt.timedelta(days=k):%Y%m%d}.txt").write_text(pdf_text(raw)); print("old", dd + dt.timedelta(days=k)); break
    rows = fx(s)
    print("fx rows", len(rows), rows[:1], rows[-1:])


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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "probe":
        probe()
