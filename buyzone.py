"""Best price to buy, technically: a limit price a little below today's close, snapped to a support level.

Tested on every share's history since 2014 (a buy order left for 10 trading days, counted as filled only if the
price traded at least one step below it, valued 30 trading days after the signal, 1.12% costs each way),
against buying at the next day's open:
  * Rising trend (close above the 50-day average): half to one typical daily range (ATR) below the close.
    It filled about 6 times in 10; when it filled it beat buying at once on the same days 8 times in 10.
    But the missed chances had run up, so over all signals waiting did NOT beat buying at once.
  * Falling trend: buying at once lost money on average. Two daily ranges below filled about 1 time in 5 and
    roughly broke even, so that price is shown there, with a warning.
  * Whether several support levels agree made no difference in testing, so levels are only used to pick a
    visible price inside the tested window, not to judge strength.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "model" / "buyzone.json"
COSTP = 0.0112                 # each way
WAIT, HOLD = 10, 30            # order left for 10 trading days; both ways valued 30 trading days after the signal
NEAR, LO, HI = 0.015, 0.005, 0.10


def tick_size(p: float) -> float:
    return 0.1 if p < 100 else 0.25 if p < 1000 else 1.0     # CSE price steps (approximate)


def _round_down(x: float) -> float:
    t = tick_size(x)
    return round(math.floor(x / t + 1e-9) * t, 2)


def _features(d: pd.DataFrame) -> pd.DataFrame:
    c, h, l = d["close"], d["high"], d["low"]
    prev = c.shift()
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=d.index)
    f["atr"] = tr.rolling(14).mean()
    f["sma20"], f["sma50"] = c.rolling(20).mean(), c.rolling(50).mean()
    f["low20"] = l.rolling(20).min()
    hi60, lo60 = h.rolling(60).max(), l.rolling(60).min()
    rng = hi60 - lo60
    for k in (0.382, 0.5, 0.618):
        f[f"fib{int(k * 1000)}"] = hi60 - k * rng
    return f


def _vol_band(d: pd.DataFrame, i: int, price: float, bins: int = 24) -> float | None:
    """Middle of the heaviest volume band below the price over the past year (volume spread over each day's range)."""
    w = d.iloc[max(0, i - 249):i + 1]
    lo, hi = float(w["low"].min()), float(w["high"].max())
    if not (hi > lo > 0):
        return None
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for a, b, v in zip(w["low"].to_numpy(float), w["high"].to_numpy(float), w["volume"].to_numpy(float)):
        if not v or not np.isfinite(v):
            continue
        ia, ib = np.searchsorted(edges, a, "right") - 1, np.searchsorted(edges, b, "left")
        ia, ib = max(0, min(bins - 1, ia)), max(ia + 1, min(bins, ib))
        vol[ia:ib] += v / (ib - ia)
    mids = (edges[:-1] + edges[1:]) / 2
    ok = mids < price * (1 - LO)
    if not ok.any():
        return None
    j = int(np.argmax(np.where(ok, vol, -1)))
    return float(mids[j])


def suggest(d: pd.DataFrame, f: pd.DataFrame, i: int, with_band: bool = True) -> dict | None:
    r, price = f.iloc[i], float(d["close"].iloc[i])
    atr = float(r["atr"]) if pd.notna(r["atr"]) else 0.0
    if not (price > 0) or not atr > 0:
        return None
    up = bool(pd.notna(r["sma50"]) and price > r["sma50"])
    k0, k1 = (0.5, 1.0) if up else (1.5, 2.5)                 # the tested window, in daily ranges below the close
    hi, lo = price - k0 * atr, price - k1 * atr
    lv = {"20-day average": r["sma20"], "50-day average": r["sma50"], "20-day low": r["low20"],
          "Fibonacci 38.2%": r["fib382"], "Fibonacci 50%": r["fib500"], "Fibonacci 61.8%": r["fib618"]}
    if with_band:
        vb = _vol_band(d, i, price)
        if vb:
            lv["busiest volume band"] = vb
    inside = {k: float(v) for k, v in lv.items() if pd.notna(v) and lo <= v <= hi}
    if inside:
        top = max(inside.values())                            # the highest support in the window fills most often
        why = [k for k, v in inside.items() if abs(v / top - 1) <= NEAR]
        px = top
    else:
        px, why = (hi if up else price - 2 * atr), []
    px = _round_down(px)
    if not (0 < px < price):
        return None
    return {"price": px, "below_pct": round((1 - px / price) * 100, 2), "atr_away": round((price - px) / atr, 2),
            "levels": why, "trend": "up" if up else "down"}


def fill_chance(d: pd.DataFrame, below: float, years: int = 3) -> float | None:
    """Share of past days (last few years) on which the low of the next 10 days reached this far below the close."""
    w = d.iloc[-250 * years:]
    c, l = w["close"].to_numpy(float), w["low"].to_numpy(float)
    if len(c) < 120:
        return None
    hit = [l[t + 1:t + 1 + WAIT].min() <= c[t] * (1 - below / 100) for t in range(len(c) - WAIT)]
    return round(100 * float(np.mean(hit)), 0)


def study(bars: dict[str, pd.DataFrame], start: str = "2014-01-01", step: int = 5) -> dict:
    rows = []
    for s, d in bars.items():
        d = d[d["close"] > 0]
        if len(d) < 300:
            continue
        f = _features(d)
        o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        idx = np.where(d.index >= pd.Timestamp(start))[0]
        for i in idx[::step]:
            if i < 260 or i + HOLD >= len(d):
                continue
            sg = suggest(d, f, i, with_band=False)
            if not sg:
                continue
            exitp = c[i + HOLD] * (1 - COSTP)
            mk_in = (o[i + 1] if o[i + 1] > 0 else c[i]) * (1 + COSTP)
            filled = None
            for j in range(i + 1, i + 1 + WAIT):
                if l[j] <= sg["price"] - tick_size(sg["price"]) + 1e-9:   # traded below it, so the order surely filled
                    filled = min(sg["price"], o[j] if o[j] > 0 else sg["price"]) * (1 + COSTP)
                    break
            rows.append((sg["trend"] == "up", sg["atr_away"], filled is not None,
                         (exitp / filled - 1) * 100 if filled else np.nan, (exitp / mk_in - 1) * 100))
    t = pd.DataFrame(rows, columns=["grp", "atr", "fill", "lim", "mkt"])
    t = t[np.isfinite(t["mkt"]) & (t["mkt"].abs() < 300)]

    def summ(x: pd.DataFrame) -> dict:
        fx = x[x["fill"]]
        return {"n": int(len(x)), "fill_pct": round(100 * x["fill"].mean(), 1),
                "limit_when_filled": round(float(fx["lim"].mean()), 2), "market_same_days": round(float(fx["mkt"].mean()), 2),
                "market_when_missed": round(float(x[~x["fill"]]["mkt"].mean()), 2), "market_all": round(float(x["mkt"].mean()), 2),
                "limit_overall": round(float(np.where(x["fill"], x["lim"], 0).mean()), 2),
                "better_when_filled_pct": round(100 * float((fx["lim"] > fx["mkt"]).mean()), 1)}
    return {"wait_days": WAIT, "hold_days": HOLD, "cost_each_way": COSTP, "from": start,
            "all": summ(t), "up": summ(t[t["grp"]]), "down": summ(t[~t["grp"]])}


def build(bars: dict[str, pd.DataFrame], symbols: list[str]) -> dict:
    out = {}
    for s in symbols:
        d = bars.get(s)
        if d is None or len(d) < 60:
            continue
        d = d[d["close"] > 0]
        f = _features(d)
        sg = suggest(d, f, len(d) - 1)
        if not sg:
            continue
        sg["fill_chance"] = fill_chance(d, sg["below_pct"])
        sg["close"] = round(float(d["close"].iloc[-1]), 2)
        sg["date"] = d.index[-1].strftime("%Y-%m-%d")
        out[s] = sg
    return out


if __name__ == "__main__":
    import longterm
    meta = longterm.load_meta()
    bars = {}
    for p in sorted((ROOT / "data" / "long" / "raw").glob("*.csv.gz")):
        s = p.name[:-7]
        try:
            bars[s] = longterm.load(s, meta)[0]
        except Exception as e:
            print("skip", s, e)
    res = study(bars)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
