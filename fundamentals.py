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
                    store[it["id"]] = {**res, "end": it["end"], "title": it["title"], "url": it["url"],
                                       "year_only": it.get("year_only", False)}
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


def resolve_columns(q: list, a: list, checks: list):
    """Decide which values on each row are 'current' and 'same period last year'.

    Every row keeps all its numbers (vals_<field>). A layout is (offset k, step): current = vals[k],
    prior = vals[k+step]. Evidence, strongest first:
      * year-to-year: this report's prior equals last year's report's current under the same layout
        (companies keep one layout) -> both figures VERIFIED;
      * the printed % change column matches cur/prior (self-check, from the parser);
      * otherwise the layout most of the company's other reports resolved to.
    """
    import collections
    layouts = [(k, st) for k in (0, 1, 2) for st in (1, 2)]
    for group, fields in ((q, ("eps", "profit", "revenue")), (a, ("eps", "dps"))):
        for f in fields:
            vk = "vals_" + f
            ev = collections.defaultdict(collections.Counter)
            for x in group:
                if not x.get(vk):
                    continue
                y = _year_before(x["end"], group, None)
                if not y or not y.get(vk):
                    continue
                for k, st in layouts:
                    xv, yv = x[vk], y[vk]
                    if len(xv) > k + st and len(yv) > k + st and (
                            _close(xv[k + st], yv[k], 0.02) or (abs(yv[k]) < 1 and abs(xv[k + st] - yv[k]) < 0.011)):
                        ev[id(x)][(k, st)] += 1
                        ev[id(y)][(k, st, "as_prev")] += 1
                        break
            majority = collections.Counter()
            for x in group:
                for lay, n in ev.get(id(x), {}).items():
                    majority[lay[:2]] += n
            for x in group:
                v = x.get(vk)
                if not v:
                    continue
                e = ev.get(id(x), {})
                own = [lay for lay in e if len(lay) == 2]
                parsed = tuple((x.get("layout_" + f) or [0, 1, False])[:2])
                if own:
                    k, st = max(own, key=lambda lay: (e[lay], lay == parsed, -lay[0], -lay[1]))
                    x[f], x[f + "_prior"] = v[k], v[k + st]
                    x["_ok"].update({f, f + "_prior"})
                    if (k, st) != parsed:
                        checks.append(("ok", x["end"], f"{f}: columns fixed by matching last year's report "
                                                       f"(value {v[k]}, last year {v[k + st]})"))
                elif any(len(lay) == 3 for lay in e):
                    lay = next(lay for lay in e if len(lay) == 3)  # confirmed as the 'previous' report
                    x[f] = v[lay[0]]
                    if len(v) > lay[0] + lay[1]:
                        x[f + "_prior"] = v[lay[0] + lay[1]]
                    x["_ok"].add(f)
                elif (x.get("layout_" + f) or [0, 1, False])[2]:
                    x["_self"].add(f)  # % change column confirms the cur/prior pairing
                elif majority:
                    k, st = majority.most_common(1)[0][0]
                    if len(v) > k + st:
                        x[f], x[f + "_prior"] = v[k], v[k + st]
        if group is q:
            for x in group:
                x["profit_growth"] = reports._growth((x.get("profit"), x.get("profit_prior")))
                x["revenue_growth"] = reports._growth((x.get("revenue"), x.get("revenue_prior")))


