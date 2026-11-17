"""Price outlook: the likely range of a share's price over the next 5, 10, 20 or 60 trading days.

For every horizon the outlook gives five points of the spread of outcomes (the 10th, 25th, 50th, 75th and 90th
percentile of the return). The width comes from the share's own volatility, scaled by how far outcomes really landed
in recent years (so about 8 in 10 land inside the 10-90% range). The middle is the usual move for that period plus
half of the share's tilt against the other shares from a gradient-boosting model that uses:
  * the chart: trend and momentum over 1-12 months, distance from the 50/200-day averages and the 52-week high,
    volatility, the biggest recent day, RSI, money flow, up-volume, the sector's 3-month move;
  * the market: the average share's 1- and 3-month move and volatility;
  * the company's figures as published at the time (earnings yield, book value to price, return on equity, profit
    growth), from the checked interim reports, dated by the day each report came out.
History: daily prices since 2010 (CSE + long history), one sample a week per tradeable share.

Tested walk-forward: each year from 2018 is predicted by models trained only on years before it (with a gap the
length of the horizon, so no training outcome overlaps the test year). The test reports whether the ranges held
(about 8 in 10 outcomes should land inside the 10-90% range), whether the middle estimate pointed the right way,
and how much better the ranges were than a plain "no view" range built from each share's own volatility.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ratings

ROOT = Path(__file__).parent
HZ = [5, 10, 20, 60]
QS = [0.1, 0.25, 0.5, 0.75, 0.9]
COST = 0.0224
STORE = ROOT / "data" / "model" / "outlook.json"
PARAMS = dict(max_depth=4, max_iter=250, learning_rate=0.05, min_samples_leaf=200, l2_regularization=1.0, max_bins=64, random_state=11)


def _bars() -> dict:
    import db
    import longterm
    meta, bars = longterm.load_meta(), {}
    for s in db.symbols_with_data(30):
        try:
            bars[s] = longterm.load(s, meta)[0]
        except Exception:
            pass
    return bars


def _asof(R: pd.DataFrame, col: str, rows, cols) -> pd.DataFrame:
    out = pd.DataFrame(np.nan, index=rows, columns=cols)
    for s, g in R.dropna(subset=[col]).groupby("s"):
        if s not in out.columns:
            continue
        g = g.drop_duplicates("up", keep="last").set_index("up")[col]
        i = g.index.searchsorted(rows, side="left") - 1
        j = np.clip(i, 0, None)
        age = (rows - g.index[j]).days
        out[s] = np.where((i >= 0) & (age <= ratings.REPORT_AGE), g.values[j], np.nan)
    return out


def dataset(sectors: dict, bars: dict | None = None, bad: dict | None = None):
    bars = bars if bars is not None else _bars()
    F, elig, c, info = ratings.measures(ratings.panel(bars), sectors)
    ret = c.pct_change(fill_method=None)
    ret = ret.where(ret.abs() < 0.6)
    F = dict(F)
    F["r5"] = c / c.shift(5) - 1
    F["r60"] = c / c.shift(60) - 1
    F["vol20"] = ret.rolling(20, min_periods=15).std()
    F["lo52"] = c / c.rolling(250, min_periods=200).min()
    F["tov"] = np.log1p(info["tov"])
    E = elig
    mkt = {"m_r20": F["r20"].where(E).mean(axis=1), "m_r60": F["r60"].where(E).mean(axis=1), "m_vol": F["vol20"].where(E).median(axis=1)}
    R = ratings.reports(set(c.columns), bad)
    rows_all = c.index
    ann, nav, pg = _asof(R, "ann", rows_all, c.columns), _asof(R, "nav", rows_all, c.columns), _asof(R, "pg", rows_all, c.columns)
    F["ey"] = (ann / c).clip(-1, 1)
    F["bp"] = (nav / c).where(nav > 0)
    F["roe"] = (ann / nav).where(nav > 0).clip(-1, 1.5)
    F["pg"] = pg.clip(-300, 300)
    fut = {h: np.log(c.shift(-h) / c) for h in HZ}
    return F, E, c, mkt, fut


def frame(F, E, mkt, fut, rows) -> pd.DataFrame:
    rows = pd.DatetimeIndex(rows)
    Er = E.loc[rows]
    cols = {k: v.loc[rows].where(Er).stack() for k, v in F.items()}
    X = pd.DataFrame(cols)
    X.index.names = ["d", "s"]
    d = X.index.get_level_values(0)
    for k, v in mkt.items():
        X[k] = v.reindex(d).values
    for h, f in fut.items():
        X[f"y{h}"] = f.loc[rows].stack().reindex(X.index).values
    X = X[X.index.get_level_values(0).isin(rows)]
    return X.dropna(subset=["r20", "vol60"])


FEATS = None


def _fit(X: pd.DataFrame, h: int):
    from sklearn.ensemble import HistGradientBoostingRegressor
    T = X.dropna(subset=[f"y{h}"])
    T = T[T[f"y{h}"].abs() < 1.5]
    # the middle estimate comes from the model; the width of the range from the share's own volatility, scaled by how
    # far outcomes actually landed from the model's middle in the last two training years (calibration).
    d = T.index.get_level_values(0)
    cut = d.max() - pd.Timedelta(days=730)
    A, C = T[d < cut], T[d >= cut]
    use = [f for f in FEATS if A[f].nunique(dropna=True) >= 20]       # a figure with no history yet is left out
    m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **PARAMS).fit(A[use], A[f"y{h}"])
    z = (C[f"y{h}"] - m.predict(C[use])) / (C["vol60"] * np.sqrt(h))
    zq = np.quantile(z.dropna(), QS)
    m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **PARAMS).fit(T[use], T[f"y{h}"])
    return use, m, zq, h, float(T[f"y{h}"].median())


SHRINK = 0.5        # tested: the model ranks shares a little better than chance but cannot call the market's direction,
                    # so the middle estimate is the usual move for the period plus half the share's tilt against the others


def _predict(fitted, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """The five points of the range, and the share's tilt against the other shares on the same day."""
    use, m, zq, h, drift = fitted
    raw = pd.Series(m.predict(X[use]), index=X.index)
    rel = (raw - raw.groupby(level=0).transform("mean")).values
    mid = drift + SHRINK * rel
    w = (X["vol60"].values * np.sqrt(h))[:, None]
    P = mid[:, None] + w * (zq - zq[2])[None, :]
    return np.sort(P, axis=1), rel


