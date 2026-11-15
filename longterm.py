"""Long-term history: 10 years for each share, and 20 years of market patterns.

Inputs
  data/long/raw/<SYMBOL>.csv.gz   what Yahoo Finance returned (longhist.py); unofficial, so cleaned and checked here
  data/history/<SYMBOL>.csv       CSE's own daily prices (about one year); these always win where both exist
  data/index/aspi_annual.csv      official ASPI year-end closes
  data/index/ASPI.csv             official daily ASPI for the last year (CSE serves no more)

Cleaning of the older prices (every step is recorded as a check and shown on the site)
  1. rows without volume are dropped: Yahoo repeats the last price on non-trading days and since its feed stopped
  2. each share split Yahoo lists is tested against the prices around it and applied here if it was not
  3. an unexplained one-day jump beyond +60% / -37.5% is treated as a break (an unlisted split, most likely):
     only the history after the last break is kept
  4. the join with CSE's prices is tested: where both have the same days they must agree; otherwise the two ends
     must meet. A share whose histories do not meet keeps only its CSE year.

The market index
  CSE does not serve old ASPI values, so an index is built from the cleaned share prices, weighted by company size
  (today's shares in issue x price) and chain-linked day to day. It is then compared with the official ASPI:
  year by year against the published year-end closes, and day by day against the last year of official values.
  The comparison is published with it; the patterns are measured on this index.

    python longterm.py            # print a summary
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import db

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "long" / "raw"
META = ROOT / "data" / "long" / "yahoo_meta.json"
INDEX = ROOT / "data" / "index"
BREAK = math.log(1.6)          # a one-day move beyond this, with no split behind it, is a break in the data
NEAR = math.log(1.35)          # how closely a price jump must match a split ratio
LIMIT = 0.5                    # index only: a share's one-day move beyond this is left out of that day (a data fault, not a market move)
INDEX_FROM = "2006-01-01"      # before this too few of the large companies have older prices for the index to stand for the market
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _r(v, n=2):
    return None if v is None or v != v or v in (float("inf"), float("-inf")) else round(float(v), n)


def _d(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ cleaning and joining

def clean(sym: str, info: dict) -> tuple[pd.DataFrame | None, list[dict]]:
    """The older (Yahoo) prices for one share on today's share basis, and the checks made on the way."""
    checks: list[dict] = []
    note = lambda level, msg: checks.append({"level": level, "msg": msg})
    p = RAW / f"{sym}.csv.gz"
    if not p.exists():
        note("info", "No older price history is available for this share (CSE serves one year).")
        return None, checks
    d = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index().astype(float)
    n0 = len(d)
    d = d[(d["volume"].fillna(0) > 0) & (d["close"] > 0)]
    d = d[~d.index.duplicated(keep="last")]
    if len(d) < 30:
        note("warn", "Too little older history to use.")
        return None, checks
    note("ok", f"{n0 - len(d):,} filler rows without trades removed; {len(d):,} trading days kept ({_d(d.index[0])} to {_d(d.index[-1])}).")
    px = ["open", "high", "low", "close"]
    for date, num, den in info.get("splits") or []:
        if not num or not den:
            continue
        ratio = num / den
        if abs(math.log(ratio)) < math.log(1.15):
            continue                                  # scrip issues of a few per cent: too small to test, or to matter
        t = pd.Timestamp(date)
        before, after = d[d.index < t], d[d.index >= t]
        if before.empty or after.empty:
            continue
        seen = before["close"].iloc[-1] / after["close"].iloc[0]
        if abs(math.log(seen / ratio)) < NEAR:        # the jump is still in the prices: listed, but never applied
            d.loc[d.index < t, px] = d.loc[d.index < t, px] / ratio
            d.loc[d.index < t, "volume"] = d.loc[d.index < t, "volume"] * ratio
            note("ok", f"{num:g}-for-{den:g} share split of {date} applied to the earlier prices (the source lists it but had not adjusted for it).")
        elif abs(math.log(seen)) >= NEAR:
            note("warn", f"{num:g}-for-{den:g} split of {date}: the price moved {seen:.2f}x across it, which fits neither.")
    # single wild prints that are gone the next day (bad ticks): drop the row
    c = d["close"].to_numpy()
    bad = [i for i in range(1, len(c) - 1) if abs(math.log(c[i] / c[i - 1])) > BREAK and abs(math.log(c[i + 1] / c[i - 1])) < math.log(1.25)]
    if bad:
        d = d.drop(d.index[bad])
        note("ok", f"{len(bad)} one-day price spike{'s' if len(bad) > 1 else ''} that reversed the next day removed as bad prints.")
    # a price that is rebased in one step and stays there: a share split the source does not list (same test as for CSE's data)
    for t, f, seen in db.find_splits(d, 1.45, 1.5):
        d.loc[d.index < t, px] = d.loc[d.index < t, px] / f
        d.loc[d.index < t, "volume"] = d.loc[d.index < t, "volume"] * f
        note("warn", f"Price rebased {seen:.2f}x in one step on {_d(t)} and stayed there: treated as a share split of {f:g} the source does not list, "
                     f"earlier prices adjusted. Not confirmed by a second source.")
    # anything still jumping this much is unexplained: keep only what comes after it
    c = d["close"]
    lr = np.log(c / c.shift())
    gap = d.index.to_series().diff().dt.days
    breaks = lr.index[(lr.abs() > BREAK) & (gap <= 15) & (c.shift() >= 2)]
    if len(breaks):
        t = breaks[-1]
        note("warn", f"Unexplained one-day price change of {math.exp(lr[t]):.2f}x on {_d(t)}. History before that date is not used.")
        d = d[d.index >= t]
    if len(d) < 30:
        return None, checks
    d = d.assign(high=d[["high", "close"]].max(axis=1), low=d[["low", "close"]].min(axis=1))
    return d[px + ["volume"]], checks


