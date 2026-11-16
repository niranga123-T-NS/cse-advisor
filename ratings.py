"""Buy / Sell / Hold ratings built on what has actually been followed by better and worse results on the CSE.

How it works, in short:
  1. Every share that trades enough to be rated is measured on five things, all from its own prices and volumes
     (GROUPS below): its trend over the past year, its strength over the past month, how steady it is,
     whether volume comes in on rising days, and how its industry group is doing.
  2. On each measure a share is ranked against the other rated shares that day. The five ranks are simply added:
     no weights are fitted, so there is nothing to over-tune.
  2b. Since interim reports are on record (2024), the share's place on its latest published report (earnings and net
     assets against the price, return on equity, profit growth) is added with the same weight as the five together.
  3. The top 10% of shares are STRONG BUY, the next 20% BUY, the middle 40% HOLD, the next 20% SELL and the
     bottom 10% STRONG SELL.
  4. study() replays this over every week since 2010 and records what each grade went on to do over 1, 3, 6 and
     12 months after trading costs. Those results are shown beside every rating, so a grade always comes with
     its measured odds.

What the test showed (see data/model/ratings.json for the figures):
  - the spread between the best and worst groups is real but modest, and the year matters more than the grade;
  - a tree model and fitted weights did no better than the plain sum of ranks;
  - the market's own state (above or below its 200-day average, breadth) did not help;
  - for thinly traded shares the measures told far less (the chance of a gain was the same for every grade), so those
    shares are not rated.

Limits that cannot be removed: older prices come from an unofficial source; only companies still listed today
are in the history, which flatters past returns; dividends are not counted; and the measures were chosen after
looking at this same history, so the future edge is probably smaller than the past one.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
MODEL = ROOT / "data" / "model" / "ratings.json"
COST = 0.0224                       # buying and selling together
FROM = "2010-01-01"                 # test period starts here (earlier years have too few rated shares)
MIN_DAYS, MIN_TOV, MIN_PRICE, MIN_HISTORY = 48, 5e5, 5.0, 250      # traded on 48 of the last 60 days, LKR 0.5M a day, LKR 5, a year of prices
HORIZONS = [(20, "1 month"), (60, "3 months"), (120, "6 months"), (250, "12 months")]
GRADES = [("STRONG SELL", 0.10), ("SELL", 0.30), ("HOLD", 0.70), ("BUY", 0.90), ("STRONG BUY", 1.01)]      # upper edge of each grade, as a share of rated shares
ORDER = [g for g, _ in GRADES]
GROUPS = {          # a leading "-" means lower is better
    "trend": ["mom12_1", "mom6_1", "slope200", "hi52", "d200"],
    "strength": ["rsi", "r20", "d50", "x20_50"],
    "calm": ["-vol60", "-maxday20"],
    "volume": ["upvol20", "cmf20"],
    "sector": ["sec60"],
}
WORDS = {
    "trend": ("Trend over the past year", "12-month and 6-month price change, the slope of its 200-day average, and how close it is to its 52-week high"),
    "strength": ("Strength over the past month", "RSI, one-month change, and price against its 20- and 50-day averages. On this market strong shares have tended to stay strong; a high RSI has not been a reason to sell"),
    "calm": ("Steadiness", "smaller daily swings and no one-day spikes. Jumpy shares have done worse on average"),
    "volume": ("Buying on volume", "more of the volume on rising days, and closes near the top of the day's range"),
    "sector": ("Its industry group", "how the rest of its industry group has done over three months"),
    "reports": ("Company figures", "from its latest interim report as published: earnings and net assets against the price, return on equity, and profit growth on a year earlier"),
}
REPORT_FROM = "2024-01-01"          # interim reports are on record from about here; the combined test starts then
REPORT_AGE = 200                    # a report older than this many days no longer counts
REPORT_WEIGHT = 1.0                 # company figures count as much as the five price measures together (tested, see study())


# ---------------------------------------------------------------- the measures

def panel(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Every share's daily bars side by side: one table per field, a row per market day, a column per share."""
    P = {k: pd.DataFrame({s: b[k].astype(float) for s, b in bars.items() if len(b)}).sort_index() for k in ("high", "low", "close", "volume")}
    return P


