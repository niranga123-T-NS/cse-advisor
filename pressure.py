"""Buying and selling pressure from the daily bars: is a share being bought or sold quietly, before the price shows it?

Nobody outside the exchange can see who is behind an order. What the daily bars do show is whether volume has been
arriving on strength or on weakness, and whether the price has responded yet. Two standard measures are used:

  money flow (Chaikin, 20 days)   each day's volume counts as buying when the close was in the upper part of the day's
                                  range and as selling when it was in the lower part: -1 (all selling) .. +1 (all buying)
  on-balance volume (20 days)     volume on up-days minus volume on down-days, in days of normal volume

Labels (thresholds fixed in advance, not tuned to results)
  acc    quiet buying   money flowing in while the price is flat or lower   -> possible hidden accumulation
  dist   quiet selling  money flowing out while the price is flat or higher -> possible hidden distribution
  buy / sell            pressure and price agree: visible, nothing hidden

    python pressure.py          # measure what followed each label over the saved history -> data/model/pressure.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

N = 20                    # trading days looked back
CMF_MIN = 0.10            # money flow beyond +/- this counts as pressure
OBV_MIN = 2.0             # ... and on-balance volume beyond +/- this many days of normal volume
FLAT = 3.0                # a 20-day price change within this many % (against the pressure) still counts as "quiet"
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "model" / "pressure.json"
TEXT = {
    "acc": "Quiet buying: volume has been arriving on strength while the price is flat or lower (possible hidden accumulation)",
    "dist": "Quiet selling: volume has been arriving on weakness while the price is flat or higher (possible hidden distribution)",
    "buy": "Buying pressure, and the price is rising with it",
    "sell": "Selling pressure, and the price is falling with it",
    "": "No clear buying or selling pressure",
}


def measures(d: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"].fillna(0.0)
    rng = (h - l).replace(0, np.nan)
    mfm = (((c - l) - (h - c)) / rng).fillna(0.0)             # where in the day's range it closed, -1..+1
    vsum = v.rolling(N).sum().replace(0, np.nan)
    r1 = c.diff()
    out = pd.DataFrame(index=d.index)
    out["cmf"] = (mfm * v).rolling(N).sum() / vsum
    obv = (np.sign(r1.fillna(0.0)) * v).cumsum()
    out["obv"] = (obv - obv.shift(N)) / v.rolling(N).mean().replace(0, np.nan)
    out["chg"] = (c / c.shift(N) - 1) * 100
    moved = v.where(r1 != 0, 0.0).rolling(N).sum().replace(0, np.nan)
    out["upvol"] = v.where(r1 > 0, 0.0).rolling(N).sum() / moved * 100       # % of volume traded on up-days
    return out


def label(cmf: float, obv: float, chg: float) -> str:
    if any(x is None or x != x for x in (cmf, obv, chg)):
        return ""
    if cmf >= CMF_MIN and obv >= OBV_MIN:
        return "acc" if chg <= FLAT else "buy"
    if cmf <= -CMF_MIN and obv <= -OBV_MIN:
        return "dist" if chg >= -FLAT else "sell"
    return ""


def labels(m: pd.DataFrame) -> pd.Series:
    return pd.Series([label(a, b, c) for a, b, c in zip(m["cmf"], m["obv"], m["chg"])], index=m.index)


def read(df: pd.DataFrame) -> dict | None:
    """The latest reading for one share (None when there is too little history)."""
    if len(df) < N + 5:
        return None
    m = measures(df)
    r = m.iloc[-1]
    if r[["cmf", "obv", "chg"]].isna().any():
        return None
    lab = label(r["cmf"], r["obv"], r["chg"])
    lb = labels(m.tail(15))
    days = 0                                   # how many trading days in a row the label has held
    for x in reversed(lb.tolist()):
        if x != lab:
            break
        days += 1
    return {"label": lab, "text": TEXT[lab], "cmf": round(float(r["cmf"]), 2), "obv": round(float(r["obv"]), 1),
            "chg": round(float(r["chg"]), 2), "upvol": None if r["upvol"] != r["upvol"] else round(float(r["upvol"]), 0),
            "days": days if lab else 0}


# ------------------------------------------------------------------ what followed each label in the past

def study(horizons=(5, 10, 20), boot: int = 400) -> dict:
    """Forward results of every label on tradeable shares. Same trade as the pattern forecast: buy at the next
    day's close, sell H trading days later; 'gained' means up after the round-trip cost."""
    import forecast as fc
    rows = []
    for s, d in fc.load_panel().items():
        if len(d) < 80:
            continue
        m = measures(d)
        c, v = d["close"], d["volume"]
        f = pd.DataFrame({"sym": s, "label": labels(m), "tov": (c * v).rolling(20).mean(), "px": c,
                          "traded": d["traded"].rolling(20).mean()})
        for h in horizons:
            f[f"r{h}"] = (c.shift(-(h + 1)) / c.shift(-1) - 1) * 100
        rows.append(f.reset_index(names="date"))
    X = pd.concat(rows, ignore_index=True)
    X = X[(X["tov"] >= fc.MIN_TURNOVER) & (X["px"] >= fc.MIN_PRICE) & (X["traded"] >= fc.MIN_TRADED)]
    cost = fc.COST * 100
    rng = np.random.default_rng(7)
    out = {"made": pd.Timestamp.now(tz="Asia/Colombo").strftime("%Y-%m-%d %H:%M"), "cost_pct": round(cost, 2),
           "lookback_days": N, "rules": {"money_flow": CMF_MIN, "obv_days": OBV_MIN, "flat_pct": FLAT}, "horizons": {}}
    for h in horizons:
        col = f"r{h}"
        Y = X.dropna(subset=[col])
        if Y.empty:
            continue
        days = Y["date"].unique()
        by_day = {d_: g for d_, g in Y.groupby("date")}
        res = {"period": [str(Y["date"].min().date()), str(Y["date"].max().date())], "days": int(len(days))}

        def summ(g):
            return {"n": int(len(g)), "avg": round(float(g[col].mean()), 2), "median": round(float(g[col].median()), 2),
                    "up": round(float((g[col] > 0).mean() * 100), 1), "gain": round(float((g[col] > cost).mean() * 100), 1)}
        res["all"] = summ(Y)
        for lab in ("acc", "dist", "buy", "sell"):
            g = Y[Y["label"] == lab]
            if len(g) < 30:
                continue
            r = summ(g)
            r["shares"] = int(g["sym"].nunique())
            diffs = []                                   # shares flagged on the same day move together: resample whole days
            for _ in range(boot):
                pick = rng.choice(days, len(days))
                B = pd.concat([by_day[d_] for d_ in pick], ignore_index=True)
                gb = B[B["label"] == lab]
                if len(gb) >= 10:
                    diffs.append(gb[col].mean() - B[col].mean())
            r["edge"] = round(r["avg"] - res["all"]["avg"], 2)
            if len(diffs) > 50:
                r["edge_ci"] = [round(float(np.percentile(diffs, 2.5)), 2), round(float(np.percentile(diffs, 97.5)), 2)]
            res[lab] = r
        out["horizons"][str(h)] = res
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    o = study()
    for h, r in o["horizons"].items():
        print(f"--- {h} trading days ({r['period'][0]} .. {r['period'][1]}, {r['days']} days)")
        for k in ("all", "acc", "dist", "buy", "sell"):
            if k in r:
                x = r[k]
                print(f"  {k:5} n={x['n']:6}  avg {x['avg']:+6.2f}%  median {x['median']:+6.2f}%  up {x['up']:5.1f}%  "
                      f"gain after costs {x['gain']:5.1f}%  edge {x.get('edge', 0):+.2f} {x.get('edge_ci', '')}  shares {x.get('shares', '')}")
