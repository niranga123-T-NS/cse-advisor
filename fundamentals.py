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


def share_counts(symbol: str) -> tuple[float | None, float]:
    """Shares in issue for this class, and for the other class (voting / non-voting) of the same company."""
    import cse_client as cse

    def qty(sym):
        j = cse.post("companyInfoSummery", {"symbol": sym}) or {}
        try:
            return float((j.get("reqSymbolInfo") or {}).get("quantityIssued") or 0) or None
        except (TypeError, ValueError):
            return None
    base, cls = symbol.split(".", 1) if "." in symbol else (symbol, "N0000")
    other = f"{base}.X0000" if cls.startswith("N") else f"{base}.N0000"
    return qty(symbol), (qty(other) if other in db.security_names() else None) or 0.0


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
                d["shares"], d["shares_other_class"] = share_counts(s)
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


def _close(a, b, tol):
    return a is not None and b is not None and b != 0 and abs(a / b - 1) <= tol


def _consecutive(ends: list[str]) -> bool:
    """Newest-first period ends roughly 3 months apart."""
    import datetime as dt
    d = [dt.date.fromisoformat(e) for e in ends if e]
    return len(d) == len(ends) and all(70 <= (d[i] - d[i + 1]).days <= 110 for i in range(len(d) - 1))


def _year_before(end: str, items: list[dict], months=None) -> dict | None:
    """The report whose period ends ~1 year before `end` (same column period when known)."""
    import datetime as dt
    try:
        target = dt.date.fromisoformat(end).replace(year=int(end[:4]) - 1)
    except ValueError:
        target = dt.date(int(end[:4]) - 1, int(end[5:7]), 28)
    for x in items:
        if not x.get("end"):
            continue
        if abs((dt.date.fromisoformat(x["end"]) - target).days) <= 7 and (months is None or x.get("months") == months):
            return x
    return None


