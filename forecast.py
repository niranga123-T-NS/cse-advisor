"""Pattern forecast: how likely is a stock to make a gain, after costs, over the next few trading days?

What it does
  * turns each stock's daily bars into ~60 pattern features (momentum, trend, volatility, volume, range position,
    strength versus the market and versus its sector peers),
  * trains a gradient-boosted model on what followed similar patterns in the past,
  * and - most importantly - measures itself honestly with a WALK-FORWARD test: the model is only ever scored on
    days that come after everything it was trained on, with trading costs taken off.

The trade being forecast (what "gain" means)
  signal after the close on day t  ->  buy at the close of day t+1  ->  sell at the close H days later,
  minus RULES['cost_per_side'] on each side.  y = 1 if that net return is above zero.

Fundamentals are deliberately NOT model inputs: today's report figures were not known on past dates, so using
them in the history would leak the future into the test.

    python forecast.py validate     # walk-forward test -> data/model/validation.json
    python forecast.py today        # train on everything, forecast the latest day -> data/forecasts/<date>.json
    python forecast.py score        # score past forecasts against what happened -> data/model/track.json
    python forecast.py all          # the three above (what the daily job runs)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import db
import sectors
from analysis import RULES

H = 5                                  # holding period in trading days
COST = 2 * RULES["cost_per_side"]      # round trip
MIN_TURNOVER = 2_000_000               # LKR/day (20-day average): must be tradeable
MIN_PRICE = 5.0                        # below this one 10-cent tick is >2% - pure noise
MIN_TRADED = 0.9                       # traded on at least 90% of the last 20 market days
ROOT = Path(__file__).parent
MODEL_DIR = ROOT / "data" / "model"
FC_DIR = ROOT / "data" / "forecasts"
PARAMS = dict(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=80, l2_regularization=1.0,
              max_bins=64, random_state=7)


# ------------------------------------------------------------------ data panel

def load_panel() -> dict[str, pd.DataFrame]:
    """All stocks on one shared market calendar. Days a stock did not trade: last close carried, volume 0."""
    raw = {s: db.load_bars(s) for s in db.symbols_with_data(60)}
    counts = pd.Series(np.concatenate([d.index.values for d in raw.values()])).value_counts()
    cal = pd.DatetimeIndex(sorted(counts[counts >= 0.3 * counts.max()].index))
    out = {}
    for s, d in raw.items():
        d = d[~d.index.duplicated(keep="last")].reindex(cal)
        traded = d["close"].notna()
        d["close"] = d["close"].ffill()
        for col in ("open", "high", "low"):
            d[col] = d[col].where(traded, d["close"])
        d["volume"] = d["volume"].fillna(0.0)
        d["traded"] = traded.astype(float)
        out[s] = d[d["close"].notna()]
    return out


def _rsi(c: pd.Series, n: int) -> pd.Series:
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def stock_features(d: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
    f = pd.DataFrame(index=d.index)
    r1 = c.pct_change()
    for n in (1, 2, 3, 5, 10, 20, 60):
        f[f"ret{n}"] = c.pct_change(n)
    f["vol20"] = r1.rolling(20).std()
    f["vol5_20"] = r1.rolling(5).std() / f["vol20"].replace(0, np.nan)
    prev = c.shift()
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    f["atr_pct"] = atr / c
    rng = (h - l)
    f["range_rel"] = rng / rng.rolling(20).mean().replace(0, np.nan)
    f["clv"] = (((c - l) - (h - c)) / rng.replace(0, np.nan)).fillna(0.0)       # where in the day's range it closed
    f["clv5"] = f["clv"].rolling(5).mean()
    sma = {n: c.rolling(n).mean() for n in (10, 20, 50)}
    for n in (10, 20, 50):
        f[f"sma{n}"] = c / sma[n] - 1
    f["sma20_slope"] = sma[20].pct_change(5)
    f["sma_stack"] = (sma[10] > sma[20]).astype(float) + (sma[20] > sma[50]).astype(float)
    f["bb"] = (c - sma[20]) / (2 * c.rolling(20).std()).replace(0, np.nan)
    for n in (20, 60, 120):
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        f[f"pos{n}"] = (c - lo) / (hi - lo).replace(0, np.nan)                 # 0 = at the low, 1 = at the high
    f["dd60"] = c / h.rolling(60).max() - 1
    f["up60"] = c / l.rolling(60).min() - 1
    f["new_hi20"] = (c >= c.rolling(20).max()).astype(float)
    f["new_lo20"] = (c <= c.rolling(20).min()).astype(float)
    f["rsi14"], f["rsi5"] = _rsi(c, 14), _rsi(c, 5)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    f["macd_hist"] = hist / c
    f["macd_chg3"] = (hist - hist.shift(3)) / c
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    f["stoch"] = (c - lo14) / (hi14 - lo14).replace(0, np.nan)
    va = v.rolling(20).mean().replace(0, np.nan)
    f["v_rel"] = v / va
    f["v5_20"] = v.rolling(5).mean() / va
    f["v_trend"] = v.rolling(20).mean() / v.rolling(60).mean().replace(0, np.nan)
    upv = v.where(r1 > 0, 0.0).rolling(10).sum()
    f["upvol10"] = upv / v.rolling(10).sum().replace(0, np.nan)
    turnover = (c * v).rolling(20).mean()
    f["turnover"] = np.log10(turnover + 1)
    f["traded20"] = d["traded"].rolling(20).mean()
    sign = np.sign(r1.fillna(0))
    grp = (sign != sign.shift()).cumsum()
    f["streak"] = sign.groupby(grp).cumsum()                                   # consecutive up (+) / down (-) days
    f["tick_pct"] = 0.1 / c
    f["gap_atr"] = (c - prev) / atr.replace(0, np.nan)                         # today's move in ATR units
    # label: buy at tomorrow's close, sell H days later, net of costs
    entry, exit_ = c.shift(-1), c.shift(-(1 + H))
    f["net"] = exit_ / entry - 1 - COST
    f["can_enter"] = d["traded"].shift(-1)
    f["close"], f["tov"] = c, turnover
    return f


FEATURES: list[str] = []


def build_dataset() -> pd.DataFrame:
    panel = load_panel()
    sec = sectors.load()
    parts = []
    for s, d in panel.items():
        f = stock_features(d)
        f["symbol"], f["sector"] = s, sec.get(s, "")
        parts.append(f)
    X = pd.concat(parts).rename_axis("date").reset_index()
    X["universe"] = (X["tov"] >= MIN_TURNOVER) & (X["close"] >= MIN_PRICE) & (X["traded20"] >= MIN_TRADED)
    U = X[X["universe"]]
    # market context (median of the tradeable universe each day)
    g = U.groupby("date")
    mkt = pd.DataFrame({"mkt1": g["ret1"].median(), "mkt5": g["ret5"].median(), "mkt20": g["ret20"].median(),
                        "breadth1": g["ret1"].apply(lambda r: (r > 0).mean()),
                        "breadth20": g["sma20"].apply(lambda r: (r > 0).mean()),
                        "mkt_vol": g["vol20"].median(), "disp5": g["ret5"].std()})
    X = X.merge(mkt, left_on="date", right_index=True, how="left")
    X["rel5"], X["rel20"] = X["ret5"] - X["mkt5"], X["ret20"] - X["mkt20"]
    # sector peers
    sg = U[U["sector"] != ""].groupby(["date", "sector"])
    secf = pd.DataFrame({"sec5": sg["ret5"].median(), "sec20": sg["ret20"].median(),
                         "sec_breadth": sg["sma20"].apply(lambda r: (r > 0).mean()), "sec_n": sg["ret5"].size()})
    X = X.merge(secf, left_on=["date", "sector"], right_index=True, how="left")
    few = X["sec_n"].fillna(0) < 3
    X.loc[few, ["sec5", "sec20", "sec_breadth"]] = np.nan
    X["rel_sec5"], X["rel_sec20"] = X["ret5"] - X["sec5"], X["ret20"] - X["sec20"]
    X["sec_vs_mkt20"] = X["sec20"] - X["mkt20"]
    # ranks within the day (0..1) among tradeable stocks
    for col in ("ret5", "ret20", "v_rel", "atr_pct", "pos60", "rsi14"):
        X[f"rk_{col}"] = X[col].where(X["universe"]).groupby(X["date"]).rank(pct=True)
    global FEATURES
    skip = {"date", "symbol", "sector", "net", "can_enter", "close", "tov", "universe", "sec_n"}
    FEATURES = [c for c in X.columns if c not in skip]
    X["y"] = (X["net"] > 0).astype(float).where(X["net"].notna())
    return X


# ------------------------------------------------------------------ model

class Model:
    """Classifier (chance of a net gain) + regressor (expected net return) on the usable feature columns."""

    def __init__(self, train: pd.DataFrame):
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
        self.cols = [c for c in FEATURES if train[c].nunique(dropna=True) >= 2]   # skip columns with no information yet
        self.clf = HistGradientBoostingClassifier(**PARAMS).fit(train[self.cols], train["y"])
        self.reg = HistGradientBoostingRegressor(**PARAMS).fit(train[self.cols], train["net"].clip(-0.25, 0.25))

    def predict(self, rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        return self.clf.predict_proba(rows[self.cols])[:, 1], self.reg.predict(rows[self.cols])


def walk_forward(X: pd.DataFrame, min_train: int = 100, step: int = 10) -> pd.DataFrame:
    """Score every test day with a model trained only on days whose outcomes were already known."""
    days = sorted(X["date"].unique())
    ok = X[X["universe"] & X["y"].notna() & (X["can_enter"] == 1)]
    out = []
    for start in range(min_train, len(days), step):
        train_days = set(days[:max(0, start - (H + 2))])       # purge: labels must be complete before the test starts
        test_days = set(days[start:start + step])
        tr, te = ok[ok["date"].isin(train_days)], ok[ok["date"].isin(test_days)]
        if len(tr) < 3000 or te.empty:
            continue
        p, e = Model(tr).predict(te)
        te = te[["date", "symbol", "sector", "net", "y", "close"]].copy()
        te["p"], te["exp"] = p, e
        out.append(te)
    return pd.concat(out) if out else pd.DataFrame()


def _ci(vals_by_day: list[np.ndarray], stat, n=2000, seed=3) -> tuple[float, float]:
    """95% interval by resampling whole DAYS (picks on the same day rise and fall together)."""
    rng = np.random.default_rng(seed)
    k = len(vals_by_day)
    if k < 5:
        return (float("nan"), float("nan"))
    res = []
    for _ in range(n):
        pick = rng.integers(0, k, k)
        res.append(stat(np.concatenate([vals_by_day[i] for i in pick])))
    return float(np.percentile(res, 2.5)), float(np.percentile(res, 97.5))


def _summ(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    by_day = [g["net"].to_numpy() for _, g in df.groupby("date")]
    hit_lo, hit_hi = _ci(by_day, lambda a: (a > 0).mean())
    ret_lo, ret_hi = _ci(by_day, np.mean)
    return {"n": int(len(df)), "days": int(df["date"].nunique()),
            "hit": round(float((df["net"] > 0).mean()) * 100, 1), "hit_ci": [round(hit_lo * 100, 1), round(hit_hi * 100, 1)],
            "avg_net": round(float(df["net"].mean()) * 100, 2), "avg_ci": [round(ret_lo * 100, 2), round(ret_hi * 100, 2)],
            "median_net": round(float(df["net"].median()) * 100, 2),
            "gross_up": round(float((df["net"] > -COST).mean()) * 100, 1),
            "worst": round(float(df["net"].min()) * 100, 1), "best": round(float(df["net"].max()) * 100, 1)}


def evaluate(oos: pd.DataFrame) -> dict:
    from sklearn.metrics import roc_auc_score
    res = {"holding_days": H, "cost_pct": round(COST * 100, 2), "period": [str(oos["date"].min())[:10], str(oos["date"].max())[:10]],
           "universe": f"avg turnover ≥ LKR {MIN_TURNOVER/1e6:.0f}M/day, price ≥ Rs {MIN_PRICE:.0f}",
           "all": _summ(oos), "auc": round(float(roc_auc_score(oos["y"], oos["p"])), 3)}
    ranked = oos.sort_values(["date", "p"], ascending=[True, False])
    res["top"] = {str(n): _summ(ranked.groupby("date").head(n)) for n in (1, 3, 5, 10)}
    res["bottom5"] = _summ(ranked.groupby("date").tail(5))
    # paired lift: on the same days, how much better did the top 5 do than the average tradeable stock?
    top5 = ranked.groupby("date").head(5)
    day_all = oos.groupby("date")["net"].agg(hit=lambda a: (a > 0).mean(), avg="mean")
    day_top = top5.groupby("date")["net"].agg(hit=lambda a: (a > 0).mean(), avg="mean")
    diff = (day_top - day_all).dropna()
    rng = np.random.default_rng(5)
    boot = np.array([diff.iloc[rng.integers(0, len(diff), len(diff))].mean().to_numpy() for _ in range(2000)])
    res["lift_top5"] = {"hit_pts": round(float(diff["hit"].mean()) * 100, 1),
                        "hit_ci": [round(float(np.percentile(boot[:, 0], 2.5)) * 100, 1), round(float(np.percentile(boot[:, 0], 97.5)) * 100, 1)],
                        "net_pts": round(float(diff["avg"].mean()) * 100, 2),
                        "net_ci": [round(float(np.percentile(boot[:, 1], 2.5)) * 100, 2), round(float(np.percentile(boot[:, 1], 97.5)) * 100, 2)]}
    t5 = res["top"]["5"]
    res["verdict"] = ("edge" if t5["avg_ci"][0] > 0 else "possible" if t5["avg_net"] > 0 else "none")
    res["by_prob"] = []
    edges = [0, .2, .3, .4, .5, .6, .7, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        b = oos[(oos["p"] >= lo) & (oos["p"] < hi)]
        res["by_prob"].append({"from": lo, "to": min(hi, 1.0), **_summ(b)})
    # month by month for the daily top 5 (is it stable, or one lucky month?)
    res["monthly_top5"] = [{"month": str(m), **_summ(g)} for m, g in top5.groupby(top5["date"].dt.to_period("M"))]
    res["monthly_all"] = [{"month": str(m), "hit": round(float((g["net"] > 0).mean()) * 100, 1),
                           "avg_net": round(float(g["net"].mean()) * 100, 2)} for m, g in oos.groupby(oos["date"].dt.to_period("M"))]
    return res


def calibration(oos: pd.DataFrame) -> list[dict]:
    """Model score -> how often such forecasts actually made a net gain out of sample (monotone)."""
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(oos["p"], oos["y"])
    grid = np.linspace(0.02, 0.98, 49)
    return [{"p": round(float(g), 2), "hit": round(float(v), 3)} for g, v in zip(grid, iso.predict(grid))]


def _calibrated(p: float, table: list[dict]) -> float:
    xs, ys = [t["p"] for t in table], [t["hit"] for t in table]
    return float(np.interp(p, xs, ys))


# ------------------------------------------------------------------ jobs

def validate(X: pd.DataFrame | None = None) -> dict:
    X = build_dataset() if X is None else X
    oos = walk_forward(X)
    res = evaluate(oos)
    res["calibration"] = calibration(oos)
    res["features"] = len(FEATURES)
    res["made"] = pd.Timestamp.now(tz="Asia/Colombo").strftime("%Y-%m-%d %H:%M")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "validation.json").write_text(json.dumps(res, indent=1))
    oos[["date", "symbol", "p", "exp", "net"]].assign(date=oos["date"].dt.strftime("%Y-%m-%d")).round(4) \
        .to_csv(MODEL_DIR / "oos.csv", index=False)
    return res


def today(X: pd.DataFrame | None = None) -> dict:
    X = build_dataset() if X is None else X
    last = X["date"].max()
    train = X[X["universe"] & X["y"].notna() & (X["can_enter"] == 1)]
    cur = X[(X["date"] == last) & X["universe"]].copy()
    cur["p"], cur["exp"] = Model(train).predict(cur)
    vpath = MODEL_DIR / "validation.json"
    table = json.loads(vpath.read_text())["calibration"] if vpath.exists() else None
    cur["rank"] = cur["p"].rank(ascending=False, method="first").astype(int)
    rows = {}
    for r in cur.itertuples():
        rows[r.symbol] = {"p": round(float(r.p), 3), "exp": round(float(r.exp) * 100, 2), "rank": int(r.rank),
                          "hit": round(_calibrated(r.p, table) * 100, 0) if table else None}
    out = {"date": last.strftime("%Y-%m-%d"), "holding_days": H, "n": len(rows), "forecasts": rows}
    FC_DIR.mkdir(parents=True, exist_ok=True)
    (FC_DIR / f"{out['date']}.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


def score() -> dict:
    """Live track record: every forecast that was published, against what the price then did."""
    panel = load_panel()
    rows = []
    for f in sorted(FC_DIR.glob("*.json")):
        fc = json.loads(f.read_text())
        d0 = pd.Timestamp(fc["date"])
        for s, v in fc["forecasts"].items():
            d = panel.get(s)
            if d is None or d0 not in d.index:
                continue
            i = d.index.get_loc(d0)
            if i + 1 + H >= len(d) or d["traded"].iloc[i + 1] == 0:
                continue
            net = d["close"].iloc[i + 1 + H] / d["close"].iloc[i + 1] - 1 - COST
            rows.append({"date": d0, "symbol": s, "p": v["p"], "rank": v["rank"], "net": net})
    df = pd.DataFrame(rows)
    res = {"forecast_days": 0, "note": "Scored once each forecast's holding period has finished."}
    if not df.empty:
        res.update({"forecast_days": int(df["date"].nunique()), "all": _summ(df),
                    "top": {str(n): _summ(df[df["rank"] <= n]) for n in (1, 3, 5, 10)},
                    "recent": [{"date": r.date.strftime("%Y-%m-%d"), "symbol": r.symbol, "p": r.p, "rank": int(r.rank),
                                "net": round(r.net * 100, 2)} for r in df[df["rank"] <= 5].sort_values(["date", "rank"]).tail(40).itertuples()]})
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "track.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    X = build_dataset() if cmd in ("validate", "today", "all") else None
    if cmd in ("validate", "all"):
        v = validate(X)
        print("Walk-forward:", v["period"], "base hit", v["all"]["hit"], "| daily top 5:", v["top"]["5"])
    if cmd in ("today", "all"):
        t = today(X)
        best = sorted(t["forecasts"].items(), key=lambda kv: kv[1]["rank"])[:5]
        print("Forecast", t["date"], [(s, v["p"]) for s, v in best])
    if cmd in ("score", "all"):
        print("Track record:", score().get("top", {}).get("5"))