def join(sym: str, old: pd.DataFrame | None, checks: list[dict]) -> tuple[pd.DataFrame, bool]:
    """CSE's own prices, with the older history in front of them when the two meet. Returns (bars, joined?)."""
    note = lambda level, msg: checks.append({"level": level, "msg": msg})
    cse = db.load_bars(sym)
    cse = cse[~cse.index.duplicated(keep="last")]
    if old is None or old.empty:
        return cse, False
    if cse.empty:
        return old, True
    first = cse.index[0]
    both = old.index.intersection(cse.index)
    if len(both) >= 3:
        r = old.loc[both, "close"] / cse.loc[both, "close"]
        m = float(r.median())
        tight = float((np.abs(np.log(r / m)) <= 0.03).mean())
        if tight < 0.8:
            note("fail", f"Older prices disagree with CSE's on the {len(both)} days both have (no steady relation). Older years are not used.")
            return cse, False
        if abs(m - 1) <= 0.02:
            note("ok", f"Older prices match CSE's own on the {len(both)} days both have.")
        else:
            old = old.assign(**{k: old[k] / m for k in ("open", "high", "low", "close")}, volume=old["volume"] * m)
            note("warn", f"Older prices were a steady {m:.3f}x CSE's on the {len(both)} days both have (a share issue the source missed); rescaled to match.")
    else:
        tail = old[old.index < first]
        if tail.empty:
            return cse, False
        days = (first - tail.index[-1]).days
        r = float(cse["close"].iloc[0] / tail["close"].iloc[-1])
        if days <= 200 and 1 / 1.6 <= r <= 1.6:
            note("info", f"Older history ends {_d(tail.index[-1])}; CSE's begins {_d(first)}. No shared days to compare; the price moved {r:.2f}x across the {days}-day gap.")
        else:
            note("fail", f"Older history ends {_d(tail.index[-1])} at {tail['close'].iloc[-1]:.2f} and CSE's begins {_d(first)} at {cse['close'].iloc[0]:.2f} "
                         f"({days} days apart): they do not meet, so the older years are not used.")
            return cse, False
    return pd.concat([old[old.index < first], cse]), True


def load(sym: str, meta: dict | None = None) -> tuple[pd.DataFrame, list[dict], bool]:
    meta = meta if meta is not None else load_meta()
    old, checks = clean(sym, meta.get(sym) or {})
    bars, joined = join(sym, old, checks)
    return bars, checks, joined


def load_meta() -> dict:
    return json.loads(META.read_text()) if META.exists() else {}


# ------------------------------------------------------------------ one share