def audit(d: dict, price: float | None) :
    """Cross-check every figure read from the reports.

    Marks per report which fields are VERIFIED (`_ok`) or CONTRADICTED (`_bad`) and returns
    (quarterly, annual, checks). A figure only counts towards the score if it is verified and not contradicted.
    Checks used:
      1. EPS = attributable profit x unit / shares in issue          (latest 4 quarterly reports)
      2. the "same period last year" column of a report = the figure in last year's report   (all reports)
      3. full-year EPS in the annual report = EPS in the 12-month interim report
      4. EPS growth agrees with profit growth (catches wrong rows/columns)
      5. net assets per share moves < 30% between consecutive reports
      6. per-share values plausible vs the share price; column period consistent with the financial year
    """
    q = sorted((dict(x) for x in d.get("quarterly", {}).values()), key=lambda x: x["end"] or "", reverse=True)
    a = sorted((dict(x) for x in d.get("annual", {}).values()), key=lambda x: x["end"] or "", reverse=True)
    checks = []
    for x in q + a:
        x["_ok"], x["_bad"] = set(), set()
        for f in ("eps", "eps_prior", "navps", "dps", "dps_prior"):
            if x.get(f) is not None and not _ok_per_share(x[f], price, lo=0.0005, hi=20 if "nav" in f else 5):
                x["_bad"].add(f)
                checks.append(("fail", x["end"], f"{f} {x[f]} implausible vs price {price} - ignored"))

    def chk(level, x, msg):
        checks.append((level, x["end"], msg))

    # financial year end month
    fy_month = next((int(x["end"][5:7]) for x in a if x.get("end")), None) or \
        next((int(x["end"][5:7]) for x in q if x.get("months") == 12 and x.get("end")), None)
    cum = lambda end: ((int(end[5:7]) - fy_month) % 12 or 12) if fy_month and end else None

    # 6b. column period vs financial year
    for x in q:
        c = cum(x["end"])
        if x.get("months") and c and x["months"] not in (3, c):
            x["_bad"].update({"eps", "eps_prior", "profit", "revenue"})
            chk("fail", x, f"first column is {x['months']} months but the period should be 3 or {c} months - column unclear, ignored")

    # 1. EPS vs profit / shares
    shares = d.get("shares") or 0
    both = shares + (d.get("shares_other_class") or 0)
    for x in q[:4]:
        if x.get("eps") and x.get("profit") and shares and "eps" not in x["_bad"]:
            units = [x["unit"]] if x.get("unit") else [1, 1e3, 1e6]
            hit = next(((u, S) for u in units for S in (shares, both) if S and _close(x["profit"] * u / S, x["eps"], 0.15)), None)
            if hit:
                x["_ok"].update({"eps", "profit"})
                chk("ok", x, f"EPS {x['eps']} = profit {x['profit']:,.0f} x {hit[0]:g} / {hit[1]:,.0f} shares")
            else:
                implied = x["profit"] * (x.get("unit") or 1) / shares
                chk("warn", x, f"EPS {x['eps']} does not match profit / shares ({implied:.2f}) - unit, share count or row may be wrong")

    # 2. prior-year column vs last year's report
    for group, fields in ((q, ("eps", "profit", "revenue")), (a, ("eps", "dps"))):
        for x in group:
            y = _year_before(x["end"], group, x.get("months") if group is q else None)
            if not y:
                continue
            for f in fields:
                pv, cv = x.get(f + "_prior"), y.get(f)
                if pv is None or cv is None or f in y["_bad"] or f + "_prior" in x["_bad"]:
                    continue
                if _close(pv, cv, 0.02) or (abs(pv) < 1 and abs(pv - cv) < 0.011):
                    # the row was read correctly, so this report's current figure (same row) is right too
                    x["_ok"].update({f, f + "_prior"}); y["_ok"].add(f)
                    chk("ok", x, f"{f} last-year column {pv:,} = {y['end']} report {cv:,}")
                elif not _close(pv, cv, 0.10):
                    x["_bad"].add(f + "_prior"); y["_bad"].add(f)
                    chk("fail", x, f"{f} last-year column {pv:,} ≠ {y['end']} report {cv:,} (mis-read or restated) - both ignored")
                else:
                    chk("warn", x, f"{f} last-year column {pv:,} vs {y['end']} report {cv:,} (small difference, likely restated)")

    # 3. annual EPS vs 12-month interim EPS
    for x in a:
        r = next((y for y in q if y["end"] == x["end"] and y.get("months") == 12), None)
        if r and x.get("eps") is not None and r.get("eps") is not None:
            if _close(x["eps"], r["eps"], 0.03):
                x["_ok"].add("eps"); r["_ok"].add("eps")
                chk("ok", x, f"annual EPS {x['eps']} = 12-month interim EPS {r['eps']}")
            else:
                chk("warn", x, f"annual EPS {x['eps']} ≠ 12-month interim EPS {r['eps']} (group vs company, or mis-read)")

    # 4. EPS growth vs profit growth
    for x in q:
        e, ep, p, pp = x.get("eps"), x.get("eps_prior"), x.get("profit"), x.get("profit_prior")
        if None in (e, ep, p, pp) or ep <= 0 or pp <= 0:
            continue
        ge, gp = e / ep - 1, p / pp - 1
        if abs(ge - gp) <= 0.10 + 0.10 * abs(gp):
            x["_ok"].add("growth")
            chk("ok", x, f"EPS growth {ge:+.0%} consistent with profit growth {gp:+.0%}")
        else:
            x["_bad"].add("growth")
            chk("warn", x, f"EPS growth {ge:+.0%} vs profit growth {gp:+.0%} disagree - growth not used")

    # 5. NAV stability
    navs = [x for x in q if x.get("navps") and "navps" not in x["_bad"]]
    for n, o in zip(navs, navs[1:]):
        if _close(n["navps"], o["navps"], 0.30):
            n["_ok"].add("navps"); o["_ok"].add("navps")
        else:
            n["_bad"].add("navps")
            chk("warn", n, f"net assets/share {n['navps']} jumped from {o['navps']} ({o['end']}) - not used")
    return q, a, [{"level": l, "end": e, "msg": m} for l, e, m in checks], fy_month, cum


