"""How a share measures up against the published methods of well-known investors and traders.

Each "lens" is a checklist of rules taken from what that person published (book named with each). They are
simplified, mechanical readings: NOT the opinion of any of these people about any share, and no substitute for the
judgement their methods also call for.

Three results per rule:  met  /  not met  /  cannot be checked.
Several of the original rules need figures this site does not have (debt, cash flow, margins, who owns the shares).
Those are listed and marked "cannot be checked" rather than guessed, and a share is only called a fit when enough
of the list could be checked. Report figures are used only when they passed the data checks.

Investing lenses   graham  buffett  lynch  greenblatt  dreman
Trading lenses     oneil  minervini  weinstein  darvas  turtle
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LENSES = [
    {"id": "graham", "kind": "invest", "who": "Benjamin Graham", "name": "Defensive value",
     "idea": "Buy sound, established companies only when the price is low against earnings and assets.",
     "source": "The Intelligent Investor (criteria for the defensive investor)"},
    {"id": "buffett", "kind": "invest", "who": "Warren Buffett", "name": "Quality at a sensible price",
     "idea": "Own businesses that earn high returns on equity year after year, bought without overpaying.",
     "source": "Berkshire Hathaway shareholder letters (as commonly summarised; he publishes no formula)"},
    {"id": "lynch", "kind": "invest", "who": "Peter Lynch", "name": "Growth at a reasonable price",
     "idea": "Pay a price-to-earnings multiple no higher than the rate at which earnings are growing.",
     "source": "One Up on Wall Street"},
    {"id": "greenblatt", "kind": "invest", "who": "Joel Greenblatt", "name": "Magic formula",
     "idea": "Rank every company by cheapness and by return on capital; buy those that score well on both.",
     "source": "The Little Book That Beats the Market (approximated here: see the notes)"},
    {"id": "dreman", "kind": "invest", "who": "David Dreman", "name": "Contrarian value",
     "idea": "Buy what the market likes least: the cheapest fifth by earnings, assets or dividend yield.",
     "source": "Contrarian Investment Strategies"},
    {"id": "oneil", "kind": "trade", "who": "William O'Neil", "name": "CAN SLIM",
     "idea": "Fast-growing earnings, a share price at new highs, strong demand, in a rising market.",
     "source": "How to Make Money in Stocks"},
    {"id": "minervini", "kind": "trade", "who": "Mark Minervini", "name": "Trend template",
     "idea": "Only buy shares already in a confirmed long-term uptrend and near their highs.",
     "source": "Trade Like a Stock Market Wizard"},
    {"id": "weinstein", "kind": "trade", "who": "Stan Weinstein", "name": "Stage analysis",
     "idea": "Buy early in the advancing stage, when price is above a rising 30-week average.",
     "source": "Secrets for Profiting in Bull and Bear Markets"},
    {"id": "darvas", "kind": "trade", "who": "Nicolas Darvas", "name": "Box method",
     "idea": "Buy a share near its highs when it breaks out of the top of its trading range on volume.",
     "source": "How I Made $2,000,000 in the Stock Market"},
    {"id": "turtle", "kind": "trade", "who": "Richard Dennis (the Turtle traders)", "name": "Breakout trend following",
     "idea": "Buy a new 20- or 55-day high, size the risk by volatility, and sell on a new short-term low.",
     "source": "The Turtle trading rules (as published by former Turtles)"},
]
FINANCIAL = ("Banks", "Diversified Financials", "Insurance", "Utilities")     # left out by Greenblatt's own rules


def _r(v, n=2):
    return None if v is None or v != v or v in (float("inf"), float("-inf")) else round(float(v), n)


def crit(text: str, result, value: str | None = None, must: bool = False) -> dict:
    """One rule. `must` marks the rule the whole method rests on: without it the share cannot be a fit.
    A rule given as None with a reason is one this site can never check (it gets "na")."""
    c = {"t": text, "r": None if result is None else bool(result), "v": value}
    if must:
        c["m"] = True
    return c


def never(text: str, why: str) -> dict:
    """A rule from the original method that needs figures this site does not have."""
    return {"t": text, "r": None, "v": why, "na": True}


# share of checkable rules needed for "fits" and "partly" (default 0.8 and 0.5). Minervini's template and the
# Turtle entry are all-or-nothing by their authors' own rules.
FIT = {"minervini": (1.0, 0.75), "turtle": (1.0, 0.6)}


def _verdict(cs: list[dict], lens: str = "") -> tuple[int, int, str]:
    """fits / partly / no / unknown. A failed key rule means no; a key rule that cannot be checked, or too few
    checked rules (under 60% of those this site is able to check), means it cannot be judged."""
    can = [c for c in cs if c["r"] is not None]
    met = sum(c["r"] for c in can)
    musts = [c for c in cs if c.get("m")]
    if any(c["r"] is False for c in musts):
        return met, len(can), "no"
    possible = sum(1 for c in cs if not c.get("na"))
    if any(c["r"] is None for c in musts) or len(can) < max(3, math.ceil(0.6 * possible)):
        return met, len(can), "unknown"
    fit, part = FIT.get(lens, (0.8, 0.5))
    ratio = met / len(can)
    return met, len(can), "fits" if ratio >= fit else "partly" if ratio >= part else "no"


def _v(f: dict, k: str):
    """A report figure, only if it passed the data checks."""
    return f.get(k) if (f.get("verified") or {}).get(k) and f.get(k) is not None else None


def _annual(f: dict, key: str) -> list[float]:
    return [a[key] for a in reversed(f.get("annual") or []) if a.get(key + "_ok") and a.get(key) is not None]


def _growth(f: dict) -> tuple[float | None, str]:
    """Earnings growth per year: from the annual reports when they link up, otherwise this year to date."""
    if f.get("eps_cagr") is not None and (f.get("verified") or {}).get("eps_cagr", True):
        return float(f["eps_cagr"]), f"{f.get('eps_cagr_years', '')}-year average"
    g = _v(f, "eps_growth_ytd")
    return (float(g), "this year to date") if g is not None else (None, "")


# ------------------------------------------------------------------ price-side working

def tech(b: pd.DataFrame) -> dict:
    c, h, l, v = (b[k].to_numpy(dtype=float) for k in ("close", "high", "low", "volume"))
    n = len(c)
    sma = lambda k, back=0: float(c[n - k - back:n - back].mean()) if n >= k + back else None
    t = {"n": n, "last": float(c[-1]), "sma50": sma(50), "sma150": sma(150), "sma200": sma(200), "sma200_then": sma(200, 22),
         "sma150_then": sma(150, 50), "sma150_before": sma(150, 150),
         "hi52": float(h[-252:].max()), "lo52": float(l[-252:].min()), "hi_all": float(h.max()), "years": n / 245,
         "v20": float(v[-20:].mean()), "v50": float(v[-70:-20].mean()) if n >= 70 else None,
         "r20": float(c[-1] / c[-21] - 1) if n > 21 else None, "r60": float(c[-1] / c[-61] - 1) if n > 61 else None}
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    t["atr20"] = float(tr[-20:].mean())
    return t


def darvas_box(b: pd.DataFrame) -> dict:
    """Darvas's boxes over the last 150 days: a top is a high not exceeded for three days, then a bottom is a low
    not undercut for three days. A close above the top is a breakout; a close below the bottom ends the box."""
    d = b.tail(150)
    h, l, c, v = (d[k].to_numpy(dtype=float) for k in ("high", "low", "close", "volume"))
    n = len(c)
    state, hi_i, lo_i, top, bot, last_break = "top", 0, 0, None, None, None
    for i in range(1, n):
        if state == "top":
            if h[i] >= h[hi_i]:
                hi_i = i
            elif i - hi_i >= 3:
                top, state, lo_i = h[hi_i], "bottom", hi_i + 1 + int(np.argmin(l[hi_i + 1:i + 1]))
        if state == "bottom":
            if h[i] > top:
                state, hi_i, top = "top", i, None
                continue
            if l[i] <= l[lo_i]:
                lo_i = i
            elif i - lo_i >= 3:
                bot, state = l[lo_i], "box"
        elif state == "box":
            if c[i] > top:
                avg = v[max(0, i - 20):i].mean() or 1.0
                last_break = {"i": i, "top": float(top), "bottom": float(bot), "vol": float(v[i] / avg)}
                state, hi_i, top, bot = "top", i, None, None
            elif c[i] < bot:
                state, hi_i, top, bot = "top", i, None, None
    out = {"in_box": state == "box", "top": _r(top) if state == "box" else None, "bottom": _r(bot) if state == "box" else None}
    if last_break:
        out["breakout"] = {"days_ago": n - 1 - last_break["i"], "above": _r(last_break["top"]), "stop": _r(last_break["bottom"]), "volume_x": _r(last_break["vol"], 1)}
    return out


def turtle(b: pd.DataFrame, enter: int, leave: int) -> dict:
    """Is the share in a Turtle long position now? Enter on a close above the highest high of the previous `enter`
    days; leave on a close below the lowest low of the previous `leave` days."""
    d = b.tail(260)
    h, l, c = (d[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    n, pos, entry = len(c), False, None
    for i in range(enter, n):
        if not pos and c[i] > h[i - enter:i].max():
            pos, entry = True, i
        elif pos and c[i] < l[i - leave:i].min():
            pos, entry = False, None
    return {"long": pos, "days": (n - 1 - entry) if pos else None, "entry": _r(c[entry]) if pos else None,
            "exit_below": _r(l[n - leave:n].min()) if n > leave else None, "buy_above": _r(h[n - enter:n].max()) if n > enter else None}


# ------------------------------------------------------------------ the lenses

def graham(f, t, ctx, s):
    pe, pb, eps, nav = _v(f, "pe"), _v(f, "pbv"), _v(f, "eps_ttm"), _v(f, "navps")
    ae, ad = _annual(f, "eps"), _annual(f, "dps")
    g, basis = _growth(f)
    number = math.sqrt(22.5 * eps * nav) if eps and nav and eps > 0 and nav > 0 else None
    cs = [
        crit("Price no more than 15 times earnings", None if pe is None else 0 < pe <= 15, None if pe is None else f"P/E {pe}", must=True),
        crit("Price no more than 1.5 times net assets, or P/E x P/B no more than 22.5",
             None if pb is None else (pb <= 1.5 or (pe is not None and 0 < pe and pe * pb <= 22.5)),
             None if pb is None else f"P/B {pb}" + (f", P/E x P/B {pe * pb:.1f}" if pe else ""), must=True),
        crit("Price at or below the Graham number (the square root of 22.5 x earnings per share x net assets per share)",
             None if number is None else t["last"] <= number,
             None if number is None else f"Graham number {number:.2f}; the price is {abs(t['last'] / number - 1) * 100:.0f}% {'below' if t['last'] <= number else 'above'} it"),
        crit(f"No loss in any year on record (Graham asked for ten years; {len(ae)} are on file)", None if len(ae) < 3 else all(x > 0 for x in ae),
             None if len(ae) < 3 else f"{sum(x > 0 for x in ae)} of {len(ae)} years profitable"),
        crit(f"A dividend in every year on record (Graham asked for twenty; {len(ad)} are on file)", None if len(ad) < 3 else all(x > 0 for x in ad),
             None if len(ad) < 3 else f"paid in {sum(x > 0 for x in ad)} of {len(ad)} years"),
        crit("Earnings per share growing (Graham: up by at least a third over ten years, about 3% a year)", None if g is None else g >= 3,
             None if g is None else f"{g:+.1f}% a year ({basis})"),
        crit("A large, established company (in the larger half of the market by value)", None if ctx["mcap"].get(s) is None else ctx["mcap"][s] >= ctx["mcap_median"],
             None if ctx["mcap"].get(s) is None else f"market value LKR {ctx['mcap'][s] / 1e9:.1f} billion"),
        never("Strong finances: current assets at least twice current liabilities, and long-term debt below working capital",
              "balance-sheet detail is not read by this site"),
    ]
    key = {"Graham number": _r(number), "Margin of safety": f"{(1 - t['last'] / number) * 100:+.0f}%" if number else None}
    return cs, key


def buffett(f, t, ctx, s):
    roe, pe = _v(f, "roe"), _v(f, "pe")
    ae = _annual(f, "eps")
    g, basis = _growth(f)
    hit = f.get("profit_growth_hit") if (f.get("verified") or {}).get("profit_growth_hit") else None
    up, n = (int(x) for x in hit.split("/")) if hit else (None, None)
    cs = [
        crit("High return on equity: 15% or more", None if roe is None else roe >= 15, None if roe is None else f"ROE {roe}%", must=True),
        crit("Earnings that keep rising: up in at least two of every three recent reports", None if not n or n < 3 else up / n >= 2 / 3,
             None if not n else f"profit up in {up} of the last {n} reports"),
        crit("No loss-making year on record", None if len(ae) < 3 else all(x > 0 for x in ae), None if len(ae) < 3 else f"{sum(x > 0 for x in ae)} of {len(ae)} years profitable"),
        crit("Earnings per share growing over the years", None if g is None else g >= 5, None if g is None else f"{g:+.1f}% a year ({basis})"),
        crit("A sensible price for that quality: no more than 15 times earnings (this site's reading; he gives no number)",
             None if pe is None else 0 < pe <= 15, None if pe is None else f"P/E {pe} (earnings yield {100 / pe:.1f}%)" if pe > 0 else f"P/E {pe}"),
        never("Little or no debt", "debt is not read by this site"),
        never("A lasting advantage over competitors, and able, honest management", "a matter of judgement, not of figures"),
    ]
    return cs, {"Return on equity": f"{roe}%" if roe is not None else None, "Earnings yield": f"{100 / pe:.1f}%" if pe and pe > 0 else None}


def lynch(f, t, ctx, s):
    pe, dy = _v(f, "pe"), _v(f, "div_yield") or 0.0
    g, basis = _growth(f)
    peg = pe / g if pe and pe > 0 and g and g > 0 else None
    total = (g + dy) / pe if pe and pe > 0 and g is not None else None
    hit = f.get("profit_growth_hit") if (f.get("verified") or {}).get("profit_growth_hit") else None
    up, n = (int(x) for x in hit.split("/")) if hit else (None, None)
    kind = None if g is None else "slow grower" if g < 5 else "stalwart" if g < 15 else "fast grower" if g <= 50 else "growing too fast to last"
    cs = [
        crit("P/E no higher than the earnings growth rate (PEG of 1 or less)", None if pe is None or g is None else (peg is not None and peg <= 1),
             None if pe is None or g is None else (f"PEG {peg:.2f} (P/E {pe} against growth {g:.1f}%)" if peg else f"earnings are not growing ({g:+.1f}%)"), must=True),
        crit("Growth plus dividend yield at least 1.5 times the P/E", None if total is None else total >= 1.5,
             None if total is None else f"({g:.1f}% + {dy:.1f}%) / {pe} = {total:.2f}"),
        crit("Growth he could trust to continue: between 10% and 30% a year", None if g is None else 10 <= g <= 30, None if g is None else f"{g:+.1f}% a year ({basis})"),
        crit("Earnings rising in recent reports", None if not n or n < 3 else up / n >= 2 / 3, None if not n else f"profit up in {up} of the last {n} reports"),
        never("A strong balance sheet with little debt", "debt is not read by this site"),
    ]
    return cs, {"PEG": _r(peg), "His category": kind}


def greenblatt(f, t, ctx, s):
    pe, roe = _v(f, "pe"), _v(f, "roe")
    rank, n = ctx["magic"].get(s), ctx["magic_n"]
    fin = ctx["sector"].get(s) in FINANCIAL
    ey = 100 / pe if pe and pe > 0 else None
    cs = [
        crit("High earnings yield: in the best third of the market", None if ey is None else ey >= ctx["ey_top3"], None if ey is None else f"earnings yield {ey:.1f}%"),
        crit("High return on capital: in the best third of the market", None if roe is None else roe >= ctx["roe_top3"], None if roe is None else f"ROE {roe}% (used in place of return on capital)"),
        crit("Among the best 30 companies on the two ranks combined", None if rank is None and not fin else (rank is not None and rank <= 30),
             f"ranked {rank} of {n}" if rank else ("not ranked: left out by the formula" if fin else None), must=True),
        crit("Not a bank, finance, insurance or utility company (the formula leaves these out)", not fin, ctx["sector"].get(s) or "industry group not known", must=True),
    ]
    return cs, {"Combined rank": f"{rank} of {n}" if rank else None}


def dreman(f, t, ctx, s):
    pe, pb, dy = _v(f, "pe"), _v(f, "pbv"), _v(f, "div_yield")
    pg = _v(f, "profit_growth")
    cs = [
        crit("In the cheapest fifth of the market by P/E or by price to net assets",
             None if pe is None and pb is None else bool((pe is not None and 0 < pe <= ctx["pe_q20"]) or (pb is not None and 0 < pb <= ctx["pb_q20"])),
             "; ".join(x for x in (f"P/E {pe} (cheapest fifth: {ctx['pe_q20']:.1f} or less)" if pe is not None else "",
                                    f"P/B {pb} (cheapest fifth: {ctx['pb_q20']:.2f} or less)" if pb is not None else "") if x) or None, must=True),
        crit("Dividend yield above the market's middle", None if dy is None else dy >= ctx["dy_median"], None if dy is None else f"yield {dy}% (market middle {ctx['dy_median']:.1f}%)"),
        crit("Earnings are not shrinking", None if pg is None else pg >= 0, None if pg is None else f"profit {pg:+.1f}% on last year"),
        crit("A larger company (in the larger half of the market by value)", None if ctx["mcap"].get(s) is None else ctx["mcap"][s] >= ctx["mcap_median"],
             None if ctx["mcap"].get(s) is None else f"market value LKR {ctx['mcap'][s] / 1e9:.1f} billion"),
        never("Strong finances (low debt, earnings well above interest)", "debt is not read by this site"),
    ]
    return cs, {}


def oneil(f, t, ctx, s):
    q = next((x for x in (f.get("quarters") or []) if x.get("growth_ok") and x.get("profit_growth") is not None), None)
    cq = q["profit_growth"] if q else _v(f, "eps_growth_ytd")
    g, basis = _growth(f)
    rs = ctx["rs"].get(s)
    near = t["last"] >= 0.95 * t["hi52"]
    vx = t["v20"] / t["v50"] if t.get("v50") else None
    cs = [
        crit("C  Current earnings: latest report up 25% or more on a year earlier", None if cq is None else cq >= 25, None if cq is None else f"{cq:+.1f}%", must=True),
        crit("A  Annual earnings: growing 25% or more a year", None if g is None else g >= 25, None if g is None else f"{g:+.1f}% a year ({basis})"),
        crit("N  New highs: within 5% of its 52-week high", near, f"{(t['last'] / t['hi52'] - 1) * 100:+.1f}% from the high of {t['hi52']:.2f}"),
        crit("S  Supply and demand: trading volume building as the price rises", None if vx is None or t["r20"] is None else (vx >= 1.3 and t["r20"] > 0),
             None if vx is None else f"volume {vx:.1f}x its earlier level; price {t['r20'] * 100:+.1f}% in a month"),
        crit("L  Leader: stronger than 80% of all shares over the past year", None if rs is None else rs >= 80, None if rs is None else f"strength score {rs} of 99"),
        never("I  Institutional sponsorship: funds are buying", "holdings by funds are not available in a usable form"),
        crit("M  Market direction: the market itself is in an uptrend", ctx["market_up"], ctx["market_note"]),
    ]
    return cs, {}


def minervini(f, t, ctx, s):
    if t["n"] < 222 or None in (t["sma50"], t["sma150"], t["sma200"], t["sma200_then"]):
        return [crit("Needs about a year of prices", None, f"{t['n']} trading days on file")] * 3, {}
    L, rs = t["last"], ctx["rs"].get(s)
    cs = [
        crit("Price above both its 150-day and 200-day averages", L > t["sma150"] and L > t["sma200"], f"price {L:.2f}; averages {t['sma150']:.2f} and {t['sma200']:.2f}"),
        crit("150-day average above the 200-day average", t["sma150"] > t["sma200"]),
        crit("200-day average rising for at least a month", t["sma200"] > t["sma200_then"], f"{(t['sma200'] / t['sma200_then'] - 1) * 100:+.1f}% in a month"),
        crit("50-day average above both the 150-day and 200-day", t["sma50"] > t["sma150"] and t["sma50"] > t["sma200"], f"50-day {t['sma50']:.2f}"),
        crit("Price above its 50-day average", L > t["sma50"]),
        crit("Price at least 30% above its 52-week low", L >= 1.3 * t["lo52"], f"{(L / t['lo52'] - 1) * 100:+.0f}% above the low of {t['lo52']:.2f}"),
        crit("Price within 25% of its 52-week high", L >= 0.75 * t["hi52"], f"{(L / t['hi52'] - 1) * 100:+.0f}% from the high of {t['hi52']:.2f}"),
        crit("Stronger than 70% of all shares over the past year", None if rs is None else rs >= 70, None if rs is None else f"strength score {rs} of 99"),
    ]
    return cs, {}


def weinstein(f, t, ctx, s):
    if t["sma150"] is None or t["sma150_then"] is None:
        return [crit("Needs about a year of prices", None, f"{t['n']} trading days on file")] * 3, {"Stage": None}
    L, ma = t["last"], t["sma150"]
    slope = ma / t["sma150_then"] - 1
    before = (t["sma150_then"] / t["sma150_before"] - 1) if t.get("sma150_before") else None
    rising, falling = slope > 0.015, slope < -0.015
    if rising:
        stage = 2 if L > ma else 3
    elif falling:
        stage = 4 if L < ma else 1
    else:
        stage = 3 if (before is not None and before > 0.03) else 1
    name = {1: "1: basing (flat after a fall)", 2: "2: advancing", 3: "3: topping (stalling after a rise)", 4: "4: declining"}[stage]
    vx = t["v20"] / t["v50"] if t.get("v50") else None
    mkt = ctx.get("market_r60")
    cs = [
        crit("Stage 2: price above a rising 30-week average", stage == 2, f"stage {name}; price {(L / ma - 1) * 100:+.0f}% from the average, which moved {slope * 100:+.1f}% in 10 weeks", must=True),
        crit("Doing better than the market over the last three months", None if t["r60"] is None or mkt is None else t["r60"] * 100 > mkt,
             None if t["r60"] is None or mkt is None else f"share {t['r60'] * 100:+.1f}% against the market's {mkt:+.1f}%"),
        crit("Volume expanding as it advances", None if vx is None else vx >= 1.3, None if vx is None else f"volume {vx:.1f}x its earlier level"),
        crit("Not yet stretched: no more than 20% above the 30-week average", L <= 1.2 * ma, f"{(L / ma - 1) * 100:+.0f}% from the average"),
        crit("Little resistance overhead: within 10% of its high of the last two years", L >= 0.9 * float(ctx["hi2y"].get(s) or t["hi52"]),
             f"{(L / float(ctx['hi2y'].get(s) or t['hi52']) - 1) * 100:+.0f}% from that high"),
    ]
    return cs, {"Stage": name}


def darvas(f, t, ctx, s):
    box, vx = ctx["box"][s], (t["v20"] / t["v50"] if t.get("v50") else None)
    br = box.get("breakout")
    fresh = bool(br and br["days_ago"] <= 5)
    cs = [
        crit("At or near its highest price: within 10% of the 52-week high", t["last"] >= 0.9 * t["hi52"], f"{(t['last'] / t['hi52'] - 1) * 100:+.1f}% from {t['hi52']:.2f}", must=True),
        crit("Near the highest price on record (Darvas wanted all-time highs)", None if t["years"] < 3 else t["last"] >= 0.85 * t["hi_all"],
             None if t["years"] < 3 else f"{(t['last'] / t['hi_all'] - 1) * 100:+.0f}% from the highest in {t['years']:.0f} years of prices"),
        crit("Has just broken out above the top of a box (in the last 5 days)", fresh,
             (f"broke above {br['above']} {br['days_ago']} day{'s' if br['days_ago'] != 1 else ''} ago" if br else "no breakout in the last 150 days")
             + (f"; now in a box between {box['bottom']} and {box['top']}" if box["in_box"] else "")),
        crit("The breakout came on heavy volume (at least 1.5 times normal)", None if not br else br["volume_x"] >= 1.5, None if not br else f"{br['volume_x']}x normal volume that day"),
        crit("Trading volume is building", None if vx is None else vx >= 1.3, None if vx is None else f"volume {vx:.1f}x its earlier level"),
    ]
    key = {"Current box": f"{box['bottom']} to {box['top']}" if box["in_box"] else None, "Buy point": f"a close above {box['top']}" if box["in_box"] else None,
           "Stop after the last breakout": br["stop"] if fresh else None}
    return cs, key


def turtle_lens(f, t, ctx, s):
    s1, s2 = ctx["t20"][s], ctx["t55"][s]
    n = t["atr20"]
    tov = ctx["tov"].get(s) or 0
    cs = [
        crit("In a breakout trade on either rule below", bool(s1["long"] or s2["long"]), None, must=True),
        crit("In a 20-day breakout trade: closed above its 20-day high and has not since closed below its 10-day low", s1["long"],
             f"in the trade for {s1['days']} day{'s' if s1['days'] != 1 else ''}, entered near {s1['entry']}" if s1["long"] else f"no; the next entry is a close above {s1['buy_above']}"),
        crit("In a 55-day breakout trade: closed above its 55-day high and has not since closed below its 20-day low", s2["long"],
             f"in the trade for {s2['days']} day{'s' if s2['days'] != 1 else ''}, entered near {s2['entry']}" if s2["long"] else f"no; the next entry is a close above {s2['buy_above']}"),
        crit("A fresh signal: the 20-day breakout came within the last 5 days", bool(s1["long"] and s1["days"] <= 5), None),
        crit("Liquid enough to get in and out (LKR 5 million or more traded a day)", tov >= 5_000_000, f"LKR {tov / 1e6:.1f} million a day"),
    ]
    key = {"N (average daily range)": _r(n), "Stop (2 N below the price)": _r(t["last"] - 2 * n), "Risk to that stop": f"{2 * n / t['last'] * 100:.1f}%",
           "Exit: close below": s1["exit_below"] if s1["long"] else None}
    return cs, key


FUNCS = {"graham": graham, "buffett": buffett, "lynch": lynch, "greenblatt": greenblatt, "dreman": dreman,
         "oneil": oneil, "minervini": minervini, "weinstein": weinstein, "darvas": darvas, "turtle": turtle_lens}


# ------------------------------------------------------------------ everything

def build(bars: dict[str, pd.DataFrame], funds: dict[str, dict], sectors: dict[str, str], names: dict[str, str],
          T: dict[str, dict], market: dict, shares: dict[str, float]) -> tuple[dict[str, list], dict]:
    """(lenses for each share, the leaderboards)."""
    syms = [s for s in bars if len(bars[s]) >= 60 and s in T]
    tk = {s: tech(bars[s]) for s in syms}
    pos = lambda k: [funds[s][k] for s in syms if _v(funds.get(s) or {}, k) is not None and funds[s][k] > 0]
    pes, pbs, dys, roes = pos("pe"), pos("pbv"), pos("div_yield"), [funds[s]["roe"] for s in syms if _v(funds.get(s) or {}, "roe") is not None]
    mcap = {s: shares[s] * tk[s]["last"] for s in syms if shares.get(s)}
    # Greenblatt's two ranks (earnings yield, return on equity) over companies that have both and are not financial
    both = [s for s in syms if _v(funds.get(s) or {}, "pe") and funds[s]["pe"] > 0 and _v(funds[s], "roe") is not None and sectors.get(s) not in FINANCIAL]
    by_ey = {s: i for i, s in enumerate(sorted(both, key=lambda s: funds[s]["pe"]))}
    by_roe = {s: i for i, s in enumerate(sorted(both, key=lambda s: -funds[s]["roe"]))}
    order = sorted(both, key=lambda s: by_ey[s] + by_roe[s])
    idx = (market.get("index") or {}).get("price") or {}
    m_, l_ = idx.get("medium"), idx.get("long")
    market_up = None if not m_ or not l_ else (m_["vs_avg"] > 0 and l_["vs_avg"] > 0)
    ctx = {"sector": sectors, "mcap": mcap, "mcap_median": float(np.median(list(mcap.values()))) if mcap else 0.0,
           "pe_q20": float(np.percentile(pes, 20)) if pes else 0.0, "pb_q20": float(np.percentile(pbs, 20)) if pbs else 0.0,
           "dy_median": float(np.median(dys)) if dys else 0.0,
           "ey_top3": float(np.percentile([100 / x for x in pes], 66.7)) if pes else 0.0, "roe_top3": float(np.percentile(roes, 66.7)) if roes else 0.0,
           "magic": {s: i + 1 for i, s in enumerate(order)}, "magic_n": len(order),
           "rs": {s: T[s].get("rs") for s in syms}, "tov": {s: ((T[s].get("volume") or {}).get("turnover") or 0) for s in syms},
           "market_up": market_up, "market_r60": (market.get("index") or {}).get("r60"),
           "market_note": "not known" if market_up is None else f"the ASPI is {m_['vs_avg']:+.1f}% from its 50-day average and {l_['vs_avg']:+.1f}% from its 200-day average",
           "box": {s: darvas_box(bars[s]) for s in syms}, "t20": {s: turtle(bars[s], 20, 10) for s in syms}, "t55": {s: turtle(bars[s], 55, 20) for s in syms},
           "hi2y": {s: float(bars[s]["high"].tail(504).max()) for s in syms}}
    out, board = {}, {x["id"]: [] for x in LENSES}
    for s in syms:
        f = funds.get(s) or {}
        res = []
        for L in LENSES:
            cs, key = FUNCS[L["id"]](f, tk[s], ctx, s)
            met, can, verdict = _verdict(cs, L["id"])
            res.append({"id": L["id"], "met": met, "can": can, "of": len(cs), "verdict": verdict, "crit": cs, "key": {k: v for k, v in key.items() if v is not None}})
            if verdict in ("fits", "partly"):
                board[L["id"]].append({"s": s, "name": names.get(s, ""), "met": met, "can": can, "fits": verdict == "fits", "tov": ctx["tov"].get(s) or 0})
        out[s] = res
    for k in board:
        board[k].sort(key=lambda x: (-x["fits"], -x["met"] / x["can"], -x["met"], -x["tov"]))
        board[k] = {"fits": sum(x["fits"] for x in board[k]), "partly": sum(not x["fits"] for x in board[k]), "top": board[k][:15]}
    summary = {"lenses": LENSES, "board": board, "market_up": market_up, "market_note": ctx["market_note"],
               "shares": [{"s": s, "name": names.get(s, ""), "v": "".join({"fits": "F", "partly": "P", "no": "N", "unknown": "U"}[x["verdict"]] for x in out[s])} for s in sorted(out)]}
    return out, summary