def _drawdown(c: pd.Series) -> dict | None:
    if len(c) < 20:
        return None
    peak = c.cummax()
    dd = c / peak - 1
    t = dd.idxmin()
    top = c[:t].idxmax()
    back = c[t:][c[t:] >= c[top]]
    return {"pct": _r(dd.min() * 100, 1), "from": _d(top), "to": _d(t), "recovered": _d(back.index[0]) if len(back) else None}


def share_stats(bars: pd.DataFrame) -> dict:
    c = bars["close"].dropna()
    last, end = float(c.iloc[-1]), c.index[-1]
    out: dict = {"from": _d(c.index[0]), "to": _d(end), "years": _r((end - c.index[0]).days / 365.25, 1), "days": int(len(c))}
    periods = {}
    for label, yrs in (("1y", 1), ("3y", 3), ("5y", 5), ("10y", 10), ("15y", 15), ("20y", 20)):
        start = end - pd.DateOffset(years=yrs)
        if c.index[0] > start + pd.Timedelta(days=45):
            continue
        base = c[c.index <= start]
        base = float(base.iloc[-1]) if len(base) else float(c.iloc[0])
        tot = last / base - 1
        periods[label] = {"total": _r(tot * 100, 1), "per_year": _r(((1 + tot) ** (1 / yrs) - 1) * 100, 1) if yrs > 1 else None}
    out["periods"] = periods
    ye = c.groupby(c.index.year).last()
    years = []
    for y in ye.index:
        prev = ye.get(y - 1)
        if prev is None:
            continue
        years.append({"y": int(y), "ret": _r((ye[y] / prev - 1) * 100, 1), "part": bool(y == end.year and end.month < 12)})
    out["by_year"] = years[-21:]
    full = [x for x in years if not x["part"]]
    if full:
        out["best_year"], out["worst_year"] = max(full, key=lambda x: x["ret"]), min(full, key=lambda x: x["ret"])
        out["up_years"] = [sum(1 for x in full if x["ret"] > 0), len(full)]
    w = c[c.index >= end - pd.DateOffset(years=10)]
    out["window_from"] = _d(w.index[0])
    out["high"], out["low"] = {"v": _r(w.max()), "d": _d(w.idxmax())}, {"v": _r(w.min()), "d": _d(w.idxmin())}
    out["from_high"] = _r((last / w.max() - 1) * 100, 1)
    out["range_pos"] = _r((last - w.min()) / (w.max() - w.min()) * 100, 0) if w.max() > w.min() else None
    out["drawdown"] = _drawdown(w)
    wk = w.resample("W-FRI").last().dropna()
    if len(wk) > 30:
        out["volatility"] = _r(np.log(wk / wk.shift()).std() * math.sqrt(52) * 100, 0)
    return out


def weekly(bars: pd.DataFrame, years: int = 20) -> list[list]:
    c = bars["close"].dropna()
    c = c[c.index >= c.index[-1] - pd.DateOffset(years=years)]
    wk = c.resample("W-FRI").last().dropna()
    last_day = c.index[-1]
    out = [[_d(min(t, last_day)), _r(v, 4)] for t, v in wk.items()]
    return out


# ------------------------------------------------------------------ the market

def _shares() -> dict[str, float]:
    out = {}
    for p in (ROOT / "data" / "fundamentals").glob("*.json"):
        try:
            n = json.loads(p.read_text()).get("shares")
        except ValueError:
            n = None
        if n:
            out[p.stem] = float(n)
    return out


def market_index(series: dict[str, pd.Series]) -> tuple[pd.Series, pd.Series]:
    """Size-weighted, chain-linked index of the given close-price series. Returns (level starting at 100, number of shares in it)."""
    shares = _shares()
    P = pd.DataFrame({s: c for s, c in series.items() if s in shares})
    traded = P.notna()
    busy = traded.sum(axis=1)
    P = P[busy >= (0.15 * busy.rolling(250, min_periods=1).median()).clip(lower=10)]   # drop stray dates with almost no shares
    held = P.ffill(limit=60)                    # a share that did not trade today keeps its last price for up to 60 days
    cap = held * pd.Series(shares)
    w = cap.shift()
    ret = held / held.shift() - 1
    ok = w.notna() & ret.notna() & (ret.abs() <= LIMIT)
    day = (ret.where(ok) * w.where(ok)).sum(axis=1) / w.where(ok).sum(axis=1)
    n = ok.sum(axis=1)
    day = day[n >= 10].fillna(0.0)
    level = 100 * (1 + day).cumprod()
    return level, n.reindex(level.index)