def _v(x, f):
    return f in x["_ok"] and f not in x["_bad"] and x.get(f) is not None


def metrics(symbol: str, price: float | None) -> dict | None:
    d = load(symbol)
    if not d.get("quarterly") and not d.get("annual"):
        return None
    q, a, checks, fy_month, cum = audit(d, price)
    m = {"quarters_read": len(q), "annuals_read": len(a), "fy_month": fy_month}
    ver = {}

    # --- full-year EPS per year end: verified if two sources agree or a cross-check confirmed it
    fy = {}
    def add_fy(end, v, ok):
        if end and v is not None and _ok_per_share(v, price, lo=0.0005):
            fy.setdefault(end, []).append((v, ok))
    for x in a:
        add_fy(x["end"], x.get("eps"), _v(x, "eps"))
    for x in q:
        if cum(x["end"]) == 12 and x.get("months") == 12 and "eps" not in x["_bad"]:
            add_fy(x["end"], x.get("eps"), _v(x, "eps"))
    fy_eps = {}
    for end, lst in fy.items():
        v, ok = max(lst, key=lambda t: (t[1], sum(_close(t[0], w, 0.03) for w, _ in lst)))
        agree = sum(_close(v, w, 0.03) for w, _ in lst)
        fy_eps[end] = (v, ok or agree >= 2)
    m["fy_eps"] = [{"end": e, "eps": round(v, 3), "verified": ok} for e, (v, ok) in sorted(fy_eps.items(), reverse=True)][:6]

    # --- trailing 12-month EPS
    ttm, how, ttm_ok = None, None, False
    if q:
        L = q[0]
        c = cum(L["end"])
        prior_fy = max((e for e in fy_eps if e < L["end"]), default=None)
        if c and c < 12 and L.get("months") == c and prior_fy and L.get("eps") is not None and L.get("eps_prior") is not None \
                and not ({"eps", "eps_prior"} & L["_bad"]):
            ttm = L["eps"] + fy_eps[prior_fy][0] - L["eps_prior"]
            how = f"{c} months to {L['end']} + full year to {prior_fy} − same {c} months last year"
            ttm_ok = _v(L, "eps") and _v(L, "eps_prior") and fy_eps[prior_fy][1]
        elif len(q) >= 4 and all(x.get("months") == 3 and x.get("eps") is not None and "eps" not in x["_bad"] for x in q[:4]) \
                and _consecutive([x["end"] for x in q[:4]]):
            ttm = sum(x["eps"] for x in q[:4])
            how = f"sum of the last 4 quarters to {L['end']}"
            ttm_ok = sum(_v(x, "eps") for x in q[:4]) >= 2
    last_fy = max(fy_eps) if fy_eps else None
    if ttm is not None and last_fy and fy_eps[last_fy][0] > 0 and not 0.4 <= ttm / fy_eps[last_fy][0] <= 2.5:
        checks.append({"level": "fail", "end": q[0]["end"], "msg": f"12-month EPS {ttm:.2f} far from last full year {fy_eps[last_fy][0]} - not used"})
        ttm = None
    if ttm is None and last_fy:
        ttm, how, ttm_ok = fy_eps[last_fy][0], f"full year to {last_fy}", fy_eps[last_fy][1]
    if ttm is not None:
        m["eps_ttm"], m["eps_basis"] = round(ttm, 3), how
        ver["eps_ttm"] = ttm_ok
        if price and ttm > 0:
            m["pe"] = round(price / ttm, 1)
            ver["pe"] = ttm_ok

    # --- growth from the latest report
    if q:
        L = q[0]
        m["latest_report"] = {"end": L["end"], "title": L["title"], "url": L["url"]}
        pg, rg = L.get("profit_growth"), L.get("revenue_growth")
        if pg is not None and "growth" not in L["_bad"] and not ({"profit", "profit_prior"} & L["_bad"]):
            m["profit_growth"] = pg
            ver["profit_growth"] = "growth" in L["_ok"] or (_v(L, "profit") and _v(L, "profit_prior"))
        if rg is not None and not ({"revenue", "revenue_prior"} & L["_bad"]):
            m["revenue_growth"] = rg
            ver["revenue_growth"] = _v(L, "revenue_prior") and abs(rg) < 150
        good = [x.get("profit_growth") for x in q if x.get("profit_growth") is not None
                and ("growth" in x["_ok"] or (_v(x, "profit") and _v(x, "profit_prior")))]
        if len(good) >= 4:
            m["profit_growth_hit"] = f"{sum(g > 0 for g in good)}/{len(good)}"
            m["_pg_ratio"] = sum(g > 0 for g in good) / len(good)
            ver["profit_growth_hit"] = True

    # --- net assets per share
    nav_src = next((x for x in q if x.get("navps") and "navps" not in x["_bad"]), None) or \
        next((x for x in a if x.get("navps") and "navps" not in x["_bad"] and _ok_per_share(x["navps"], price, 0.02, 50)), None)
    if nav_src:
        nav = nav_src["navps"]
        m["navps"] = round(nav, 2)
        ver["navps"] = _v(nav_src, "navps")
        if price and nav > 0:
            m["pbv"] = round(price / nav, 2)
            ver["pbv"] = ver["navps"]
        if ttm is not None and nav > 0:
            m["roe"] = round(ttm / nav * 100, 1)
            ver["roe"] = ver["navps"] and ver.get("eps_ttm", False) and -60 <= m["roe"] <= 80

    # --- annual history (EPS, dividends)
    years = []
    for x in a:
        row = {"end": x["end"], "url": x["url"], "title": x.get("title")}
        if x["end"] in fy_eps:
            row["eps"], row["eps_ok"] = round(fy_eps[x["end"]][0], 2), fy_eps[x["end"]][1]
        if x.get("dps") is not None and "dps" not in x["_bad"] and price and 0 < x["dps"] <= 0.5 * price:
            e = row.get("eps")
            if e is None or e <= 0 or x["dps"] <= 1.5 * e:
                row["dps"], row["dps_ok"] = round(x["dps"], 2), _v(x, "dps")
            else:
                checks.append({"level": "fail", "end": x["end"], "msg": f"dividend/share {x['dps']} > 1.5 x EPS {e} - ignored"})
        for f in ("eps", "dps"):
            if x.get("src_" + f):
                row["src_" + f] = x["src_" + f]
        years.append(row)
    m["annual"] = years
    dsrc = next((y for y in years if "dps" in y), None)
    if dsrc and price:
        m["dps"], m["div_yield"] = dsrc["dps"], round(dsrc["dps"] / price * 100, 1)
        ver["div_yield"] = dsrc.get("dps_ok", False)
    e = [(y["eps"], y.get("eps_ok")) for y in years if "eps" in y]
    if len(e) >= 3 and e[-1][0] > 0 and e[0][0] > 0:
        n = len(e) - 1
        m["eps_cagr"] = round(((e[0][0] / e[-1][0]) ** (1 / n) - 1) * 100, 1)
        m["eps_cagr_years"] = n
        ver["eps_cagr"] = bool(e[0][1] and e[-1][1])

    m["quarters"] = [{"end": x["end"], "months": x.get("months"), "url": x["url"], "title": x.get("title"),
                      "eps": x.get("eps") if "eps" not in x["_bad"] else None, "eps_ok": _v(x, "eps"),
                      "profit_growth": x.get("profit_growth") if "growth" not in x["_bad"] else None,
                      "growth_ok": "growth" in x["_ok"] or (_v(x, "profit") and _v(x, "profit_prior")),
                      "revenue_growth": x.get("revenue_growth"),
                      "src": {f: x["src_" + f] for f in ("eps", "profit", "revenue", "navps") if x.get("src_" + f)}}
                     for x in q]
    m["verified"] = ver
    m["checks"] = checks
    n_ok = sum(1 for c in checks if c["level"] == "ok")
    n_bad = sum(1 for c in checks if c["level"] == "fail")
    m["check_summary"] = {"ok": n_ok, "warn": sum(1 for c in checks if c["level"] == "warn"), "fail": n_bad}
    nv = sum(ver.values())
    m["confidence"] = "good" if nv >= 4 else "partial" if nv >= 2 else "low"
    return m


