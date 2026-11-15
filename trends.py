"""Trends in every aspect of a share, of each industry group and of the market.

Price trend is read the same simple way in four time frames:

    up        the price is above its average for that time frame AND that average is itself rising
    down      the price is below a falling average
    sideways  anything else (price and average disagree, or the average is flat)

    short  = 20 trading days (about a month)      medium = 50 days (a quarter)
    long   = 200 days (about a year)              years  = 750 days (about three years; needs the long history)

Around it: how long the trend has lasted, how steady it is, where the price sits in its 52-week range, and the
trend in volume, earnings, revenue and dividends, and in strength against the market and its own sector.
Only verified report figures are used for the earnings side.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

FRAMES = (("short", 20), ("medium", 50), ("long", 200), ("years", 750))
FLAT = 0.002          # an average that moved less than this over a quarter of its window counts as flat


def _r(v, n=2):
    return None if v is None or v != v or v in (float("inf"), float("-inf")) else round(float(v), n)


def _states(c: pd.Series, n: int) -> pd.Series:
    """+1 up, -1 down, 0 sideways for every day, for the time frame with an n-day average."""
    ma = c.rolling(n).mean()
    was = ma.shift(max(2, n // 4))
    rising, falling = ma > was * (1 + FLAT), ma < was * (1 - FLAT)
    s = pd.Series(0, index=c.index, dtype=int)
    s[(c > ma) & rising] = 1
    s[(c < ma) & falling] = -1
    return s.where(ma.notna() & was.notna())


def price_frames(c: pd.Series) -> dict:
    out = {}
    for name, n in FRAMES:
        if len(c) < n + max(2, n // 4) + 1:
            out[name] = None
            continue
        st = _states(c, n).dropna()
        now = int(st.iloc[-1])
        changed = st[st != now]
        days = int(len(st) - (st.index.get_loc(changed.index[-1]) + 1)) if len(changed) else int(len(st))
        ma = c.rolling(n).mean().iloc[-1]
        seg = np.log(c.tail(n).to_numpy(dtype=float))
        x = np.arange(len(seg))
        fit = np.corrcoef(x, seg)[0, 1] if seg.std() > 0 else 0.0
        out[name] = {"dir": now, "days": days, "all_data": not len(changed), "chg": _r((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, 1),
                     "vs_avg": _r((c.iloc[-1] / ma - 1) * 100, 1), "steady": _r(fit * fit, 2)}
    return out


def strength(c: pd.Series) -> float | None:
    """Performance over the last year with the latest quarter counted double (the usual 'relative strength' recipe).
    Ranked against every other share to give a 1-99 score."""
    if len(c) < 130:
        return None
    r = lambda n: c.iloc[-1] / c.iloc[-1 - min(n, len(c) - 1)] - 1
    return float(0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(252))


def one(bars: pd.DataFrame, fund: dict | None) -> dict:
    """Every trend for one share except the ones that need the rest of the market (added in `build`)."""
    c, v = bars["close"].dropna(), bars["volume"].fillna(0.0)
    out: dict = {"price": price_frames(c)}
    yr = bars.tail(252)
    hi, lo = float(yr["high"].max()), float(yr["low"].min())
    last = float(c.iloc[-1])
    out["range"] = {"high": _r(hi), "low": _r(lo), "pos": _r((last - lo) / (hi - lo) * 100, 0) if hi > lo else None,
                    "from_high": _r((last / hi - 1) * 100, 1), "from_low": _r((last / lo - 1) * 100, 1),
                    "days_since_high": int(len(yr) - 1 - int(np.argmax(yr["high"].to_numpy()))),
                    "days_since_low": int(len(yr) - 1 - int(np.argmin(yr["low"].to_numpy())))}
    if len(v) >= 80:
        recent, before = float(v.tail(20).mean()), float(v.iloc[-80:-20].mean())
        ratio = recent / before if before > 0 else None
        out["volume"] = {"ratio": _r(ratio, 2), "dir": 0 if ratio is None else 1 if ratio >= 1.3 else -1 if ratio <= 0.7 else 0,
                         "turnover": _r(float((c * v).tail(20).mean()), 0)}
    else:
        out["volume"] = None
    f = fund or {}
    ok = f.get("verified") or {}
    # earnings: year-on-year profit growth in the latest verified reports, newest first
    qs = [q for q in (f.get("quarters") or []) if q.get("growth_ok") and q.get("profit_growth") is not None][:6]
    if qs:
        ups = sum(q["profit_growth"] > 0 for q in qs)
        latest = qs[0]["profit_growth"]
        d = 1 if latest > 0 and ups >= math.ceil(len(qs) * 2 / 3) else -1 if latest < 0 and ups <= len(qs) // 3 else 0
        out["earnings"] = {"dir": d, "latest": _r(latest, 1), "up": ups, "n": len(qs), "end": qs[0].get("end")}
    else:
        out["earnings"] = None
    out["revenue"] = {"dir": 1 if f["revenue_growth"] > 0 else -1 if f["revenue_growth"] < 0 else 0, "latest": _r(f["revenue_growth"], 1)} \
        if ok.get("revenue_growth") and f.get("revenue_growth") is not None else None
    eps = [(a["end"][:4], a["eps"]) for a in reversed(f.get("annual") or []) if a.get("eps_ok") and a.get("eps") is not None]
    if len(eps) >= 3:
        ups = sum(b[1] > a[1] for a, b in zip(eps, eps[1:]))
        d = 1 if eps[-1][1] > eps[0][1] and ups >= (len(eps) - 1) / 2 else -1 if eps[-1][1] < eps[0][1] and ups <= (len(eps) - 1) / 2 else 0
        out["eps_years"] = {"dir": d, "series": eps, "up": ups, "n": len(eps) - 1}
    else:
        out["eps_years"] = None
    dps = [(a["end"][:4], a["dps"]) for a in reversed(f.get("annual") or []) if a.get("dps_ok") and a.get("dps") is not None]
    if len(dps) >= 2:
        d = 1 if dps[-1][1] > dps[0][1] * 1.02 else -1 if dps[-1][1] < dps[0][1] * 0.98 else 0
        out["dividend"] = {"dir": d, "series": dps, "paid": sum(x[1] > 0 for x in dps), "n": len(dps)}
    else:
        out["dividend"] = None
    out["_strength"] = strength(c)
    out["_r20"] = _r((c.iloc[-1] / c.iloc[-21] - 1) * 100, 2) if len(c) > 21 else None
    out["_r60"] = _r((c.iloc[-1] / c.iloc[-61] - 1) * 100, 2) if len(c) > 61 else None
    out["_r250"] = _r((c.iloc[-1] / c.iloc[-251] - 1) * 100, 2) if len(c) > 251 else None
    out["_above50"] = bool(last > c.tail(50).mean()) if len(c) >= 50 else None
    out["_above200"] = bool(last > c.tail(200).mean()) if len(c) >= 200 else None
    out["_new_high"] = bool(out["range"]["days_since_high"] <= 4)
    out["_new_low"] = bool(out["range"]["days_since_low"] <= 4)
    return out


def headline(t: dict) -> tuple[str, int]:
    """One line for the whole picture, and a code: 2 up everywhere, 1 mostly up, 0 mixed, -1 mostly down, -2 down everywhere."""
    p = t["price"]
    s, m, l = (p[k]["dir"] if p.get(k) else None for k in ("short", "medium", "long"))
    if l is None and m is None:
        return "Too little price history to read a trend", 0
    if l is None:
        return {1: "Rising over the last quarter", -1: "Falling over the last quarter", 0: "No clear trend over the last quarter"}[m], m or 0
    if (s, m, l) == (1, 1, 1):
        return "Rising in every time frame", 2
    if (s, m, l) == (-1, -1, -1):
        return "Falling in every time frame", -2
    if l == 1 and m == 1:
        return "Rising, with a short pause", 1
    if l == 1 and s == -1:
        return "Long-term rise, now pulling back", 0
    if l == 1:
        return "Long-term rise, losing pace", 1 if m == 0 else 0
    if l == -1 and m == -1:
        return "Falling, with a short bounce", -1
    if l == -1 and s == 1 and m == 1:
        return "Turning up after a long fall", 0
    if l == -1 and s == 1:
        return "Long-term fall, short bounce", -1
    if l == -1:
        return "Long-term fall, slowing", -1 if m == 0 else 0
    if m == 1 and s == 1:
        return "Turning up from a flat year", 1
    if m == -1 and s == -1:
        return "Turning down from a flat year", -1
    return "Sideways: no clear trend", 0


def build(bars: dict[str, pd.DataFrame], funds: dict[str, dict], sectors: dict[str, str], names: dict[str, str],
          market_level: pd.Series | None) -> tuple[dict[str, dict], dict]:
    """(per-share trends, the market / sector / lists summary)."""
    T = {s: one(b, funds.get(s)) for s, b in bars.items() if len(b) >= 30}
    # strength against the whole market: 1-99
    st = pd.Series({s: t["_strength"] for s, t in T.items() if t["_strength"] is not None})
    rank = (st.rank(pct=True) * 98 + 1).round().astype(int) if len(st) else pd.Series(dtype=int)
    sec_members: dict[str, list[str]] = {}
    for s in T:
        if sectors.get(s):
            sec_members.setdefault(sectors[s], []).append(s)
    med = lambda xs: float(np.median(xs)) if len(xs) else None
    sec_med = {k: {"r20": med([T[s]["_r20"] for s in m if T[s]["_r20"] is not None]),
                   "r60": med([T[s]["_r60"] for s in m if T[s]["_r60"] is not None]),
                   "r250": med([T[s]["_r250"] for s in m if T[s]["_r250"] is not None])} for k, m in sec_members.items()}
    for s, t in T.items():
        t["rs"] = int(rank[s]) if s in rank.index else None
        sm = sec_med.get(sectors.get(s) or "")
        t["vs_sector"] = {"sector": sectors.get(s), "r20": _r(t["_r20"] - sm["r20"], 1) if sm and sm["r20"] is not None and t["_r20"] is not None else None,
                          "r60": _r(t["_r60"] - sm["r60"], 1) if sm and sm["r60"] is not None and t["_r60"] is not None else None} if sm else None
        t["headline"], t["code"] = headline(t)

    liquid = lambda s: ((T[s]["volume"] or {}).get("turnover") or 0)
    brief = lambda s: {"s": s, "name": names.get(s, ""), "h": T[s]["headline"], "rs": T[s]["rs"], "r20": T[s]["_r20"], "r60": T[s]["_r60"],
                       "tov": liquid(s), "sector": sectors.get(s, "")}
    top = lambda pick, key=None, n=12: [brief(s) for s in sorted([s for s in T if pick(T[s], s)], key=key or (lambda s: -liquid(s)))[:n]]
    fr = lambda t, k: (t["price"].get(k) or {})
    tradeable = lambda s: liquid(s) >= 1_000_000

    def share(cond):                      # % of shares for which cond is true, among those it can be judged for
        xs = [cond(t) for t in T.values()]
        xs = [x for x in xs if x is not None]
        return _r(100 * sum(xs) / len(xs), 0) if xs else None

    market: dict = {"shares": len(T),
                    "breadth": {k: {"up": share(lambda t, k=k: (fr(t, k)["dir"] == 1) if fr(t, k) else None),
                                    "down": share(lambda t, k=k: (fr(t, k)["dir"] == -1) if fr(t, k) else None)} for k, _ in FRAMES},
                    "above50": share(lambda t: t["_above50"]), "above200": share(lambda t: t["_above200"]),
                    "new_highs": sum(t["_new_high"] for t in T.values()), "new_lows": sum(t["_new_low"] for t in T.values()),
                    "advancers": sum((t["_r20"] or 0) > 0 for t in T.values()), "decliners": sum((t["_r20"] or 0) < 0 for t in T.values()),
                    "earnings_up": share(lambda t: (t["earnings"]["dir"] == 1) if t["earnings"] else None),
                    "earnings_down": share(lambda t: (t["earnings"]["dir"] == -1) if t["earnings"] else None)}
    # the market's own turnover: all shares added up, last 20 days against the 60 before
    tv = pd.DataFrame({s: (b["close"] * b["volume"]).tail(90) for s, b in bars.items()}).sum(axis=1).sort_index()
    if len(tv) >= 80:
        market["turnover"] = {"recent": _r(tv.tail(20).mean(), 0), "before": _r(tv.iloc[-80:-20].mean(), 0),
                              "ratio": _r(tv.tail(20).mean() / tv.iloc[-80:-20].mean(), 2)}
    if market_level is not None and len(market_level) > 260:
        lv = market_level.dropna()
        market["index"] = {"price": price_frames(lv), "level": _r(lv.iloc[-1]),
                           "from_high": _r((lv.iloc[-1] / lv.tail(252).max() - 1) * 100, 1), "r20": _r((lv.iloc[-1] / lv.iloc[-21] - 1) * 100, 1),
                           "r60": _r((lv.iloc[-1] / lv.iloc[-61] - 1) * 100, 1), "r250": _r((lv.iloc[-1] / lv.iloc[-251] - 1) * 100, 1)}
        market["index"]["headline"], market["index"]["code"] = headline({"price": market["index"]["price"]})
    sec_rows = []
    for k, m in sorted(sec_members.items()):
        if len(m) < 3:
            continue
        pct = lambda cond: _r(100 * sum(1 for s in m if cond(T[s])) / len(m), 0)
        e = [T[s]["earnings"]["dir"] for s in m if T[s]["earnings"]]
        lead = sorted([s for s in m if T[s]["_r60"] is not None and tradeable(s)], key=lambda s: -T[s]["_r60"])
        sec_rows.append({"sector": k, "n": len(m), "r20": _r(sec_med[k]["r20"], 1), "r60": _r(sec_med[k]["r60"], 1), "r250": _r(sec_med[k]["r250"], 1),
                         "up_medium": pct(lambda t: fr(t, "medium").get("dir") == 1), "up_long": pct(lambda t: fr(t, "long").get("dir") == 1),
                         "down_medium": pct(lambda t: fr(t, "medium").get("dir") == -1), "down_long": pct(lambda t: fr(t, "long").get("dir") == -1),
                         "above200": pct(lambda t: bool(t["_above200"])), "earnings_up": _r(100 * sum(x == 1 for x in e) / len(e), 0) if e else None,
                         "leaders": [x.split(".")[0] for x in lead[:3]], "laggards": [x.split(".")[0] for x in lead[-3:][::-1]] if len(lead) > 5 else []})
    for r in sec_rows:                   # by how many of the group's companies are rising or falling over a quarter and a year
        um, ul, dm, dl = (r[k] or 0 for k in ("up_medium", "up_long", "down_medium", "down_long"))
        r["label"] = ("rising" if um >= 50 and ul >= 40 else "falling" if dm >= 40 and dl >= 50 else "turning up" if um >= 40 and dl >= 40
                      else "weakening" if ul >= 40 and dm >= 30 else "mixed")
    lists = {
        "all_up": top(lambda t, s: t["code"] == 2 and tradeable(s), lambda s: -(T[s]["rs"] or 0)),
        "turned_up": top(lambda t, s: fr(t, "medium").get("dir") == 1 and fr(t, "medium").get("days", 99) <= 10 and tradeable(s), lambda s: fr(T[s], "medium")["days"]),
        "turned_down": top(lambda t, s: fr(t, "medium").get("dir") == -1 and fr(t, "medium").get("days", 99) <= 10 and tradeable(s), lambda s: fr(T[s], "medium")["days"]),
        "all_down": top(lambda t, s: t["code"] == -2 and tradeable(s), lambda s: (T[s]["rs"] or 0)),
        "new_highs": top(lambda t, s: t["_new_high"] and tradeable(s)),
        "new_lows": top(lambda t, s: t["_new_low"] and tradeable(s)),
        "earnings_up_price_down": top(lambda t, s: bool(t["earnings"]) and t["earnings"]["dir"] == 1 and fr(t, "long").get("dir") == -1 and tradeable(s)),
        "price_up_volume_up": top(lambda t, s: fr(t, "medium").get("dir") == 1 and (t["volume"] or {}).get("dir") == 1 and tradeable(s),
                                  lambda s: -(T[s]["volume"]["ratio"] or 0)),
    }
    summary = {"market": market, "sectors": sorted(sec_rows, key=lambda r: -(r["r60"] if r["r60"] is not None else -999)), "lists": lists,
               "counts": {str(k): sum(t["code"] == k for t in T.values()) for k in (2, 1, 0, -1, -2)}}
    return T, summary


def public(t: dict) -> dict:
    """The per-share trends without the working fields."""
    return {k: v for k, v in t.items() if not k.startswith("_")}