def official() -> tuple[pd.Series, pd.Series]:
    """(official year-end closes by year, official daily closes for the last year)."""
    a = pd.read_csv(INDEX / "aspi_annual.csv").set_index("year")["close"] if (INDEX / "aspi_annual.csv").exists() else pd.Series(dtype=float)
    d = pd.Series(dtype=float)
    if (INDEX / "ASPI.csv").exists():
        d = pd.read_csv(INDEX / "ASPI.csv", parse_dates=["date"]).set_index("date")["close"].sort_index()
        ye = d.groupby(d.index.year).last()
        for y in ye.index:                       # add year-ends the official daily file covers (the last trading day of December)
            if y not in a.index and d[d.index.year == y].index[-1].month == 12 and (d.index[-1].year > y):
                a.loc[y] = float(ye[y])
    return a.sort_index(), d


def tie_to_official(level: pd.Series, a: pd.Series, d: pd.Series) -> pd.Series:
    """Bend the built index so that it passes exactly through every official year-end close, and use the official
    daily values where they exist (the last year).

    Between two official points the built index gives the shape; whatever it gained or lost against the official
    change over that stretch is spread evenly across the days in it. So year-end levels and yearly changes are the
    official ones, and only the path inside each year is an estimate.
    """
    pts = []
    if level.index[0].year - 1 in a.index:                       # the close of the year before the index starts
        pts.append((0, float(a[level.index[0].year - 1])))
    for y in sorted(set(level.index.year)):
        days = np.flatnonzero(level.index.year == y)
        if y in a.index and len(days) and level.index[days[-1]].month == 12 and (not len(d) or level.index[days[-1]] < d.index[0]):
            pts.append((int(days[-1]), float(a[y])))
    if len(d):
        i = np.flatnonzero(level.index >= d.index[0])
        if len(i):
            pts.append((int(i[0]), float(d.iloc[0])))
    if not pts:
        return level
    b = level.to_numpy(dtype=float)
    out = b * pts[0][1] / b[pts[0][0]]                           # before the first official point: just rescaled
    for (i0, v0), (i1, v1) in zip(pts, pts[1:]):
        if i1 <= i0:
            continue
        drift = (v1 / v0) / (b[i1] / b[i0])
        k = np.arange(1, i1 - i0 + 1)
        out[i0 + 1:i1 + 1] = v0 * (b[i0 + 1:i1 + 1] / b[i0]) * drift ** (k / (i1 - i0))
    last_i, last_v = pts[-1]
    out[last_i:] = b[last_i:] * last_v / b[last_i]
    out = pd.Series(out, index=level.index)
    if len(d):
        both = out.index.intersection(d.index)
        out[both] = d[both]
        extra = d[~d.index.isin(out.index) & (d.index > out.index[0])]
        if len(extra):
            out = pd.concat([out[out.index < d.index[0]], d]).sort_index()
            out = out[~out.index.duplicated(keep="last")]
    return out


def _phases(level: pd.Series, depth: float = 0.2) -> list[dict]:
    """Falls of `depth` or more from a peak: when they started, how deep, how long down, how long to get back."""
    out, peak_t, peak_v, low_t, low_v, down = [], level.index[0], level.iloc[0], None, None, False
    for t, v in level.items():
        if not down:
            if v >= peak_v:
                peak_t, peak_v = t, v
            elif v <= peak_v * (1 - depth):
                down, low_t, low_v = True, t, v
        else:
            if v < low_v:
                low_t, low_v = t, v
            if v >= peak_v:
                out.append({"peak": _d(peak_t), "low": _d(low_t), "fall": _r((low_v / peak_v - 1) * 100, 1),
                            "months_down": _r((low_t - peak_t).days / 30.44, 0), "back": _d(t), "months_back": _r((t - low_t).days / 30.44, 0)})
                down, peak_t, peak_v = False, t, v
    if down:
        out.append({"peak": _d(peak_t), "low": _d(low_t), "fall": _r((low_v / peak_v - 1) * 100, 1),
                    "months_down": _r((low_t - peak_t).days / 30.44, 0), "back": None, "months_back": None,
                    "now": _r((level.iloc[-1] / peak_v - 1) * 100, 1)})
    return out


