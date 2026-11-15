"""Long price history (10-20 years) for every counter.

CSE's own site serves one year of daily prices, for shares and for the ASPI alike. Older years come from Yahoo
Finance, which holds daily prices for most CSE shares back to 2000-2008 (JKH.N0000 is JKH-N0000.CM there).

Yahoo is a free, unofficial source and its Colombo data has known faults, so nothing from it is used as it comes:
  * its feed stopped updating in late 2025 and repeats the last price on every weekday since  -> rows without
    volume are dropped (a day with no trades has no bar, as in the CSE data);
  * some share splits are listed but were never applied to the earlier prices                 -> each listed split is
    checked against the prices around it and applied here when the jump is still in the data;
  * the join with CSE's own prices (which take over from the first day we have) is checked    -> a share whose two
    histories do not meet is flagged and its older years are not used.
The cleaning and checks are in longterm.py; this file only downloads and stores what Yahoo returns.

    python longhist.py fetch            # every counter -> data/long/raw/<SYMBOL>.csv.gz + data/long/yahoo_meta.json
    python longhist.py fetch JKH.N0000  # only these
    python longhist.py aspi             # one year of official daily ASPI from CSE -> data/index/ASPI.csv
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import sys
import time
from pathlib import Path

import requests

import db

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "long" / "raw"
META = ROOT / "data" / "long" / "yahoo_meta.json"
INDEX = ROOT / "data" / "index"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*"}


def yahoo_ticker(symbol: str) -> str:
    return symbol.replace(".", "-") + ".CM"


def fetch_one(symbol: str, session: requests.Session) -> tuple[list[list], dict]:
    """Everything Yahoo holds for one counter: rows [date, open, high, low, close, adjclose, volume] and a summary."""
    tk = yahoo_ticker(symbol)
    info: dict = {"yahoo": tk, "fetched": dt.date.today().isoformat()}
    r = None
    for attempt in range(4):
        try:
            r = session.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}", headers=UA, timeout=40,
                            params={"period1": 0, "period2": int(time.time()), "interval": "1d", "events": "div,split"})
        except requests.RequestException as e:
            info["status"] = f"error {type(e).__name__}"
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503):
            info["status"] = f"http {r.status_code}"
            time.sleep(20 * (attempt + 1))
            continue
        break
    if r is None or r.status_code != 200:
        info["status"] = info.get("status") or (f"http {r.status_code}" if r is not None else "error")
        if r is not None and r.status_code == 404:
            info["status"] = "not on Yahoo"
        return [], info
    try:
        res = r.json()["chart"]["result"][0]
    except (ValueError, KeyError, TypeError, IndexError):
        info["status"] = "no data"
        return [], info
    off = (res.get("meta") or {}).get("gmtoffset") or 19800
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    adj = (((res.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or [None] * len(ts)
    day = lambda t: dt.datetime.fromtimestamp(t + off, dt.timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for i, t in enumerate(ts):
        c = (q.get("close") or [None] * len(ts))[i]
        if c is None:
            continue
        rows.append([day(t)] + [(q.get(k) or [None] * len(ts))[i] for k in ("open", "high", "low")] + [c, adj[i], (q.get("volume") or [None] * len(ts))[i]])
    ev = res.get("events") or {}
    info.update(status="ok" if rows else "empty", rows=len(rows), first=rows[0][0] if rows else None, last=rows[-1][0] if rows else None,
                name=(res.get("meta") or {}).get("longName") or (res.get("meta") or {}).get("shortName"),
                currency=(res.get("meta") or {}).get("currency"),
                splits=sorted([day(int(k)), s.get("numerator"), s.get("denominator")] for k, s in (ev.get("splits") or {}).items()),
                dividends=sorted([day(int(k)), d.get("amount")] for k, d in (ev.get("dividends") or {}).items()))
    return rows, info


def save_raw(symbol: str, rows: list[list]):
    RAW.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    buf.write("date,open,high,low,close,adjclose,volume\n")
    f = lambda v: "" if v is None else (f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
    for r in rows:
        buf.write(",".join([r[0]] + [f(v) for v in r[1:]]) + "\n")
    with open(RAW / f"{symbol}.csv.gz", "wb") as fh:                 # mtime=0: the same data always gives the same file
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
            gz.write(buf.getvalue().encode())


def fetch(symbols: list[str] | None = None):
    symbols = symbols or sorted(set(db.symbols_with_data(1)) | set(db.security_names()))
    meta = json.loads(META.read_text()) if META.exists() else {}
    s = requests.Session()
    ok = 0
    for i, sym in enumerate(symbols):
        rows, info = fetch_one(sym, s)
        if rows:
            save_raw(sym, rows)
            ok += 1
        meta[sym] = info
        print(f"{i + 1:3}/{len(symbols)} {sym:12} {info.get('status'):14} {info.get('rows') or 0:5} days  {info.get('first') or ''} .. {info.get('last') or ''}"
              f"  splits {len(info.get('splits') or [])}", flush=True)
        time.sleep(1.2)
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta, indent=0, sort_keys=True))
    print(f"Yahoo history saved for {ok} of {len(symbols)} counters")


def aspi():
    """Official daily ASPI for the last year (all CSE serves), merged into data/index/ASPI.csv so it builds up from here."""
    import cse_client as cse
    j = cse.post("chartData", {"symbol": "ASPI", "chartId": 1, "period": 5}, retries=2)
    pts = j if isinstance(j, list) else []
    INDEX.mkdir(parents=True, exist_ok=True)
    p = INDEX / "ASPI.csv"
    have = {}
    if p.exists():
        for line in p.read_text().splitlines()[1:]:
            d, v = line.split(",")
            have[d] = v
    for x in pts:
        if x.get("d") and x.get("v"):
            have[cse.colombo_date(x["d"])] = f"{float(x['v']):.2f}"
    p.write_text("date,close\n" + "".join(f"{d},{v}\n" for d, v in sorted(have.items())))
    print(f"ASPI: {len(pts)} points from CSE, {len(have)} days on file ({min(have) if have else ''} .. {max(have) if have else ''})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "aspi":
        aspi()
    else:
        fetch([x.upper() for x in sys.argv[2:]] or None)