def score(m: dict | None) -> tuple[float, list[str]]:
    """Fundamental points, same style as the technical rules (each line is a reason)."""
    if not m:
        return 0.0, []
    s, why = 0.0, []
    V = m.get("verified", {})
    ok = lambda k: V.get(k, False)  # unverified figures are shown on the page but never scored

    def add(p, t):
        nonlocal s
        s += p
        why.append(f"{'+' if p > 0 else ''}{p:g}  {t}")

    ttm = m.get("eps_ttm") if ok("eps_ttm") else None
    if ttm is not None and ttm <= 0:
        add(-1, f"Loss-making over the last 12 months (EPS {ttm})")
    elif "pe" in m and ok("pe"):
        pe = m["pe"]
        if pe < 8: add(1, f"Cheap: P/E {pe}")
        elif pe < 15: add(0.5, f"Reasonable P/E {pe}")
        elif pe > 25: add(-0.5, f"Expensive: P/E {pe}")
    if "pbv" in m and ok("pbv"):
        if m["pbv"] < 0.8: add(0.5, f"Trades below book value (P/BV {m['pbv']})")
        elif m["pbv"] > 3: add(-0.5, f"High P/BV {m['pbv']}")
    g = m.get("profit_growth") if ok("profit_growth") else None
    if g is not None:
        if g > 15: add(1, f"Profit up {g}% vs same period last year")
        elif g > 0: add(0.5, f"Profit up {g}% vs last year")
        elif g < -15: add(-1, f"Profit down {abs(g)}% vs last year")
        else: add(-0.5, f"Profit down {abs(g)}% vs last year")
    r = m.get("revenue_growth") if ok("revenue_growth") else None
    if r is not None:
        if r > 10: add(0.5, f"Revenue up {r}%")
        elif r < -10: add(-0.5, f"Revenue down {abs(r)}%")
    if m.get("_pg_ratio") is not None:
        if m["_pg_ratio"] >= 0.75: add(0.5, f"Consistent profit growth ({m['profit_growth_hit']} reports)")
        elif m["_pg_ratio"] <= 0.25: add(-0.5, f"Profits mostly falling ({m['profit_growth_hit']} reports grew)")
    if "roe" in m and ok("roe"):
        if m["roe"] >= 15: add(0.5, f"Strong ROE {m['roe']}%")
        elif m["roe"] < 5: add(-0.5, f"Weak ROE {m['roe']}%")
    if ok("div_yield") and m.get("div_yield", 0) >= 5:
        add(0.5, f"Dividend yield {m['div_yield']}%")
    if "eps_cagr" in m and ok("eps_cagr"):
        c = m["eps_cagr"]
        if c >= 10: add(0.5, f"EPS grew {c}%/yr over {m['eps_cagr_years']} years")
        elif c < 0: add(-0.5, f"EPS shrank {abs(c)}%/yr over {m['eps_cagr_years']} years")
    return s, why


if __name__ == "__main__":
    syms = [a.upper() for a in sys.argv[1:]] or db.watchlist()
    collect(syms, float(os.environ.get("FUND_BUDGET_MIN", "330")))