def _after(m: pd.Series, cond: pd.Series, horizons=(1, 3, 6, 12)) -> dict:
    """Average result in the months after the months where `cond` is true (m = monthly closes)."""
    out = {"n": int(cond.sum())}
    for h in horizons:
        fwd = (m.shift(-h) / m - 1)[cond].dropna()
        base = (m.shift(-h) / m - 1).dropna()
        out[str(h)] = {"n": int(len(fwd)), "avg": _r(fwd.mean() * 100, 1), "up": int((fwd > 0).sum()), "all_avg": _r(base.mean() * 100, 1),
                       "all_up_pct": _r((base > 0).mean() * 100, 0)} if len(fwd) else None
    return out


def patterns(level: pd.Series) -> dict:
    """Twenty years (or as many as there are) of market patterns from a daily index level."""
    c = level.dropna()
    out: dict = {"from": _d(c.index[0]), "to": _d(c.index[-1]), "years": _r((c.index[-1] - c.index[0]).days / 365.25, 1)}
    m = c.resample("ME").last().dropna()
    mr = (m / m.shift() - 1).dropna()
    mr = mr[:-1] if c.index[-1].day < 25 and len(mr) and mr.index[-1].month == c.index[-1].month and mr.index[-1].year == c.index[-1].year else mr
    # 1. month of the year
    season = []
    for k in range(1, 13):
        x = mr[mr.index.month == k]
        if len(x) < 5:
            continue
        up = int((x > 0).sum())
        # chance of a split this lopsided if up and down months were equally likely (two-sided sign test)
        n = len(x)
        tail = sum(math.comb(n, j) for j in range(0, min(up, n - up) + 1)) / 2 ** n
        season.append({"m": MONTHS[k - 1], "n": n, "avg": _r(x.mean() * 100, 2), "median": _r(x.median() * 100, 2), "up": up,
                       "best": _r(x.max() * 100, 1), "worst": _r(x.min() * 100, 1), "p": _r(min(1.0, 2 * tail), 3)})
    out["season"] = season
    # 2. day of the week
    dr = (c / c.shift() - 1).dropna()
    out["weekday"] = [{"d": ["Mon", "Tue", "Wed", "Thu", "Fri"][k], "n": int((dr.index.dayofweek == k).sum()),
                       "avg": _r(dr[dr.index.dayofweek == k].mean() * 100, 3), "up_pct": _r((dr[dr.index.dayofweek == k] > 0).mean() * 100, 1)}
                      for k in range(5) if (dr.index.dayofweek == k).sum() > 50]
    # 3. big falls and recoveries
    out["falls"] = _phases(c, 0.2)
    peak = c.cummax()
    out["now"] = {"level": _r(c.iloc[-1]), "from_peak": _r((c.iloc[-1] / peak.iloc[-1] - 1) * 100, 1), "peak_date": _d(c.idxmax()),
                  "vs_200d": _r((c.iloc[-1] / c.tail(200).mean() - 1) * 100, 1) if len(c) >= 200 else None,
                  "ret_12m": _r((c.iloc[-1] / c[c.index <= c.index[-1] - pd.DateOffset(years=1)].iloc[-1] - 1) * 100, 1)
                  if c.index[0] <= c.index[-1] - pd.DateOffset(years=1) else None}
    # 4. holding periods: if you had bought at any month-end and held N years
    hold = []
    for yrs in (1, 3, 5, 10):
        r = (m.shift(-12 * yrs) / m - 1).dropna()
        if len(r) < 12:
            continue
        per_year = (1 + r) ** (1 / yrs) - 1
        hold.append({"years": yrs, "n": int(len(r)), "up_pct": _r((r > 0).mean() * 100, 0), "median": _r(per_year.median() * 100, 1),
                     "worst": _r(per_year.min() * 100, 1), "best": _r(per_year.max() * 100, 1)})
    out["holding"] = hold
    if out["now"]["ret_12m"] is not None:
        r12 = (m / m.shift(12) - 1).dropna() * 100
        out["now"]["ret_12m_rank"] = _r((r12 < out["now"]["ret_12m"]).mean() * 100, 0)
    # 5. what followed strong and weak months, and falls from the peak
    out["after"] = {"down8": _after(m, (m / m.shift() - 1) <= -0.08), "up8": _after(m, (m / m.shift() - 1) >= 0.08),
                    "dd20": _after(m, (m / m.cummax() - 1) <= -0.20), "high": _after(m, m >= m.cummax())}
    # 6. year by year: return, largest fall within the year, how rough it was
    yr = []
    for y, x in c.groupby(c.index.year):
        prev = c[c.index.year < y]
        if prev.empty or len(x) < 30:
            continue
        seg = pd.concat([prev.tail(1), x])
        d_ = np.log(seg / seg.shift()).dropna()
        yr.append({"y": int(y), "ret": _r((x.iloc[-1] / prev.iloc[-1] - 1) * 100, 1), "worst_fall": _r((seg / seg.cummax() - 1).min() * 100, 1),
                   "vol": _r(d_.std() * math.sqrt(len(d_)) * 100, 0), "part": bool(y == c.index[-1].year and c.index[-1].month < 12)})
    out["by_year"] = yr
    this = c.index[-1].month
    cur = next((s for s in season if s["m"] == MONTHS[this - 1]), None)
    nxt = next((s for s in season if s["m"] == MONTHS[this % 12]), None)
    out["now"]["month"], out["now"]["next_month"] = cur, nxt
    return out


