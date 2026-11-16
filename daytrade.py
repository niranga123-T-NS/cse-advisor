"""Day trading watch: shares where a same-day trade is at least physically possible on the CSE, and an honest test.

A share qualifies when it trades enough money to get in and out the same day (20-day average turnover LKR 20M+),
moves enough in a typical day to clear the costs (average high-low range 3%+ of the previous close; buying and
selling costs about 2.24% together), and costs at least LKR 10 (below that one price step is a large part of the move).

Setups from yesterday's bar:
  momentum   closed in the top 30% of its day's range on 1.5x normal volume
  dip        closed in the bottom 30% of its range on 1.5x normal volume (buyers may step in)

The test uses the past year of daily bars. CSE's daily history here has no real opening price, so entry is taken at
the previous close, which is close to where the CSE opens most days; exits at the close, or at a target / stop judged
from the day's high and low (when both are touched the stop is assumed to come first).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import db

COST = 2 * 0.0112          # buying + selling, as in the trade planner
MIN_TOV, MIN_ADR, MIN_PX = 20e6, 0.03, 10.0


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pc = df.close.shift(1)
    df["rng"] = (df.high - df.low) / pc
    df["adr20"] = df.rng.rolling(20).mean()
    df["tov20"] = (df.close * df.volume).rolling(20).mean()
    df["vol20"] = df.volume.rolling(20).mean()
    df["pos"] = ((df.close - df.low) / (df.high - df.low)).where(df.high > df.low)
    df["vr"] = df.volume / df.vol20.shift(1)
    df["big"] = (df.rng >= 0.03).rolling(60).mean()
    df["ret"] = df.close / pc - 1
    df["hi"] = df.high / pc - 1
    df["lo"] = df.low / pc - 1
    return df


def _setup(pos, vr) -> str:
    if pos is None or vr is None or np.isnan(pos) or np.isnan(vr) or vr < 1.5:
        return ""
    return "momentum" if pos >= 0.7 else "dip" if pos <= 0.3 else ""


def build(names: dict) -> dict:
    hist, today = [], []
    for s in db.symbols_with_data(60):
        f = _frame(db.load_bars(s))
        if len(f) < 60:
            continue
        y = f.shift(1)                         # what was known before each day's open
        t = pd.DataFrame({"s": s, "tov": y.tov20, "adr": y.adr20, "px": y.close, "pos": y.pos, "vr": y.vr,
                          "ret": f.ret, "hi": f.hi, "lo": f.lo}).dropna()
        hist.append(t)
        last = f.iloc[-1]
        if last.tov20 >= MIN_TOV and last.adr20 >= MIN_ADR and last.close >= MIN_PX:
            st = _setup(last.pos, last.vr)
            today.append({"s": s, "name": names.get(s, ""), "px": round(float(last.close), 2), "adr": round(float(last.adr20) * 100, 2),
                          "tov": round(float(last.tov20)), "big": round(float(last.big) * 100) if not np.isnan(last.big) else None,
                          "vol20": round(float(last.vol20)), "setup": st,
                          "pos": None if np.isnan(last.pos) else round(float(last.pos), 2), "vr": round(float(last.vr), 2)})
    H = pd.concat(hist) if hist else pd.DataFrame()
    test = {}
    if len(H):
        C = H[(H.tov >= MIN_TOV) & (H.adr >= MIN_ADR) & (H.px >= MIN_PX)]
        groups = {"all": C, "momentum": C[(C.pos >= 0.7) & (C.vr >= 1.5)], "dip": C[(C.pos <= 0.3) & (C.vr >= 1.5)]}
        def stats(X, tgt=None, stp=None):
            if not len(X):
                return None
            r = X.ret if tgt is None else pd.Series(np.where(X.lo <= -stp, -stp, np.where(X.hi >= tgt, tgt, X.ret)), index=X.index)
            net = r - COST
            return {"n": int(len(X)), "gross": round(float(r.mean()) * 100, 2), "net": round(float(net.mean()) * 100, 2),
                    "win": round(float((net > 0).mean()) * 100), "reach3": round(float((X.hi >= 0.03).mean()) * 100)}
        test = {"from": str(H.index.min().date()), "to": str(H.index.max().date()), "cost": round(COST * 100, 2),
                "close": {k: stats(v) for k, v in groups.items()},
                "target": {k: stats(v, 0.04, 0.02) for k, v in groups.items()}}
    rank = {"momentum": 0, "dip": 1, "": 2}
    today.sort(key=lambda x: (rank[x["setup"]], -x["adr"] * np.sqrt(x["tov"])))
    return {"rules": {"tov": MIN_TOV, "adr": MIN_ADR * 100, "px": MIN_PX}, "test": test, "list": today[:30], "n": len(today)}


if __name__ == "__main__":
    import json
    r = build(db.security_names())
    print(json.dumps(r["test"], indent=1)); print(r["n"], "shares qualify;", [(x["s"], x["setup"]) for x in r["list"][:10]])