def _pinball(y, P):
    return float(np.mean([np.mean(np.maximum(q * (y - P[:, i]), (q - 1) * (y - P[:, i]))) for i, q in enumerate(QS)]))


def study(X: pd.DataFrame, start: int = 2018) -> dict:
    from scipy.stats import norm
    out = {}
    years = sorted(set(X.index.get_level_values(0).year))
    for h in HZ:
        res = []
        for yr in [y for y in years if y >= start]:
            d = X.index.get_level_values(0)
            gap = pd.Timestamp(f"{yr}-01-01") - pd.Timedelta(days=int(h * 1.6))
            tr, te = X[d < gap], X[(d.year == yr)].dropna(subset=[f"y{h}"])
            if len(te) < 200 or len(tr) < 5000:
                continue
            P, rel = _predict(_fit(tr, h), te)
            base = np.outer(te["vol60"].values * np.sqrt(h), norm.ppf(QS))   # "no view": the share's own volatility, no drift
            res.append(pd.DataFrame({"y": te[f"y{h}"].values, **{f"q{int(q*100)}": P[:, i] for i, q in enumerate(QS)},
                                     **{f"b{int(q*100)}": base[:, i] for i, q in enumerate(QS)}, "rel": rel, "year": yr, "d": te.index.get_level_values(0)}))
        if not res:
            continue
        T = pd.concat(res)
        y, P, B = T.y.values, T[[f"q{int(q*100)}" for q in QS]].values, T[[f"b{int(q*100)}" for q in QS]].values
        ic = T.groupby("d").apply(lambda g: g.rel.corr(g.y, method="spearman") if len(g) > 10 and g.rel.std() > 0 else np.nan).dropna()
        up = T.q50 > 0
        hi, lo = T[T.rel >= T.groupby("d").rel.transform(lambda v: v.quantile(0.8))], T[T.rel <= T.groupby("d").rel.transform(lambda v: v.quantile(0.2))]
        out[str(h)] = {
            "n": int(len(T)), "years": f"{int(T.year.min())}-{int(T.year.max())}",
            "in80": round(float(((y >= P[:, 0]) & (y <= P[:, 4])).mean()) * 100, 1),
            "in50": round(float(((y >= P[:, 1]) & (y <= P[:, 3])).mean()) * 100, 1),
            "base_in80": round(float(((y >= B[:, 0]) & (y <= B[:, 4])).mean()) * 100, 1),
            "skill": round((1 - _pinball(y, P) / _pinball(y, B)) * 100, 1),
            "dir": round(float(((y > 0) == up).mean()) * 100, 1),
            "dir_up": round(float((y[up.values] > 0).mean()) * 100, 1) if up.any() else None,
            "ic": round(float(ic.mean()), 3),
            "top20": round(float(np.expm1(hi.y).mean() - COST) * 100, 2), "bottom20": round(float(np.expm1(lo.y).mean() - COST) * 100, 2),
            "all": round(float(np.expm1(T.y).mean() - COST) * 100, 2),
        }
    return out


def build(sectors: dict, names: dict | None = None, bars: dict | None = None, bad: dict | None = None, retest: bool = False) -> dict:
    global FEATS
    F, E, c, mkt, fut = dataset(sectors, bars, bad)
    FEATS = list(F.keys()) + list(mkt.keys())
    rows = E.index[E.index >= ratings.FROM][::5]
    X = frame(F, E, mkt, fut, rows)
    prev = json.loads(STORE.read_text()) if STORE.exists() else {}
    stale = retest or not prev.get("test") or (dt.date.today() - dt.date.fromisoformat(prev.get("made", "2000-01-01"))).days >= 30
    test = study(X) if stale else prev["test"]
    last = E.index[-1]
    L = frame(F, E, mkt, {h: f * np.nan for h, f in fut.items()}, [last])
    shares = {}
    for h in HZ:
        P, rel = _predict(_fit(X, h), L)
        pct = pd.Series(rel).rank(pct=True).values
        for (d, s), p, r in zip(L.index, P, pct):
            shares.setdefault(s, {})[str(h)] = [round(float(np.expm1(v)) * 100, 2) for v in p] + [round(float(r) * 100)]
    out = {"made": dt.date.today().isoformat() if stale else prev.get("made"), "date": str(last.date()), "horizons": HZ,
           "quantiles": QS, "test": test, "cost": COST * 100, "shares": shares}
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({k: v for k, v in out.items() if k != "shares"}, indent=1))
    return out


if __name__ == "__main__":
    import sectors as S
    import time
    t = time.time()
    r = build(S.load(), retest=True)
    print(json.dumps(r["test"], indent=1)); print(len(r["shares"]), "shares;", round(time.time() - t), "s")
    print("JKH", r["shares"].get("JKH.N0000"))