def market(bars: dict[str, pd.DataFrame], joined: set[str]) -> dict:
    """The market index, how well it tracks the official ASPI, and the patterns measured on it."""
    level, n = market_index({s: b["close"] for s, b in bars.items()})
    a, d = official()
    out: dict = {"built_from": {"first": _d(level.index[0]), "last": _d(level.index[-1]), "shares_now": int(n.iloc[-1]),
                                "shares_start": int(n.iloc[min(5, len(n) - 1)]), "long_histories": len(joined)}}
    # start where enough of the large companies are in it to stand for the market
    enough = n[(n >= 40) & (n.index >= INDEX_FROM)]
    start = enough.index[0] if len(enough) else level.index[0]
    level, n = level[level.index >= start], n[n.index >= start]
    out["built_from"].update(start=_d(start), shares_at_start=int(n.iloc[0]))
    # how close is it to the official ASPI?
    ye = level.groupby(level.index.year).last()
    cmp_ = []
    for y in ye.index:
        if y - 1 in ye.index and y in a.index and y - 1 in a.index and level[level.index.year == y].index[-1].month == 12 \
                and level[level.index.year == y - 1].index[-1].month == 12:
            cmp_.append({"y": int(y), "official": _r((a[y] / a[y - 1] - 1) * 100, 1), "built": _r((ye[y] / ye[y - 1] - 1) * 100, 1)})
    fit: dict = {"years": cmp_}
    if len(cmp_) >= 5:
        o, b = np.array([x["official"] for x in cmp_]), np.array([x["built"] for x in cmp_])
        fit.update(n=len(cmp_), corr=_r(np.corrcoef(o, b)[0, 1], 3), mean_gap=_r(np.abs(o - b).mean(), 1), worst_gap=_r(np.abs(o - b).max(), 1),
                   same_direction=int((np.sign(o) == np.sign(b)).sum()))
    if len(d) > 60:
        both = level.index.intersection(d.index)
        if len(both) > 60:
            x, y_ = (level[both] / level[both].shift() - 1).dropna(), (d[both] / d[both].shift() - 1).dropna()
            fit["daily"] = {"n": int(len(x)), "corr": _r(np.corrcoef(x, y_)[0, 1], 3), "from": _d(both[0]), "to": _d(both[-1]),
                            "official": _r((d[both].iloc[-1] / d[both].iloc[0] - 1) * 100, 1), "built": _r((level[both].iloc[-1] / level[both].iloc[0] - 1) * 100, 1)}
    out["fit"] = fit
    # put it on the ASPI's own scale: through every official year-end close, official daily values for the last year
    level = tie_to_official(level, a, d)
    out["official_daily_from"] = _d(d.index[0]) if len(d) else None
    wk = level.resample("W-FRI").last().dropna()
    out["weekly"] = [[_d(min(t, level.index[-1])), _r(v, 1)] for t, v in wk.items()]
    out["official_daily"] = [[_d(t), _r(v, 2)] for t, v in d.items()]
    out["official_years"] = [{"y": int(y), "close": _r(a[y]), "ret": _r((a[y] / a[y - 1] - 1) * 100, 1) if y - 1 in a.index else None} for y in a.index]
    if len(d):
        y0 = d.index[-1].year
        if y0 - 1 in a.index:
            out["official_ytd"] = {"y": int(y0), "close": _r(d.iloc[-1]), "ret": _r((d.iloc[-1] / a[y0 - 1] - 1) * 100, 1), "to": _d(d.index[-1])}
    off = a[a.index >= a.index.max() - 20] if len(a) else a
    if len(off) >= 11:
        last_y = int(off.index.max())
        g = lambda yrs: _r(((a[last_y] / a[last_y - yrs]) ** (1 / yrs) - 1) * 100, 1) if last_y - yrs in a.index else None
        rets = (a / a.shift() - 1).dropna()
        rets = rets[rets.index > last_y - 20]
        out["official_summary"] = {"to": last_y, "growth_10y": g(10), "growth_20y": g(20), "up_years": int((rets > 0).sum()), "n_years": int(len(rets)),
                                   "best": {"y": int(rets.idxmax()), "ret": _r(rets.max() * 100, 1)}, "worst": {"y": int(rets.idxmin()), "ret": _r(rets.min() * 100, 1)}}
    out["patterns"] = patterns(level)
    return out