def measures(P: dict, sectors: dict[str, str]) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    raw = P["close"]
    traded = raw.notna()
    c = raw.ffill(limit=10)                                    # a share that skipped a few days keeps its last price
    h, l = P["high"].where(traded), P["low"].where(traded)
    v = P["volume"].where(traded, 0.0).fillna(0.0)
    tov = (raw * P["volume"]).where(traded, 0.0).fillna(0.0)
    ret = c.pct_change(fill_method=None)
    ret = ret.where(ret.abs() < 0.6)                           # a larger one-day move is a data fault or a corporate action
    sma20, sma50, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    F = {
        "mom12_1": c.shift(20) / c.shift(250) - 1,             # the past year, leaving out the latest month
        "mom6_1": c.shift(20) / c.shift(125) - 1,
        "slope200": sma200 / sma200.shift(20) - 1,
        "hi52": c / c.rolling(250, min_periods=200).max(),
        "d200": c / sma200 - 1,
        "r20": c / c.shift(20) - 1,
        "d50": c / sma50 - 1,
        "x20_50": sma20 / sma50 - 1,
        "vol60": ret.rolling(60, min_periods=40).std(),
        "maxday20": ret.rolling(20).max(),
        "upvol20": v.where(ret > 0, 0).rolling(20).sum() / v.rolling(20).sum(),
    }
    mfm = ((raw - l) - (h - raw)) / (h - l).replace(0, np.nan)
    F["cmf20"] = (mfm.fillna(0) * v).rolling(20).sum() / v.rolling(20).sum()
    delta = c.diff()
    gain, loss = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    F["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    r60, sec60 = c / c.shift(60) - 1, {}
    S = pd.Series({s: sectors.get(s) or "?" for s in c.columns})
    for members in S.groupby(S).groups.values():               # the group's average, leaving the share itself out
        m = r60[list(members)]
        for s in members:
            sec60[s] = (m.sum(axis=1) - m[s].fillna(0)) / (m.notna().sum(axis=1) - m[s].notna()).replace(0, np.nan)
    F["sec60"] = pd.DataFrame(sec60)[c.columns]
    info = {"days": traded.rolling(60).sum(), "tov": tov.rolling(60).mean(), "history": c.shift(MIN_HISTORY).notna()}
    elig = (info["days"] >= MIN_DAYS) & (info["tov"] >= MIN_TOV) & (c >= MIN_PRICE) & info["history"]
    return F, elig, c, info


def scores(F: dict, elig: pd.DataFrame, rows) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """For the given days: each share's place among the rated shares on every group, and the five places added up."""
    E = elig.loc[rows]

    def rk(k):
        x = F[k.lstrip("-")].loc[rows].where(E).rank(axis=1, pct=True) - 0.5
        return -x if k.startswith("-") else x

    G = {}
    for g, ks in GROUPS.items():
        parts = [rk(k) for k in ks]
        tot, n = sum(p.fillna(0) for p in parts), sum(p.notna().astype(int) for p in parts)
        G[g] = (tot / n.replace(0, np.nan)).where(E)
    total = sum(x.fillna(0) for x in G.values()).where(E)
    return total, G


# ---------------------------------------------------------------- company figures, as they stood when each report came out

def reports(columns, bad: dict[str, set] | None = None) -> pd.DataFrame:
    """Every interim report on record, dated by the day it was published on cse.lk. Per-share figures are put onto
    today's share basis when a split came later. Reports whose figures failed this site's checks are left out."""
    import glob
    import db
    rows, bad = [], bad or {}
    for f in glob.glob(str(ROOT / "data" / "fundamentals" / "*.json")):
        d = json.loads(Path(f).read_text())
        s = d.get("symbol")
        if s not in columns:
            continue
        L = {x["id"]: x for x in d.get("listing", {}).get("quarterly", [])}
        import fundamentals
        sp = [{"date": e[0], "factor": e[1]} for e in fundamentals.share_events(s)]
        for k, q in (d.get("quarterly") or {}).items():
            u = (L.get(k) or {}).get("uploaded")
            if not u or not q.get("ok") or q.get("end") in bad.get(s, ()):
                continue
            up = pd.Timestamp(dt.datetime.fromtimestamp(u / 1000, dt.timezone.utc).date())
            fac = float(np.prod([x["factor"] for x in sp if pd.Timestamp(x["date"]) > up])) if sp else 1.0
            m, eps, nav = q.get("months"), q.get("eps"), q.get("navps")
            rows.append({"s": s, "up": up, "end": q.get("end"),
                         "ann": eps / fac * 12 / m if eps is not None and m in (3, 6, 9, 12) else None,      # earnings per share for a year at this pace
                         "nav": nav / fac if nav and nav > 0 else None, "pg": q.get("profit_growth")})
    return pd.DataFrame(rows, columns=["s", "up", "end", "ann", "nav", "pg"]).sort_values("up")


def report_ranks(R: pd.DataFrame, rows, c: pd.DataFrame, E: pd.DataFrame):
    """For the given days: each rated share's place on its latest published report (if no older than REPORT_AGE days).
    Returns the average place over the figures it has (only where it has at least two) and the count."""
    rows = pd.DatetimeIndex(rows)

    def asof(col):
        out = pd.DataFrame(np.nan, index=rows, columns=c.columns)
        for s, g in R.dropna(subset=[col]).groupby("s"):
            g = g.drop_duplicates("up", keep="last").set_index("up")[col]
            i = g.index.searchsorted(rows, side="left") - 1          # a report counts from the day after it was published
            j = np.clip(i, 0, None)
            age = (rows - g.index[j]).days
            out[s] = np.where((i >= 0) & (age <= REPORT_AGE), g.values[j], np.nan)
        return out
    px, ann, nav = c.loc[rows], asof("ann"), asof("nav")
    raw = [(ann / px).clip(-1, 1), (nav / px).where(nav > 0), (ann / nav).where(nav > 0).clip(-1, 1.5), asof("pg").clip(-300, 300)]
    ranks = [x.where(E).rank(axis=1, pct=True) - 0.5 for x in raw]
    n = sum(r.notna().astype(int) for r in ranks)
    comp = (sum(r.fillna(0) for r in ranks) / n.replace(0, np.nan)).where(n >= 2)
    return comp, n


def combined(total: pd.DataFrame, comp: pd.DataFrame | None, E: pd.DataFrame) -> pd.DataFrame:
    """Price place plus company-figure place. A share without two usable report figures is treated as average on them."""
    base = total.where(E).rank(axis=1, pct=True) - 0.5
    return base if comp is None else (base + REPORT_WEIGHT * comp.fillna(0)).where(E)


def grade(pct: float) -> str:
    return next(g for g, top in GRADES if pct <= top)


def headline(price_grade: str, fund_score: float | None) -> tuple[str, int]:
    """The rating shown: the price rating, moved at most one step by the company's checked report figures.

    The report step has not been tested against history (there are too few years of reports), so it is kept small:
    clearly strong figures (3 points or more) lift a rating one step, clearly weak ones (-2 or less) lower it one
    step, and reports never create a STRONG grade. Those come only from the tested price rating."""
    i0 = ORDER.index(price_grade)
    step = 0 if fund_score is None else 1 if fund_score >= 3 else -1 if fund_score <= -2 else 0
    i = i0 + step
    if ORDER[min(len(ORDER) - 1, max(0, i))].startswith("STRONG") and not price_grade.startswith("STRONG"):
        i = i0
    i = min(len(ORDER) - 1, max(0, i))
    return ORDER[i], i - i0


# ---------------------------------------------------------------- what followed each grade, since 2010

def _stats(fw: pd.Series, ex: pd.Series) -> dict:
    return {"n": int(len(fw)), "hit": round(float((fw > COST).mean() * 100), 1), "net": round(float((fw.mean() - COST) * 100), 2),
            "median": round(float((fw.median() - COST) * 100), 2), "p25": round(float((fw.quantile(0.25) - COST) * 100), 1),
            "p75": round(float((fw.quantile(0.75) - COST) * 100), 1), "excess": round(float(ex.mean() * 100), 2),
            "down10": round(float((fw < -0.10).mean() * 100), 1)}


def _long(c, rows, E, hz, **cols) -> pd.DataFrame:
    """One row per share and day: what the price did over the next hz trading days, buying at the next day's close."""
    f = c.shift(-(hz + 1)) / c.shift(-1) - 1
    f = f.where(f.abs() < 4).loc[rows].where(E)
    ex = f.sub(f.mean(axis=1), axis=0)                          # against the average rated share over the same days
    df = pd.DataFrame({"fw": f.stack(), "ex": ex.stack(), **{k: x.stack() for k, x in cols.items()}}).dropna(subset=["fw"])
    df.index.names = ["d", "s"]
    df["year"] = df.index.get_level_values(0).year
    return df


def study(F: dict, elig: pd.DataFrame, c: pd.DataFrame, old: pd.DataFrame | None = None, R: pd.DataFrame | None = None) -> dict:
    rows = elig.index[(elig.index >= FROM)][::5]                # one day a week
    E = elig.loc[rows]
    total, _ = scores(F, elig, rows)
    pct = total.rank(axis=1, pct=True)
    out = {"made": dt.date.today().isoformat(), "from": FROM, "cost_pct": round(COST * 100, 2), "horizons": {},
           "rated_per_day": {str(y): int(n) for y, n in E.sum(axis=1).groupby(rows.year).mean().round().items() if n > 0},
           "rule": f"traded on {MIN_DAYS} of the last 60 days, LKR {MIN_TOV / 1e6:g} million a day or more, price LKR {MIN_PRICE:g} or more, a year of prices"}
    for hz, label in HORIZONS:
        df = _long(c, rows, E, hz, pct=pct).dropna(subset=["pct"])
        df["g"] = pd.cut(df["pct"], [0] + [t for _, t in GRADES], labels=ORDER)
        H = {"label": label, "to": str(df.index.get_level_values(0).max().date()), "grades": {g: _stats(d["fw"], d["ex"]) for g, d in df.groupby("g", observed=True)}}
        H["grades"]["ALL"] = _stats(df["fw"], df["ex"])
        buy, sell = df[df.g.isin(["BUY", "STRONG BUY"])], df[df.g.isin(["SELL", "STRONG SELL"])]
        H["buy"], H["sell"] = _stats(buy["fw"], buy["ex"]), _stats(sell["fw"], sell["ex"])
        yb, ys, ya = buy.groupby("year"), sell.groupby("year"), df.groupby("year")
        H["years"] = [{"y": int(y), "buy": round(float(yb["ex"].mean().get(y, np.nan) * 100), 1), "sell": round(float(ys["ex"].mean().get(y, np.nan) * 100), 1),
                       "buy_hit": round(float((yb["fw"].apply(lambda s: (s > COST).mean())).get(y, np.nan) * 100), 0),
                       "all_hit": round(float((d["fw"] > COST).mean() * 100), 0), "sell_hit": round(float((ys["fw"].apply(lambda s: (s > COST).mean())).get(y, np.nan) * 100), 0)}
                      for y, d in ya if len(d) >= 200]
        H["buy_beat_sell"] = [sum(1 for r in H["years"] if r["buy"] > r["sell"]), len(H["years"])]
        H["periods"] = {name: round(float((buy.loc[(buy.year >= a) & (buy.year <= b), "ex"].mean() - sell.loc[(sell.year >= a) & (sell.year <= b), "ex"].mean()) * 100), 1)
                        for name, a, b in (("2010 to 2019", 2010, 2019), ("2020 to now", 2020, 2100))}
        if old is not None and hz in (20, 60):                  # the rules used before, on the same shares and days
            o = old.reindex(index=rows, columns=E.columns).where(E).stack().reindex(df.index)
            lab = pd.cut(o, [-99, -3, -1.5, 1.49, 2.99, 99], labels=ORDER)
            H["old"] = {g: _stats(d["fw"], d["ex"]) for g, d in df.assign(lab=lab).groupby("lab", observed=True)}
        out["horizons"][str(hz)] = H
    out["cases"] = out["horizons"]["60"]["grades"]["ALL"]["n"]
    if R is not None and len(R):
        out["combined"] = study_combined(F, elig, c, R)
    return out


def study_combined(F, elig, c, R) -> dict:
    """The rating as shown (price and company figures together), from the start of the report record.
    Short: about two and a half years, so it is checked against the price-only rating over the same weeks."""
    rows = elig.index[elig.index >= REPORT_FROM][::5]
    E = elig.loc[rows]
    total, _ = scores(F, elig, rows)
    comp, n = report_ranks(R, rows, c, E)
    both, price = combined(total, comp, E).rank(axis=1, pct=True), total.rank(axis=1, pct=True)
    out = {"from": REPORT_FROM, "weight": REPORT_WEIGHT, "with_figures": int((n >= 2).where(E).sum(axis=1)[E.sum(axis=1) > 0].mean()), "horizons": {}}
    for hz, label in HORIZONS[1:3]:
        df = _long(c, rows, E, hz, both=both, price=price).dropna(subset=["both"])
        H = {"label": label, "to": str(df.index.get_level_values(0).max().date())}
        for key in ("both", "price"):
            g = pd.cut(df[key], [0] + [t for _, t in GRADES], labels=ORDER)
            H[key] = {k: _stats(d["fw"], d["ex"]) for k, d in df.assign(g=g).groupby("g", observed=True)}
            b, s_ = df[g.isin(["BUY", "STRONG BUY"])], df[g.isin(["SELL", "STRONG SELL"])]
            H[key + "_gap"] = round(float((b["ex"].mean() - s_["ex"].mean()) * 100), 2)
            half = df.assign(h=[f"{d.year} {'H1' if d.month <= 6 else 'H2'}" for d in df.index.get_level_values(0)], g=g)
            H[key + "_halves"] = {h: round(float((d.loc[d.g.isin(["BUY", "STRONG BUY"]), "ex"].mean() - d.loc[d.g.isin(["SELL", "STRONG SELL"]), "ex"].mean()) * 100), 1)
                                  for h, d in half.groupby("h") if len(d) >= 300}
        H["grades"] = H["both"]
        H["grades"]["ALL"] = _stats(df["fw"], df["ex"])
        out["horizons"][str(hz)] = H
    return out


def old_scores(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The price part of the rules used before, day by day. Slow (minutes); run once to have the comparison."""
    import analysis
    out = {}
    for s, b in bars.items():
        if len(b) >= 60:
            out[s] = analysis.score_series(analysis.indicators(b[["open", "high", "low", "close", "volume"]].astype(float)))
    return pd.DataFrame(out)


# ---------------------------------------------------------------- today's ratings

def _level(p: int) -> str:
    return "very strong" if p >= 80 else "strong" if p >= 60 else "middling" if p >= 40 else "weak" if p >= 20 else "very weak"


def today(F: dict, elig: pd.DataFrame, c: pd.DataFrame, info: dict, R: pd.DataFrame | None = None) -> dict[str, dict]:
    day = elig.index[-1]
    total, G = scores(F, elig, [day])
    E = elig.loc[[day]]
    comp = report_ranks(R, [day], c, E)[0] if R is not None and len(R) else None
    pct = combined(total, comp, E).rank(axis=1, pct=True).iloc[0]
    ppct = total.rank(axis=1, pct=True).iloc[0]
    if comp is not None:
        G = {**G, "reports": comp}
    gp = {g: (x.rank(axis=1, pct=True).iloc[0] * 100) for g, x in G.items()}
    n = int(elig.loc[day].sum())
    out = {}
    for s in elig.columns:
        if pd.isna(c.at[day, s]):
            continue
        if not elig.at[day, s]:
            d, t, px = info["days"].at[day, s], info["tov"].at[day, s], c.at[day, s]
            why = ("it has under a year of price history" if not info["history"].at[day, s] else
                   f"its price is under LKR {MIN_PRICE:g}, where one price step is too large a share of the price" if px < MIN_PRICE else
                   f"it is too thinly traded: {int(d) if d == d else 0} of the last 60 days, about LKR {(t or 0) / 1e6:.2f} million a day")
            out[s] = {"rated": False, "why": why}
            continue
        p = float(pct[s])
        groups = {}
        for g in list(GROUPS) + ["reports"]:
            v = gp[g].get(s) if g in gp else None
            if v == v:
                groups[g] = int(min(99, max(1, round(v))))
        out[s] = {"rated": True, "grade": grade(p), "pct": int(min(99, max(1, round(p * 100)))), "of": n, "groups": groups,
                  "price_grade": grade(float(ppct[s])), "price_pct": int(min(99, max(1, round(float(ppct[s]) * 100)))),
                  "words": [f"{WORDS[g][0]}: {_level(v)} (ahead of {v}% of rated shares)" for g, v in groups.items()]}
    return out


def build(sectors: dict[str, str], bars: dict[str, pd.DataFrame] | None = None, refit: bool = False, with_old: bool = False, bad: dict | None = None) -> tuple[dict, dict]:
    """Today's rating for every share, and the record of what each grade went on to do (re-measured monthly)."""
    if bars is None:
        import db
        import longterm
        meta, bars = longterm.load_meta(), {}
        for s in db.symbols_with_data(30):
            try:
                bars[s] = longterm.load(s, meta)[0]
            except Exception:
                pass
    F, elig, c, info = measures(panel(bars), sectors)
    R = reports(set(c.columns), bad)
    S = json.loads(MODEL.read_text()) if MODEL.exists() else None
    stale = not S or "combined" not in S or (dt.date.today() - dt.date.fromisoformat(S["made"])).days >= 30
    if refit or stale or with_old:
        new = study(F, elig, c, old_scores(bars) if with_old else None, R)
        if S and not with_old:                                  # keep the comparison with the earlier rules; it is slow to redo
            for hz, H in new["horizons"].items():
                if "old" in S["horizons"].get(hz, {}):
                    H["old"] = S["horizons"][hz]["old"]
            new["old_made"] = S.get("old_made")
        else:
            new["old_made"] = new["made"]
        S = new
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        MODEL.write_text(json.dumps(S, indent=1))
    return today(F, elig, c, info, R), S


if __name__ == "__main__":
    import sys
    import sectors as _sectors
    R, S = build(_sectors.load(), refit=True, with_old="old" in sys.argv[1:])
    rated = [r for r in R.values() if r["rated"]]
    print(f"{len(rated)} of {len(R)} shares rated; test: {S['cases']:,} cases from {S['from']}")
    for hz, H in S["horizons"].items():
        print(f"\n{H['label']}: Buy grades beat Sell grades in {H['buy_beat_sell'][0]} of {H['buy_beat_sell'][1]} years; by period {H['periods']}")
        for g in ORDER[::-1] + ["ALL"]:
            x = H["grades"].get(g)
            if x:
                print(f"  {g:12} n {x['n']:6}  gained after costs {x['hit']:5.1f}%  typical {x['median']:6.2f}%  middle half {x['p25']:6.1f} to {x['p75']:5.1f}%  against market {x['excess']:6.2f}  fell 10%+ {x['down10']:5.1f}%")
        if "old" in H:
            print("  earlier rules:", {g: (x["hit"], x["excess"]) for g, x in H["old"].items()})
    C = S.get("combined")
    if C:
        print(f"\nWITH COMPANY FIGURES from {C['from']} ({C['with_figures']} rated shares a week have them):")
        for hz, H in C["horizons"].items():
            print(f" {H['label']}: buy-sell gap {H['both_gap']} pts (price only {H['price_gap']}); halves {H['both_halves']} vs {H['price_halves']}")
            for g in ORDER[::-1]:
                a, b = H["both"].get(g), H["price"].get(g)
                if a and b: print(f"  {g:12} with figures: gained {a['hit']:5.1f}% vs avg {a['excess']:6.2f} | price only: {b['hit']:5.1f}% {b['excess']:6.2f}")
