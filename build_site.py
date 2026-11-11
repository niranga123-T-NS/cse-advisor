"""Fetch CSE data and build the static website in site/ (what GitHub Pages serves).

    python build_site.py                      # snapshot today + backfill short watchlist histories + build
    python build_site.py --backfill JKH.N0000 COMB.N0000
    python build_site.py --backfill-all       # full history for every stock on the board (~10 min)
    python build_site.py --no-fetch           # rebuild the site from saved data only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import shutil
from pathlib import Path

import analysis
import collector
import cse_client as cse
import db

SITE = Path(__file__).parent / "site"
OUT = SITE / "data"


def _clean(o):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 4)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), separators=(",", ":")))


def stock_payload(symbol: str, df) -> dict:
    d = analysis.indicators(df)
    t = [ts.strftime("%Y-%m-%d") for ts in d.index]

    def series(col):
        return [{"time": ti, "value": float(v)} for ti, v in zip(t, d[col]) if v == v]

    return {
        "symbol": symbol,
        "candles": [{"time": ti, "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for ti, r in zip(t, d.itertuples())],
        "volume": [{"time": ti, "value": r.volume} for ti, r in zip(t, d.itertuples())],
        "sma20": series("sma20"), "sma50": series("sma50"),
        "rsi": series("rsi"), "macd_hist": series("macd_hist"),
        "advice": analysis.advice(df),
        "backtest": analysis.backtest(df),
    }


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    names, watch = db.security_names(), set(db.watchlist())
    signals = []
    for s in db.symbols_with_data(30):
        df = db.load_bars(s)
        p = stock_payload(s, df)
        if not p["advice"]:
            continue
        _dump(OUT / "stocks" / f"{s}.json", p)
        bt = p["backtest"] or {}
        signals.append({"symbol": s, "name": names.get(s, ""), "watch": s in watch,
                        "bars": len(df), "bt_edge": None if not bt else
                        round(bt["strategy_return_pct"] - bt["buy_hold_return_pct"], 1), **p["advice"]})
    signals.sort(key=lambda x: (not x["watch"], -x["score"]))
    _dump(OUT / "signals.json", signals)

    meta = {"updated": dt.datetime.now(collector.COLOMBO).strftime("%Y-%m-%d %H:%M"), "stocks": len(signals),
            "tracked": len(db.symbols_with_data(1)), "watchlist": sorted(watch), "rules": analysis.RULES}
    _dump(OUT / "meta.json", meta)
    print(f"Built site: {len(signals)} stocks with signals ({meta['tracked']} tracked)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--backfill", nargs="*", default=[])
    ap.add_argument("--backfill-all", action="store_true")
    a = ap.parse_args()

    if not a.no_fetch:
        collector.snapshot()
        requested = {s.strip().upper() for s in a.backfill if s.strip()}
        for s in requested:  # symbols you ask for are added to the watchlist
            db.set_watch(s, True)
        todo = set(requested)
        if a.backfill_all:
            todo |= {r["symbol"] for r in cse.trade_summary()}
        # watchlist stocks with short history get a full backfill automatically
        todo |= {s for s in db.watchlist() if db.bar_count(s) < 150}
        if todo:
            collector.backfill(sorted(todo))
    build()


if __name__ == "__main__":
    main()
