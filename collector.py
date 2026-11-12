"""Collect CSE data into the local database.

    python collector.py snapshot            # save today's board for every traded stock (run after 2:30pm)
    python collector.py backfill JKH.N0000  # pull ~1 year of daily history for given symbols
    python collector.py backfill --watchlist
    python collector.py import-csv FILE.csv SYMBOL   # import a CSV (e.g. exported from TradingView)
    python collector.py schedule            # stay running; snapshot every weekday at 14:45 Colombo time
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import pandas as pd

import cse_client as cse
import db

COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))


def today() -> str:
    return dt.datetime.now(COLOMBO).strftime("%Y-%m-%d")


def snapshot(force: bool = False) -> int:
    now = dt.datetime.now(COLOMBO)
    if not force and (now.weekday() >= 5 or (now.hour, now.minute) < (14, 35)):
        # Before today's close the board still shows the previous session; saving it now
        # would label yesterday's prices with today's date.
        print("Market hasn't closed yet today - skipping snapshot (history backfill still runs).")
        return 0
    rows = cse.trade_summary()
    if not rows:
        print("No data from tradeSummary (market closed/holiday, or the API changed - try probe.py).")
        return 0
    d = today()
    # Holiday guard: on non-trading days the board repeats the last session. If nearly every
    # stock's close and volume match what we already saved, don't store a duplicate day.
    same = checked = 0
    for r in rows[:80]:
        last = db.load_bars(r["symbol"]).tail(1)
        if len(last) and last.index[-1].strftime("%Y-%m-%d") != d:
            checked += 1
            same += abs(last.close.iloc[0] - r["close"]) < 1e-6 and abs(last.volume.iloc[0] - r["volume"]) < 1e-6
    if checked >= 10 and same / checked > 0.95:
        print("Board is identical to the last saved session - looks like a non-trading day, skipping.")
        return 0
    for r in rows:
        db.upsert_security(r["symbol"], r["name"], r["stock_id"])
        db.upsert_bars(r["symbol"], [{**r, "date": d}])
    new = {r["symbol"] for r in rows} - set(db.watchlist())
    if new:
        db.add_watch(new)
        print(f"New listings added to watchlist: {', '.join(sorted(new))}")
    print(f"Saved {len(rows)} bars for {d}")
    return len(rows)


def backfill(symbols: list[str], period: int = 5):
    for s in symbols:
        bars = cse.history(s, period)
        n = db.upsert_bars(s, bars)
        db.upsert_security(s)
        print(f"{s}: {n} daily bars" + ("" if n else "  (none - check symbol, or run probe.py)"))
        if n:
            db.mark_backfilled([s], today())


def import_csv(path: str, symbol: str):
    """Accepts TradingView exports (time,open,high,low,close,Volume) or any CSV with date+OHLC columns."""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    tcol = cols.get("time") or cols.get("date") or cols.get("datetime")
    if not tcol:
        raise SystemExit("CSV needs a time/date column")
    t = df[tcol]
    dates = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    dates = dates.dt.tz_convert("Asia/Colombo").dt.strftime("%Y-%m-%d")

    def col(name, fallback=None):
        c = cols.get(name)
        return df[c].astype(float) if c else fallback

    close = col("close")
    if close is None:
        raise SystemExit("CSV needs a close column")
    bars = pd.DataFrame({
        "date": dates,
        "open": col("open", close), "high": col("high", close), "low": col("low", close),
        "close": close, "volume": col("volume", pd.Series(0.0, index=df.index)),
    }).to_dict("records")
    n = db.upsert_bars(symbol, bars)
    db.upsert_security(symbol)
    print(f"Imported {n} bars for {symbol}")


def schedule():
    print("Scheduler running. Snapshots weekdays at 14:45 Colombo time. Ctrl+C to stop.")
    done_for = None
    while True:
        now = dt.datetime.now(COLOMBO)
        if now.weekday() < 5 and (now.hour, now.minute) >= (14, 45) and done_for != now.date():
            snapshot()
            done_for = now.date()
        time.sleep(60)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
    elif args[0] == "snapshot":
        snapshot(force="--force" in args)
    elif args[0] == "backfill":
        syms = db.watchlist() if args[1:] == ["--watchlist"] else args[1:]
        if not syms:
            raise SystemExit("Give symbols, e.g. backfill JKH.N0000 COMB.N0000")
        backfill(syms)
    elif args[0] == "import-csv" and len(args) == 3:
        import_csv(args[1], args[2])
    elif args[0] == "schedule":
        schedule()
    else:
        print(__doc__)
