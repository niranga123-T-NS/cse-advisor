"""Collect report figures for every company and turn them into fundamental metrics.

    python fundamentals.py                 # process new reports for all watchlist stocks (resumable)
    python fundamentals.py JKH.N0000 ...   # just these
    FUND_BUDGET_MIN=60 python fundamentals.py   # stop after 60 minutes (the rest continues next run)

Per company it reads the latest 10 quarterly reports and the latest 5 annual reports.
Results go to data/fundamentals/<SYMBOL>.json; each report is read once (by CSE report id).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import db
import reports

N_QUARTERLY, N_ANNUAL = 10, 5
FDIR = Path(__file__).parent / "data" / "fundamentals"


def _path(symbol):
    return FDIR / f"{symbol}.json"


def load(symbol: str) -> dict:
    p = _path(symbol)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"symbol": symbol, "quarterly": {}, "annual": {}}


def save(symbol: str, d: dict):
    FDIR.mkdir(parents=True, exist_ok=True)
    _path(symbol).write_text(json.dumps(d, indent=1, sort_keys=True))


# ------------------------------------------------------------------ collection job

def _autocommit(msg: str):
    """Save progress to the repo during long GitHub runs (set AUTOCOMMIT=1)."""
    if os.environ.get("AUTOCOMMIT") != "1":
        return
    import subprocess
    sh = lambda *c: subprocess.run(c, capture_output=True, text=True)
    sh("git", "add", "data/fundamentals")
    if sh("git", "diff", "--cached", "--quiet").returncode:
        sh("git", "commit", "-m", msg)
        sh("git", "pull", "--rebase", "-X", "theirs", "origin", "main")
        print("  progress saved:", sh("git", "push").returncode == 0)


def collect(symbols: list[str], budget_min: float):
    t0 = time.time()
    last_commit = [time.time()]
    out_of_time = lambda: (time.time() - t0) / 60 > budget_min
    # Pass 1 lists reports and reads quarterlies (small PDFs); pass 2 reads annual reports (large PDFs).
    for kind, n_keep in (("quarterly", N_QUARTERLY), ("annual", N_ANNUAL)):
        for s in symbols:
            if out_of_time():
                print(f"Time budget of {budget_min} min reached - the rest continues next run.")
                return
            d = load(s)
            if kind == "quarterly" or "listing" not in d:
                lst = reports.list_reports(s)
                if not lst:
                    continue
                d["listing"] = {k: v[:{"quarterly": N_QUARTERLY, "annual": N_ANNUAL}[k]] for k, v in lst.items()}
                d["listed_at"] = time.strftime("%Y-%m-%d")
            wanted = d["listing"].get(kind, [])[:n_keep]
            store = d.setdefault(kind, {})
            todo = [it for it in wanted if store.get(it["id"], {}).get("v") != reports.PARSER_VERSION]
            for it in todo:
                if out_of_time():
                    break
                print(f"{s}  {kind:9}  {it['end']}  {it['title'][:60]}")
                res = reports.read_report(it, kind)
                if res.get("ok"):
                    store[it["id"]] = {**res, "end": it["end"], "title": it["title"], "url": it["url"]}
                time.sleep(0.5)
            # forget reports that dropped out of the window
            keep = {it["id"] for it in wanted}
            d[kind] = {k: v for k, v in store.items() if k in keep}
            save(s, d)
            if time.time() - last_commit[0] > 20 * 60:
                _autocommit(f"Report figures (progress) {time.strftime('%Y-%m-%d %H:%M')}")
                last_commit[0] = time.time()


# ------------------------------------------------------------------ metrics

def _ok_per_share(v, price, lo=0.002, hi=5.0):
    """Per-share value must be plausible relative to the price (rejects mis-read numbers/units)."""
    return v is not None and price and lo * price <= abs(v) <= hi * price


def _consecutive(ends: list[str]) -> bool:
    """Newest-first period ends roughly 3 months apart."""
    import datetime as dt
    d = [dt.date.fromisoformat(e) for e in ends if e]
    return len(d) == len(ends) and all(70 <= (d[i] - d[i + 1]).days <= 110 for i in range(len(d) - 1))


def metrics(symbol: str, price: float | None) -> dict | None:
    d = load(symbol)
    q = sorted(d.get("quarterly", {}).values(), key=lambda x: x["end"] or "", reverse=True)
    a = sorted(d.get("annual", {}).values(), key=lambda x: x["end"] or "", reverse=True)
    if not q and not a:
        return None
    m = {"quarters_read": len(q), "annuals_read": len(a), "notes": []}

    # --- financial year end (month), from annual reports, else from 12-month interim reports
    fy_month = None
    for x in a + [x for x in q if x.get("months") == 12]:
        if x.get("end"):
            fy_month = int(x["end"][5:7]); break

    def cum_months(end):
        if not (fy_month and end):
            return None
        return (int(end[5:7]) - fy_month) % 12 or 12

    # --- full-year EPS: collect every source per year end and prefer values two sources agree on
    cands: dict[str, list[tuple[float, str]]] = {}
    def cand(end, v, src):
        if end and _ok_per_share(v, price):
            cands.setdefault(end, []).append((v, src))
    def prev_year(end):
        return f"{int(end[:4]) - 1}{end[4:]}" if end else None
    for x in a:
        cand(x["end"], x.get("eps"), "annual")
        cand(prev_year(x["end"]), x.get("eps_prior"), "annual-prior")
    for x in q:
        if cum_months(x["end"]) == 12 and x.get("months") == 12:
            cand(x["end"], x.get("eps"), "interim")
            cand(prev_year(x["end"]), x.get("eps_prior"), "interim-prior")
    fy_eps = {}
    for end, lst in cands.items():
        best = None
        for v, src in lst:
            agree = sum(abs(v - w) <= 0.03 * abs(v) for w, _ in lst)
            pri = (agree, src == "annual")
            if best is None or pri > best[0]:
                best = (pri, v, agree)
        fy_eps[end] = (best[1], best[2])  # value, number of agreeing sources
    m["fy_month"] = fy_month

    # --- trailing 12-month EPS
    ttm, how = None, None
    last_fy_end = max(fy_eps) if fy_eps else None
    qv = [x for x in q if _ok_per_share(x.get("eps"), price)]
    if qv:
        L = qv[0]
        cm = cum_months(L["end"])
        prior_fy = max((e for e in fy_eps if e < L["end"]), default=None)
        if cm and cm < 12 and L.get("months") == cm and prior_fy and _ok_per_share(L.get("eps_prior"), price, lo=0) \
                and abs(L["eps"] - fy_eps[prior_fy][0]) > 1e-9:
            ttm = L["eps"] + fy_eps[prior_fy][0] - L["eps_prior"]
            how = f"{cm} months to {L['end']} + last full year − same period last year"
        elif all(x.get("months") == 3 for x in qv[:4]) and len(qv) >= 4 and _consecutive([x["end"] for x in qv[:4]]):
            ttm = sum(x["eps"] for x in qv[:4])
            how = f"sum of last 4 quarters to {L['end']}"
        # sanity: TTM far from the last full year is more likely a mis-read than reality
        if ttm is not None and last_fy_end and fy_eps[last_fy_end][0] > 0:
            r = ttm / fy_eps[last_fy_end][0]
            if not 0.5 <= r <= 2.0:
                ttm, how = None, None
    if ttm is None and last_fy_end:
        ttm, how = fy_eps[last_fy_end][0], f"last full year to {last_fy_end}"
    if ttm is not None:
        m["eps_ttm"], m["eps_basis"] = round(ttm, 2), how
        if price and ttm > 0:
            m["pe"] = round(price / ttm, 1)
    m["fy_eps"] = [{"end": e, "eps": round(v, 2), "sources": n} for e, (v, n) in sorted(fy_eps.items(), reverse=True)][:6]

    # --- growth (latest report vs same period last year)
    lq = q[0] if q else None
    if lq:
        m["latest_report"] = {"end": lq["end"], "title": lq["title"], "url": lq["url"]}
        m["profit_growth"] = lq.get("profit_growth")
        m["revenue_growth"] = lq.get("revenue_growth")
        # consistency: how many of the last quarters showed profit growth
        pg = [x.get("profit_growth") for x in q if x.get("profit_growth") is not None]
        if len(pg) >= 4:
            m["profit_growth_hit"] = f"{sum(g > 0 for g in pg)}/{len(pg)}"
            m["_pg_ratio"] = sum(g > 0 for g in pg) / len(pg)

    # --- book value
    nav = next((x["navps"] for x in q + a if _ok_per_share(x.get("navps"), price, lo=0.02, hi=50)), None)
    if nav:
        m["navps"] = round(nav, 2)
        if price and nav > 0:
            m["pbv"] = round(price / nav, 2)
        if ttm is not None and nav > 0:
            m["roe"] = round(ttm / nav * 100, 1)

    # --- annual history: EPS & dividends (up to 5 years)
    years = []
    for x in a:
        row = {"end": x["end"], "url": x["url"]}
        if x["end"] in fy_eps:
            row["eps"] = round(fy_eps[x["end"]][0], 2)
        e_ref = row.get("eps")
        if x.get("dps") is not None and price and 0 < x["dps"] <= 0.5 * price \
                and (e_ref is None or e_ref <= 0 or x["dps"] <= 1.5 * e_ref):
            row["dps"] = round(x["dps"], 2)
        years.append(row)
    m["annual"] = years
    dps = next((y["dps"] for y in years if "dps" in y), None)
    if dps and price:
        m["dps"], m["div_yield"] = dps, round(dps / price * 100, 1)
    e = [y["eps"] for y in years if "eps" in y]
    if len(e) >= 3 and e[-1] > 0 and e[0] > 0:
        n = len(e) - 1
        m["eps_cagr"] = round(((e[0] / e[-1]) ** (1 / n) - 1) * 100, 1)
        m["eps_cagr_years"] = n
    m["quarters"] = [{"end": x["end"], "months": x.get("months"), "url": x["url"],
                      "eps": x["eps"] if _ok_per_share(x.get("eps"), price) else None,
                      "profit_growth": x.get("profit_growth"), "revenue_growth": x.get("revenue_growth")}
                     for x in q]
    found = sum(k in m for k in ("pe", "pbv", "profit_growth", "div_yield", "eps_cagr"))
    m["confidence"] = "good" if found >= 4 else "partial" if found >= 2 else "low"
    return m


def score(m: dict | None) -> tuple[float, list[str]]:
    """Fundamental points, same style as the technical rules (each line is a reason)."""
    if not m:
        return 0.0, []
    s, why = 0.0, []

    def add(p, t):
        nonlocal s
        s += p
        why.append(f"{'+' if p > 0 else ''}{p:g}  {t}")

    ttm = m.get("eps_ttm")
    if ttm is not None and ttm <= 0:
        add(-1, f"Loss-making over the last 12 months (EPS {ttm})")
    elif "pe" in m:
        pe = m["pe"]
        if pe < 8: add(1, f"Cheap: P/E {pe}")
        elif pe < 15: add(0.5, f"Reasonable P/E {pe}")
        elif pe > 25: add(-0.5, f"Expensive: P/E {pe}")
    if "pbv" in m:
        if m["pbv"] < 0.8: add(0.5, f"Trades below book value (P/BV {m['pbv']})")
        elif m["pbv"] > 3: add(-0.5, f"High P/BV {m['pbv']}")
    g = m.get("profit_growth")
    if g is not None:
        if g > 15: add(1, f"Profit up {g}% vs same period last year")
        elif g > 0: add(0.5, f"Profit up {g}% vs last year")
        elif g < -15: add(-1, f"Profit down {abs(g)}% vs last year")
        else: add(-0.5, f"Profit down {abs(g)}% vs last year")
    r = m.get("revenue_growth")
    if r is not None:
        if r > 10: add(0.5, f"Revenue up {r}%")
        elif r < -10: add(-0.5, f"Revenue down {abs(r)}%")
    if m.get("_pg_ratio") is not None:
        if m["_pg_ratio"] >= 0.75: add(0.5, f"Consistent profit growth ({m['profit_growth_hit']} reports)")
        elif m["_pg_ratio"] <= 0.25: add(-0.5, f"Profits mostly falling ({m['profit_growth_hit']} reports grew)")
    if "roe" in m:
        if m["roe"] >= 15: add(0.5, f"Strong ROE {m['roe']}%")
        elif m["roe"] < 5: add(-0.5, f"Weak ROE {m['roe']}%")
    if m.get("div_yield", 0) >= 5:
        add(0.5, f"Dividend yield {m['div_yield']}%")
    if "eps_cagr" in m:
        c = m["eps_cagr"]
        if c >= 10: add(0.5, f"EPS grew {c}%/yr over {m['eps_cagr_years']} years")
        elif c < 0: add(-0.5, f"EPS shrank {abs(c)}%/yr over {m['eps_cagr_years']} years")
    return s, why


if __name__ == "__main__":
    syms = [a.upper() for a in sys.argv[1:]] or db.watchlist()
    collect(syms, float(os.environ.get("FUND_BUDGET_MIN", "330")))
