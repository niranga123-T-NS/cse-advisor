"""Day trading: which shares can be traded in and out the same day, and a tested signal for when to buy.

Universe (each day, from what is known before the open): the 40 shares with the most money traded over 20 days
that move 3%+ in a typical day and cost LKR 10+. Buying and selling costs about 2.24% together (as in the planner).

Signal: a gradient-boosting model of the chance that a share bought at the open closes more than 2.24% higher,
from yesterday's bar (range, close position, volume, 1/5/20-day returns, distance from the 20-day high, RSI),
the market (yesterday's average return, today's average gap), the weekday, its turnover rank, and its own
opening gap. It is trained on Yahoo's daily history (real opening prices) since 2012 and tested walk-forward:
each year is predicted by a model trained only on earlier years, with the buy cut-off also taken from those years.
The site gets, for every share in today's universe, the model's chance on a grid of opening gaps and market gaps,
so it can light the signal up live once the share opens.

Also reported: buying a 3%+ gap-down at the open and holding it for a few days, which tested better than selling
the same day because the costs are paid once against a bigger move.
"""
from __future__ import annotations

import datetime as dt
import glob
import os

import numpy as np
import pandas as pd

import db

COST = 2 * 0.0112
TOP, MIN_ADR, MIN_PX = 40, 0.03, 10.0
F = ["gap", "adr", "pos", "vr", "r1", "r5", "r20", "hi20", "rsi", "mr1", "mgap", "dow", "rank"]
LONG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "long", "raw")
GAPS = [round(g, 3) for g in np.arange(-0.10, 0.1001, 0.005)]
MGAPS = [round(g, 3) for g in np.arange(-0.02, 0.0201, 0.005)]


def features(d: pd.DataFrame, s: str) -> pd.DataFrame:
    """One row per day; everything except gap/oc/hold columns is known before that day's open."""
    pc = d.close.shift(1)
    tov = d.close * d.volume
    y = lambda v: v.shift(1)
    x = pd.DataFrame(index=d.index)
    x["s"] = s
    x["gap"] = d.open / pc - 1
    x["oc"] = d.close / d.open - 1
    for k in (3, 5):
        x[f"h{k}"] = d.close.shift(-(k - 1)) / d.open - 1
    x["adr"] = y(((d.high - d.low) / pc).rolling(20).mean())
    x["tov20"] = y(tov.rolling(20).mean())
    x["px"] = pc
    x["pos"] = y(((d.close - d.low) / (d.high - d.low)).where(d.high > d.low))
    x["vr"] = y(d.volume / d.volume.rolling(20).mean().shift(1))
    x["r1"] = y(d.close / pc - 1)
    x["r5"] = y(d.close / d.close.shift(5) - 1)
    x["r20"] = y(d.close / d.close.shift(20) - 1)
    x["hi20"] = y(d.close / d.high.rolling(20).max() - 1)
    dl = d.close.diff()
    up, dn = dl.clip(lower=0).rolling(14).mean(), (-dl.clip(upper=0)).rolling(14).mean()
    x["rsi"] = y(100 - 100 / (1 + up / dn.replace(0, np.nan)))
    x["dow"] = x.index.dayofweek
    return x


def _universe(X: pd.DataFrame) -> pd.DataFrame:
    mk = X.groupby(level=0).agg(mr1=("r1", "mean"), mgap=("gap", "mean"))
    X = X.join(mk, how="left")
    X["rank"] = X.groupby(level=0)["tov20"].rank(ascending=False)
    return X[(X["rank"] <= TOP) & (X.adr >= MIN_ADR) & (X.px >= MIN_PX)]


def history() -> pd.DataFrame:
    frames = []
    for f in glob.glob(os.path.join(LONG, "*.csv.gz")):
        d = pd.read_csv(f, parse_dates=["date"]).set_index("date").dropna(subset=["open", "high", "low", "close"])
        d = d[(d.volume > 0) & (d.index >= "2012-01-01")]
        if len(d) < 300:
            continue
        x = features(d, os.path.basename(f)[:-7])
        ok = (d.open >= d.low - 1e-9) & (d.open <= d.high + 1e-9) & (d.low > 0) & ((d.close / d.close.shift(1) - 1).abs() < 0.35)
        frames.append(x[ok])
    X = pd.concat(frames).dropna(subset=[c for c in F if c not in ("mr1", "mgap", "rank")] + ["oc", "tov20", "px"])
    U = _universe(X)
    U["year"] = U.index.year
    return U


def _model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05, min_samples_leaf=100, l2_regularization=1.0, random_state=0)