# ------------------------------------------------------------------ build everything for the site

def build(out_dir: Path | None = None, verbose: bool = False) -> dict:
    meta = load_meta()
    names = db.security_names()
    bars, joined, summary = {}, set(), []
    counts = {"shares": 0, "with_older": 0, "joined": 0, "not_joined": 0, "no_older": 0, "splits_applied": 0, "breaks": 0, "rescaled": 0}
    for s in db.symbols_with_data(30):
        b, checks, ok = load(s, meta)
        if b.empty:
            continue
        bars[s] = b
        counts["shares"] += 1
        has_old = (RAW / f"{s}.csv.gz").exists()
        counts["with_older"] += has_old
        counts["no_older"] += not has_old
        counts["joined"] += ok
        counts["not_joined"] += has_old and not ok
        counts["splits_applied"] += sum("share split of" in c["msg"] and c["level"] == "ok" for c in checks)
        counts["breaks"] += any("Unexplained" in c["msg"] for c in checks)
        counts["assumed_splits"] = counts.get("assumed_splits", 0) + sum("treated as a share split" in c["msg"] for c in checks)
        counts["rescaled"] += any("rescaled" in c["msg"] for c in checks)
        if ok:
            joined.add(s)
        st = share_stats(b)
        summary.append({"s": s, "name": names.get(s, ""), "from": st["from"], "years": st["years"], "joined": ok,
                        "warn": sum(c["level"] == "warn" for c in checks), "fail": sum(c["level"] == "fail" for c in checks),
                        "p10": (st["periods"].get("10y") or {}).get("per_year"), "p5": (st["periods"].get("5y") or {}).get("per_year")})
        if out_dir is not None:
            p = out_dir / "long" / f"{s}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"symbol": s, "joined": ok, "checks": checks, "stats": st, "weekly": weekly(b)}, separators=(",", ":")))
    mk = market(bars, joined)
    mk["checks"] = counts
    mk["shares"] = sorted(summary, key=lambda x: x["s"])
    mk["made"] = pd.Timestamp.now(tz="Asia/Colombo").strftime("%Y-%m-%d %H:%M")
    if out_dir is not None:
        (out_dir / "market.json").write_text(json.dumps(mk, separators=(",", ":")))
    if verbose:
        print(json.dumps(counts))
    return mk


if __name__ == "__main__":
    mk = build(verbose=True)
    f = mk["fit"]
    print("index from", mk["built_from"], "| official daily from", mk["official_daily_from"])
    print("fit vs official: years", f.get("n"), "corr", f.get("corr"), "mean gap", f.get("mean_gap"), "worst", f.get("worst_gap"), "| daily", f.get("daily"))
    for x in f["years"]:
        print("  ", x)
    p = mk["patterns"]
    print("patterns", p["from"], p["to"])
    for s in p["season"]:
        print("  ", s)
    print("falls:", *p["falls"], sep="\n   ")
    print("holding:", p["holding"])
    print("after:", json.dumps(p["after"]))
    print("now:", p["now"])