def infer_periods(q: list, a: list, cum, checks: list):
    """Work out, for each interim report, which EPS column is the quarter and which is year-to-date.

    Reports print either [year-to-date | last year] first or [quarter | last year] first, and headers are
    unreliable once converted to text. Arithmetic is not:  year-to-date = previous report's year-to-date + quarter.
    For the year-end report the 12-month figure must also equal the annual report's EPS.
    Sets x['months'] (period of the first column), x['eps_q'], x['eps_cum'], x['eps_cum_prior'], and
    marks 'period' verified when the arithmetic confirms it.
    """
    import datetime as dt
    tol = lambda a_, b_: abs(a_ - b_) <= 0.011 + 0.002 * max(abs(a_), abs(b_))  # rounding of printed figures only
    by_end = {x["end"]: x for x in q if x.get("end")}
    ann = {x["end"]: x for x in a if x.get("end")}
    for x in sorted(q, key=lambda r: r["end"] or ""):
        c = cum(x["end"]) if x.get("end") else None
        v = x.get("vals_eps")
        if not v or x.get("eps") is None or c is None:
            continue
        try:
            k0 = v.index(x["eps"])
        except ValueError:
            continue
        lay = x.get("layout_eps") or [0, 1, False]
        st = lay[1] if len(lay) > 1 else 1
        prior_of = lambda j: v[j + st] if len(v) > j + st else None
        if c == 3:
            x.update(months=3, eps_q=x["eps"], eps_cum=x["eps"], eps_cum_prior=x.get("eps_prior"))
            continue
        # previous report in the same financial year (3 months earlier)
        d0 = dt.date.fromisoformat(x["end"])
        p = next((r for e, r in by_end.items() if 70 <= (d0 - dt.date.fromisoformat(e)).days <= 110), None)
        p_cums = []
        if p is not None and cum(p["end"]) == c - 3:
            p_cums = [p["eps_cum"]] if p.get("eps_cum") is not None else list(p.get("vals_eps") or [])[:4]
        others = [j for j in range(len(v)) if j != k0 and j != k0 + st]
        found = None
        for pc in p_cums:
            for j in others:
                if tol(v[k0], pc + v[j]):          # first column is year-to-date
                    found = ("cum_first", j); break
                if tol(v[j], pc + v[k0]):          # first column is the quarter
                    found = ("q_first", j); break
            if found:
                break
        if not found and c == 12 and x["end"] in ann and ann[x["end"]].get("eps") is not None:
            ae = ann[x["end"]]["eps"]
            if tol(v[k0], ae):
                found = ("cum_first", None)
            else:
                j = next((j for j in others if tol(v[j], ae)), None)
                if j is not None:
                    found = ("q_first", j)
        if found:
            kind, j = found
            if kind == "cum_first":
                x.update(months=c, eps_cum=x["eps"], eps_cum_prior=x.get("eps_prior"), eps_q=v[j] if j is not None else None)
            else:
                x.update(months=3, eps_q=x["eps"], eps_cum=v[j], eps_cum_prior=prior_of(j))
            x["_ok"].add("period")
            checks.append(("ok", x["end"], f"columns: {'year-to-date' if kind == 'cum_first' else 'quarter'} first "
                                           f"({c}-month EPS {x['eps_cum']}) confirmed by adding up the quarters"))
        elif x.get("months") == c:
            x.update(eps_cum=x["eps"], eps_cum_prior=x.get("eps_prior"))
        elif x.get("months") == 3:
            x["eps_q"] = x["eps"]
        else:
            checks.append(("warn", x["end"], "could not tell whether the first EPS column is the quarter or year-to-date"))


