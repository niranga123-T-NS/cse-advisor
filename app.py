"""Web dashboard.  Run:  python app.py   then open http://127.0.0.1:5000"""
from __future__ import annotations

import math

from flask import Flask, jsonify, render_template, request

import analysis
import collector
import db

app = Flask(__name__)


def _clean(v):
    return None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/signals")
def signals():
    """Signal for every stock with enough data (watchlist first)."""
    names, watch = db.security_names(), set(db.watchlist())
    only_watch = request.args.get("watch") == "1"
    out = []
    for s in db.symbols_with_data(30):
        if only_watch and s not in watch:
            continue
        a = analysis.advice(db.load_bars(s))
        if a:
            out.append({"symbol": s, "name": names.get(s, ""), "watch": s in watch, **a})
    out.sort(key=lambda x: (not x["watch"], -x["score"]))
    return jsonify(out)


@app.get("/api/stock/<symbol>")
def stock(symbol):
    df = db.load_bars(symbol)
    if df.empty:
        return jsonify({"error": "no data"}), 404
    d = analysis.indicators(df)
    t = [ts.strftime("%Y-%m-%d") for ts in d.index]

    def series(col):
        return [{"time": ti, "value": _clean(round(float(v), 4))} for ti, v in zip(t, d[col]) if v == v]

    return jsonify({
        "symbol": symbol,
        "candles": [{"time": ti, "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for ti, r in zip(t, d.itertuples())],
        "volume": [{"time": ti, "value": r.volume} for ti, r in zip(t, d.itertuples())],
        "sma20": series("sma20"), "sma50": series("sma50"),
        "rsi": series("rsi"), "macd_hist": series("macd_hist"),
        "advice": analysis.advice(df),
        "backtest": analysis.backtest(df),
        "watch": symbol in db.watchlist(),
    })


@app.post("/api/watch/<symbol>")
def watch(symbol):
    db.set_watch(symbol, request.json.get("on", True))
    return jsonify(ok=True)


@app.post("/api/refresh")
def refresh():
    """Snapshot today's board; optionally backfill one symbol's history."""
    n = collector.snapshot()
    sym = (request.json or {}).get("backfill")
    if sym:
        collector.backfill([sym])
    return jsonify(saved=n)


if __name__ == "__main__":
    app.run(debug=True)