def _stats(Y: pd.DataFrame, col: str = "oc") -> dict | None:
    Y = Y.dropna(subset=[col])
    if not len(Y):
        return None
    net = Y[col] - COST
    return {"n": int(len(Y)), "gross": round(float(Y[col].mean()) * 100, 2), "net": round(float(net.mean()) * 100, 2),
            "win": round(float((net > 0).mean()) * 100)}


def study(U: pd.DataFrame) -> tuple[dict, object, float]:
    """Walk-forward test, then the model trained on everything for live use."""
    res = []
    for yr in range(2017, int(U.year.max()) + 1):
        tr, te = U[U.year < yr], U[U.year == yr]
        if len(te) < 30:
            continue
        m = _model().fit(tr[F], (tr.oc > COST).astype(int))
        cut = float(np.quantile(m.predict_proba(tr[F])[:, 1], 0.98))
        te = te.assign(p=m.predict_proba(te[F])[:, 1])
        res.append(te[te.p >= cut])
    S = pd.concat(res) if res else U.iloc[:0]
    B = U[U.year >= 2020]
    gd = lambda Z, g: Z[Z.gap <= -g]
    out = {
        "from": str(U.index.min().date()), "to": str(U.index.max().date()), "cost": round(COST * 100, 2),
        "all": _stats(U), "gapup": _stats(U[U.gap >= 0.02]),
        "model": _stats(S), "model_h3": _stats(S, "h3"),
        "model_years": {str(y): _stats(g) for y, g in S.groupby("year")},
        "gd3_recent": _stats(gd(B, 0.03)), "gd3_recent_h3": _stats(gd(B, 0.03), "h3"), "gd3_recent_h5": _stats(gd(B, 0.03), "h5"),
        "gd3_old": _stats(gd(U[U.year <= 2019], 0.03)),
    }
    m = _model().fit(U[F], (U.oc > COST).astype(int))
    cut = float(np.quantile(m.predict_proba(U[F])[:, 1], 0.98))
    out["cut"] = round(cut, 4)
    return out, m, cut


def next_trading_day(last: pd.Timestamp) -> pd.Timestamp:
    d = last + pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d += pd.Timedelta(days=1)
    return d


def build(names: dict) -> dict:
    U = history()
    test, model, cut = study(U)
    # today's universe from the CSE's own daily data (the stored opens there are not real, so gaps come live)
    rows = []
    for s in db.symbols_with_data(60):
        d = db.load_bars(s)
        if len(d) < 60:
            continue
        f = features(pd.concat([d, d.iloc[[-1]].assign(volume=np.nan)]).set_axis(list(d.index) + [next_trading_day(d.index[-1])]), s)
        rows.append(f.iloc[[-1]].assign(vol20=float(d.volume.tail(20).mean()), last=float(d.close.iloc[-1]),
                                        big=float((((d.high - d.low) / d.close.shift(1)) >= 0.03).tail(60).mean())))
    T = pd.concat(rows)
    T["mr1"] = T.r1.mean()
    T["rank"] = T.tov20.rank(ascending=False)
    T = T[(T["rank"] <= TOP) & (T.adr >= MIN_ADR) & (T.px >= MIN_PX)].copy()
    out = []
    for _, r in T.iterrows():
        grid = []
        for mg in MGAPS:
            Z = pd.DataFrame([{**{k: r[k] for k in F if k not in ("gap", "mgap")}, "gap": g, "mgap": mg} for g in GAPS])[F]
            grid.append([round(float(p), 3) for p in model.predict_proba(Z)[:, 1]])
        st = "momentum" if (r.pos >= 0.7 and r.vr >= 1.5) else "dip" if (r.pos <= 0.3 and r.vr >= 1.5) else ""
        out.append({"s": r.s, "name": names.get(r.s, ""), "px": round(r["last"], 2), "adr": round(r.adr * 100, 2),
                    "tov": round(r.tov20), "vol20": round(r.vol20), "big": round(r.big * 100), "setup": st, "rank": int(r["rank"]),
                    "grid": grid})
    out.sort(key=lambda x: x["rank"])
    return {"rules": {"top": TOP, "adr": MIN_ADR * 100, "px": MIN_PX}, "test": test, "cut": round(cut, 4),
            "gaps": GAPS, "mgaps": MGAPS, "for": str(next_trading_day(pd.Timestamp(dt.date.today())).date()),
            "list": out, "n": len(out)}


if __name__ == "__main__":
    import json
    r = build(db.security_names())
    print(json.dumps(r["test"], indent=1))
    print(r["n"], "in universe; cut", r["cut"])
    x = r["list"][0]; gi = r["gaps"].index(-0.03); mi = r["mgaps"].index(0.0)
    print(x["s"], "p at gap -3%", x["grid"][mi][gi], "at gap 0", x["grid"][mi][r["gaps"].index(0.0)], "at +3%", x["grid"][mi][r["gaps"].index(0.03)])
