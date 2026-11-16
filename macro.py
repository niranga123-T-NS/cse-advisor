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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "probe":
        probe()