def audit(d: dict, price: float | None):  # noqa: C901
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
    import calendar, collections
    q = [dict(x) for x in d.get("quarterly", {}).values()]
    a = [dict(x) for x in d.get("annual", {}).values()]
    checks = []

    def chk(level, x, msg):
        checks.append((level, x["end"], msg))

    # financial year end month: majority of annual reports whose title gives a real date
    months = collections.Counter(int(x["end"][5:7]) for x in a if x.get("end") and not x.get("year_only"))
    fy_month = months.most_common(1)[0][0] if months else (12 if a else None)
    # "Annual Report 2025" for a March-year-end company means the year ending March 2025
    for x in a:
        if x.get("year_only") and fy_month and x.get("end") and int(x["end"][5:7]) != fy_month:
            y = int(x["end"][:4])
            x["end"] = f"{y}-{fy_month:02d}-{calendar.monthrange(y, fy_month)[1]:02d}"
    q.sort(key=lambda x: x["end"] or "", reverse=True)
    a.sort(key=lambda x: x["end"] or "", reverse=True)
    cum = lambda end: ((int(end[5:7]) - fy_month) % 12 or 12) if fy_month and end else None

    for x in q + a:
        x["_ok"], x["_bad"], x["_self"] = set(), set(), set()
    resolve_columns(q, a, checks)
    infer_periods(q, a, cum, checks)
    for x in q + a:
        for f in ("eps", "eps_prior", "eps_cum", "eps_cum_prior", "navps", "dps", "dps_prior"):
            if f.startswith("dps") and x.get(f) == 0:
                continue  # no dividend is a valid figure
            if x.get(f) is not None and not _ok_per_share(x[f], price, lo=0.0001, hi=20 if "nav" in f else 5):
                x["_bad"].add(f)
                checks.append(("fail", x["end"], f"{f} {x[f]} implausible vs price {price} - ignored"))

    # 1. EPS vs profit / shares  (profit row and EPS row may print quarter / year-to-date in different order,
    #    so compare against both EPS figures)
    shares = d.get("shares") or 0
    both = shares + (d.get("shares_other_class") or 0)
    for x in q[:4]:
        if not (x.get("profit") and shares):
            continue
        targets = [(f, x.get(f)) for f in ("eps", "eps_cum", "eps_q") if x.get(f) and f not in x["_bad"]]
        units = [x["unit"]] if x.get("unit") else [1, 1e3, 1e6]
        hit = next(((f, e, u, S) for f, e in targets for u in units for S in (shares, both)
                    if S and _close(x["profit"] * u / S, e, 0.15)), None)
        if hit:
            f, e, u, S = hit
            x["_ok"].update({f, "profit"})
            chk("ok", x, f"EPS {e} = profit {x['profit']:,.0f} x {u:g} / {S:,.0f} shares")
        elif targets:
            implied = x["profit"] * (x.get("unit") or 1) / shares
            chk("warn", x, f"EPS {targets[0][1]} does not match profit / shares ({implied:.2f}) - unit, share count or row may be wrong")

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

    # 3. annual EPS vs the 12-month figure in the year-end interim report
    for x in a:
        r = next((y for y in q if y["end"] == x["end"] and y.get("eps_cum") is not None and cum(y["end"]) == 12), None)
        if r and x.get("eps") is not None and "eps" not in x["_bad"]:
            if _close(x["eps"], r["eps_cum"], 0.03):
                x["_ok"].add("eps"); r["_ok"].add("eps_cum")
                chk("ok", x, f"annual EPS {x['eps']} = 12-month EPS in the year-end interim report {r['eps_cum']}")
            else:
                x["_conflict"] = r["eps_cum"]
                chk("warn", x, f"annual EPS {x['eps']} ≠ 12-month interim EPS {r['eps_cum']} (group vs company, or mis-read)")

    # 3b. year-to-date EPS: last-year column = last year's report
    for x in q:
        y = _year_before(x["end"], q, None)
        if y and x.get("eps_cum_prior") is not None and y.get("eps_cum") is not None:
            if _close(x["eps_cum_prior"], y["eps_cum"], 0.02) or abs(x["eps_cum_prior"] - y["eps_cum"]) < 0.011:
                x["_ok"].update({"eps_cum", "eps_cum_prior"}); y["_ok"].add("eps_cum")
            elif not _close(x["eps_cum_prior"], y["eps_cum"], 0.10):
                x["_bad"].add("eps_cum_prior")
                chk("warn", x, f"year-to-date EPS last-year column {x['eps_cum_prior']} ≠ {y['end']} report {y['eps_cum']}")

    # 4. EPS growth vs profit growth
    for x in q:
        e, ep, p, pp = x.get("eps"), x.get("eps_prior"), x.get("profit"), x.get("profit_prior")
        if None in (e, ep, p, pp) or ep <= 0 or pp <= 0:
            continue
        ge, gp = e / ep - 1, p / pp - 1
        if abs(ge - gp) <= 0.10 + 0.10 * abs(gp):
            x["_ok"].add("growth")
            chk("ok", x, f"EPS growth {ge:+.0%} consistent with profit growth {gp:+.0%}")
        elif {"profit", "profit_prior"} <= x["_ok"]:
            chk("warn", x, f"EPS growth {ge:+.0%} vs profit growth {gp:+.0%} differ (share count changed?) - profit figures verified, kept")
        else:
            x["_bad"].add("growth")
            chk("warn", x, f"EPS growth {ge:+.0%} vs profit growth {gp:+.0%} disagree - growth not used")

    # 5. net assets per share: the balance sheet's comparative column is the last financial year end,
    #    so it must equal the NAV reported at that year end (interim Q4 or annual report)
    for x in q:
        v = x.get("vals_navps")
        if not v or "navps" in x["_bad"]:
            continue
        x["navps"] = v[0]
        if fy_month and len(v) > 1:
            y, mth = int(x["end"][:4]), int(x["end"][5:7])
            fy_end_year = y if mth > fy_month else y - 1
            fe = f"{fy_end_year}-{fy_month:02d}"
            ref = next((r for r in q + a if r is not x and (r.get("end") or "").startswith(fe) and r.get("vals_navps")), None)
            if ref:
                if _close(v[1], ref["vals_navps"][0], 0.02):
                    x["_ok"].add("navps"); ref["_ok"].add("navps")
                    chk("ok", x, f"net assets/share comparative {v[1]} = year-end {ref['end']} figure {ref['vals_navps'][0]}")
                elif _close(v[1], ref["vals_navps"][0], 0.15) is False and not any(_close(v[1], w, 0.02) for w in ref["vals_navps"][:4]):
                    x["_bad"].add("navps")
                    chk("warn", x, f"net assets/share comparative {v[1]} ≠ year-end {ref['end']} figure {ref['vals_navps'][0]} - not used")
    navs = [x for x in q if x.get("navps") and "navps" not in x["_bad"]]
    for n, o in zip(navs, navs[1:]):
        if not _close(n["navps"], o["navps"], 0.30):
            n["_bad"].add("navps"); n["_ok"].discard("navps")
            chk("warn", n, f"net assets/share {n['navps']} jumped from {o['navps']} ({o['end']}) - not used")
    for x in a:
        if x.get("vals_navps"):
            x["navps"] = x["vals_navps"][0]
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

    # --- full-year EPS per year end: annual report and the 12-month column of the year-end interim report.
    #     Verified if a cross-check confirmed it or both sources agree; conflicting sources are not verified.
    fy = {}
    def add_fy(end, v, ok, src):
        if end and v is not None and _ok_per_share(v, price, lo=0.0001):
            fy.setdefault(end, []).append((v, ok, src))
    for x in a:
        if "eps" not in x["_bad"]:
            add_fy(x["end"], x.get("eps"), _v(x, "eps"), "annual")
    for x in q:
        if cum(x["end"]) == 12 and x.get("eps_cum") is not None and "eps_cum" not in x["_bad"]:
            add_fy(x["end"], x["eps_cum"], "eps_cum" in x["_ok"] or ("period" in x["_ok"] and _v(x, "eps")), "interim")
    fy_eps = {}
    for end, lst in fy.items():
        agree_n = lambda t: sum(_close(t[0], w[0], 0.03) for w in lst)
        v, ok, src = max(lst, key=lambda t: (t[1], agree_n(t), t[2] == "annual"))
        conflict = any(not _close(v, w[0], 0.03) and w[1] for w in lst)
        fy_eps[end] = (v, (ok or agree_n((v,)) >= 2) and not conflict)
    m["fy_eps"] = [{"end": e, "eps": round(v, 3), "verified": ok} for e, (v, ok) in sorted(fy_eps.items(), reverse=True)][:6]

    # --- trailing 12-month EPS = year-to-date + last full year − same period last year
    ttm, how, ttm_ok = None, None, False
    if q:
        L = q[0]
        c = cum(L["end"])
        prior_fy = max((e for e in fy_eps if e < L["end"]), default=None)
        if c == 12 and L.get("eps_cum") is not None and "eps_cum" not in L["_bad"]:
            ttm, how = L["eps_cum"], f"12 months to {L['end']}"
            ttm_ok = "eps_cum" in L["_ok"] or ("period" in L["_ok"] and _v(L, "eps"))
        elif c and L.get("eps_cum") is not None and L.get("eps_cum_prior") is not None and prior_fy \
                and not ({"eps_cum", "eps_cum_prior"} & L["_bad"]):
            ttm = L["eps_cum"] + fy_eps[prior_fy][0] - L["eps_cum_prior"]
            how = f"{c} months to {L['end']} + full year to {prior_fy} − same {c} months last year"
            ttm_ok = (("eps_cum" in L["_ok"] and "eps_cum_prior" in L["_ok"]) or
                      (c == 3 and _v(L, "eps") and _v(L, "eps_prior")) or
                      ("period" in L["_ok"] and _v(L, "eps") and _v(L, "eps_prior"))) and fy_eps[prior_fy][1]
        else:
            qs = [x for x in q[:4] if x.get("eps_q") is not None]
            if len(qs) == 4 and _consecutive([x["end"] for x in qs]):
                ttm, how = sum(x["eps_q"] for x in qs), f"sum of the last 4 quarters to {L['end']}"
                ttm_ok = all(("period" in x["_ok"] or cum(x["end"]) == 3) and _v(x, "eps") for x in qs)
    last_fy = max(fy_eps) if fy_eps else None
    if ttm is not None and last_fy and fy_eps[last_fy][0] > 0 and not 0.33 <= ttm / fy_eps[last_fy][0] <= 3:
        checks.append({"level": "fail", "end": q[0]["end"], "msg": f"12-month EPS {ttm:.2f} far from last full year {fy_eps[last_fy][0]} - not used"})
        ttm = None
    if ttm is None and last_fy:
        import datetime as dt
        newest = q[0]["end"] if q else last_fy
        age = (dt.date.fromisoformat(newest) - dt.date.fromisoformat(last_fy)).days
        if age <= 400:
            ttm, how, ttm_ok = fy_eps[last_fy][0], f"full year to {last_fy}", fy_eps[last_fy][1]
        else:
            checks.append({"level": "warn", "end": newest, "msg": f"latest full-year EPS found is for {last_fy} - too old for P/E, not used"})
    if ttm is not None:
        m["eps_ttm"], m["eps_basis"] = round(ttm, 3), how
        ver["eps_ttm"] = bool(ttm_ok)
        if price and ttm > 0:
            m["pe"] = round(price / ttm, 1)
            ver["pe"] = bool(ttm_ok)

    # --- growth from the latest report
    def ytd_growth(x):
        c_, p_ = x.get("eps_cum"), x.get("eps_cum_prior")
        if c_ is None or p_ is None or p_ <= 0 or {"eps_cum", "eps_cum_prior"} & x["_bad"]:
            return None, False
        ok_ = ({"eps_cum", "eps_cum_prior"} <= x["_ok"]) or (("period" in x["_ok"] or cum(x["end"]) == 3)
                                                          and _v(x, "eps") and _v(x, "eps_prior"))
        g_ = round((c_ / p_ - 1) * 100, 1)
        return (g_ if -95 <= g_ <= 1000 else None), ok_
    if q:
        g, gok = ytd_growth(q[0])
        if g is not None:
            m["eps_growth_ytd"], m["eps_growth_months"] = g, cum(q[0]["end"])
            ver["eps_growth_ytd"] = gok
        hits = [ytd_growth(x) for x in q]
        hits = [g_ for g_, ok_ in hits if g_ is not None and ok_]
        if len(hits) >= 4:
            m["eps_growth_hit"] = f"{sum(g_ > 0 for g_ in hits)}/{len(hits)}"
            m["_eg_ratio"] = sum(g_ > 0 for g_ in hits) / len(hits)
    if q:
        L = q[0]
        m["latest_report"] = {"end": L["end"], "title": L["title"], "url": L["url"]}
        pg, rg = L.get("profit_growth"), L.get("revenue_growth")
        if pg is not None and "growth" not in L["_bad"] and not ({"profit", "profit_prior"} & L["_bad"]):
            m["profit_growth"] = pg
            ver["profit_growth"] = "growth" in L["_ok"] or (_v(L, "profit") and _v(L, "profit_prior")) \
                or ("profit" in L["_self"] and _v(L, "eps"))
        if rg is not None and not ({"revenue", "revenue_prior"} & L["_bad"]):
            m["revenue_growth"] = rg
            ver["revenue_growth"] = (_v(L, "revenue_prior") or "revenue" in L["_self"]) and abs(rg) < 150
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
        if x.get("dps") is not None and "dps" not in x["_bad"] and price and 0 <= x["dps"] <= 0.5 * price:
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
    # EPS growth per year only along an unbroken chain: each year's "last year" column must equal the
    # previous annual report's figure (a share split or restatement breaks the chain - no false trend)
    chain = []
    x = next((r for r in a if r.get("eps") is not None and "eps" not in r["_bad"]), None)
    while x is not None:
        chain.append(x["eps"])
        y = _year_before(x["end"], a, None)
        if not (y and y.get("eps") is not None and x.get("eps_prior") is not None
                and (_close(x["eps_prior"], y["eps"], 0.03) or abs(x["eps_prior"] - y["eps"]) < 0.011)):
            break
        x = y
    if len(chain) >= 3 and chain[0] > 0 and chain[-1] > 0:
        n = len(chain) - 1
        m["eps_cagr"] = round(((chain[0] / chain[-1]) ** (1 / n) - 1) * 100, 1)
        m["eps_cagr_years"] = n
        ver["eps_cagr"] = True  # every link in the chain is itself a passed cross-check
    m["quarters"] = [{"end": x["end"], "months": x.get("months"), "url": x["url"], "title": x.get("title"),
                      "eps": x.get("eps") if "eps" not in x["_bad"] else None, "eps_ok": _v(x, "eps"),
                      "eps_cum": x.get("eps_cum") if "eps_cum" not in x["_bad"] else None, "cum_months": cum(x["end"]),
                      "eps_q": x.get("eps_q"), "period_ok": "period" in x["_ok"] or cum(x["end"]) == 3,
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
    if ok("eps_growth_ytd"):
        g, what = m["eps_growth_ytd"], f"EPS {m.get('eps_growth_months')}-month year-to-date"
    elif ok("profit_growth"):
        g, what = m["profit_growth"], "Profit (latest period)"
    else:
        g = None
    if g is not None:
        if g > 15: add(1, f"{what} up {g}% vs last year")
        elif g > 0: add(0.5, f"{what} up {g}% vs last year")
        elif g < -15: add(-1, f"{what} down {abs(g)}% vs last year")
        else: add(-0.5, f"{what} down {abs(g)}% vs last year")
    r = m.get("revenue_growth") if ok("revenue_growth") else None
    if r is not None:
        if r > 10: add(0.5, f"Revenue up {r}%")
        elif r < -10: add(-0.5, f"Revenue down {abs(r)}%")
    ratio, hit = (m.get("_eg_ratio"), m.get("eps_growth_hit")) if m.get("_eg_ratio") is not None else (m.get("_pg_ratio"), m.get("profit_growth_hit"))
    if ratio is not None:
        if ratio >= 0.75: add(0.5, f"Consistent earnings growth ({hit} verified reports up)")
        elif ratio <= 0.25: add(-0.5, f"Earnings mostly falling ({hit} verified reports up)")
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
