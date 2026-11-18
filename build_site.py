"""Fetch CSE data and build the static website in site/ (what GitHub Pages serves).

    python build_site.py                      # snapshot today + backfill short watchlist histories + build
    python build_site.py --backfill JKH.N0000 COMB.N0000
    python build_site.py --backfill-all       # full history for every stock on the board (~10 min)
    python build_site.py --no-fetch           # rebuild the site from saved data only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pandas as pd
import math
import shutil
from pathlib import Path

import analysis
import collector
import cse_client as cse
import db
import fundamentals
import pressure
import sectors

SITE = Path(__file__).parent / "site"
OUT = SITE / "data"


def _clean(o):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 4)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


NO_FETCH = True


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), separators=(",", ":")))


def _latest_forecast() -> dict:
    files = sorted((Path(__file__).parent / "data" / "forecasts").glob("*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text())
    except ValueError:
        return {}


def _perf(c, n):
    return round(float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, 2) if len(c) > n and c.iloc[-1 - n] else None


def stock_payload(symbol: str, df, sector: str = "", forecast: dict | None = None) -> dict:
    fund = fundamentals.metrics(symbol, float(df["close"].iloc[-1])) if len(df) else None
    d = analysis.indicators(df)
    t = [ts.strftime("%Y-%m-%d") for ts in d.index]
    c = df["close"]
    fc = None
    if forecast and symbol in forecast.get("forecasts", {}):
        fc = {**forecast["forecasts"][symbol], "date": forecast["date"], "n": forecast["n"], "days": forecast["holding_days"]}

    def series(col):
        return [{"time": ti, "value": float(v)} for ti, v in zip(t, d[col]) if v == v]

    return {
        "symbol": symbol,
        "candles": [{"time": ti, "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for ti, r in zip(t, d.itertuples())],
        "volume": [{"time": ti, "value": r.volume} for ti, r in zip(t, d.itertuples())],
        "sma20": series("sma20"), "sma50": series("sma50"),
        "rsi": series("rsi"), "macd_hist": series("macd_hist"),
        "advice": (lambda a: {**a, "style": analysis.classify(a)} if a else a)(analysis.advice(df, fund)),
        "backtest": analysis.backtest(df),
        "sector": sector,
        "perf": {"d1": _perf(c, 1), "d5": _perf(c, 5), "d20": _perf(c, 20), "d60": _perf(c, 60)},
        "atr_pct": round(float(d["atr"].iloc[-1] / c.iloc[-1] * 100), 2) if len(d) and d["atr"].iloc[-1] == d["atr"].iloc[-1] else None,
        "forecast": fc,
        "pressure": pressure.read(df),
        "splits": db.splits(symbol),
    }


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    names, watch = db.security_names(), set(db.watchlist())
    sec, forecast = sectors.load(), _latest_forecast()
    signals, funds, payloads = [], {}, {}
    for s in db.symbols_with_data(30):
        df = db.load_bars(s)
        p = stock_payload(s, df, sec.get(s, ""), forecast)
        if not p["advice"]:
            continue
        payloads[s] = p
        bt = p["backtest"] or {}
        a = {k: v for k, v in p["advice"].items() if k not in ("fundamentals", "fund_reasons", "reasons", "style", "fib", "ind")}
        f = p["advice"].get("fundamentals") or {}
        funds[s] = f
        signals.append({"symbol": s, "name": names.get(s, ""), "watch": s in watch, "bars": len(df),
                        "bt_edge": None if not bt else round(bt["strategy_return_pct"] - bt["buy_hold_return_pct"], 1),
                        "pe": f.get("pe") if (f.get("verified") or {}).get("pe") else None,
                        "pbv": f.get("pbv"), "div_yield": f.get("div_yield") if (f.get("verified") or {}).get("div_yield") else None,
                        "data_ok": (f.get("check_summary") or {}).get("ok", 0),
                        "data_warn": (f.get("check_summary") or {}).get("warn", 0),
                        "data_fail": (f.get("check_summary") or {}).get("fail", 0),
                        "verified_n": sum((f.get("verified") or {}).values()),
                        "trade": p["advice"]["style"]["trade"], "invest": p["advice"]["style"]["invest"],
                        "trade_watch": p["advice"]["style"].get("trade_watch", False),
                        "rr": p["advice"]["style"].get("reward_risk"),
                        "sector": p["sector"], "d20": p["perf"]["d20"], "d60": p["perf"]["d60"],
                        "roe": f.get("roe") if (f.get("verified") or {}).get("roe") else None,
                        "pbv_ok": bool((f.get("verified") or {}).get("pbv")),
                        "tov": (p["advice"].get("ind") or {}).get("turnover_avg20"),
                        "fc_rank": (p["forecast"] or {}).get("rank"),
                        "pr": (p["pressure"] or {}).get("label") or None,
                        "fields_n": len(f.get("verified") or {}), **a})
    # The rating. ratings.py places each share on five price and volume measures (tested since 2010) and on the
    # figures in its latest published report (tested since 2024). Shares that trade too little are not rated.
    rstudy = None
    try:
        import ratings
        bad = {s: {c["end"] for c in (f.get("checks") or []) if c.get("level") == "fail"} for s, f in funds.items()}
        RT, rstudy = ratings.build(sec, bad=bad)
        for x in signals:
            a, r = payloads[x["symbol"]]["advice"], RT.get(x["symbol"]) or {"rated": False, "why": "it has not traded recently"}
            a["rule_signal"], a["rule_score"] = a["signal"], a["score"]          # the earlier chart rules: kept as notes and for the trading-setup test
            if r["rated"]:
                g = r["grade"]
                a["signal"], a["score"], a["rating"] = g, r["pct"], r
                x.update(signal=g, score=r["pct"], pg=r["price_grade"], of=r["of"])
                st = a["style"]
                if "SELL" in r["grade"] and (st["trade"] or st.get("trade_watch")):
                    st["trade"], st["trade_watch"] = False, False
                    st["trade_missing"].append("price rating is Sell: shares rated Sell have lagged the market on average")
                    x.update(trade=False, trade_watch=False)
            else:
                a["signal"], a["score"], a["rating"] = "NOT RATED", None, r
                x.update(signal="NOT RATED", score=None, pg=None, why=r["why"])
        print(f"Ratings: {sum(1 for r in RT.values() if r['rated'])} of {len(signals)} shares rated; "
              + ", ".join(f"{g.lower()} {sum(1 for x in signals if x['signal'] == g)}" for g in ratings.ORDER[::-1]))
    except Exception as e:                  # the site must still build: the earlier rule signal stays in place
        import traceback
        traceback.print_exc()
        print("ratings step failed:", repr(e))
    # what changed since the last daily build (for the closing Telegram summary). Only the daily build moves the
    # saved copy on, so a re-publish during the day does not wipe out the day's changes.
    try:
        lastf = Path(__file__).parent / "data" / "model" / "last_ratings.json"
        prev = json.loads(lastf.read_text()) if lastf.exists() else {}
        day, now = (signals[0]["date"] if signals else None), {x["symbol"]: x["signal"] for x in signals}
        if prev.get("date") == day:                      # already moved on today: keep the day's list
            rec = prev
        else:
            old = prev.get("ratings", {})
            rec = {"date": day, "since": prev.get("date"), "ratings": now,
                   "changes": [{"s": k, "from": old[k], "to": g} for k, g in now.items() if old.get(k) and old[k] != g]}
            if not NO_FETCH or not prev:
                lastf.write_text(json.dumps(rec))
        _dump(OUT / "changes.json", {k: rec.get(k) for k in ("date", "since", "changes")})
    except Exception as e:
        print("rating changes step failed:", repr(e))
    try:                                    # price outlook: likely range over the next 5/10/20/60 trading days
        import outlook
        badf = {s: {c["end"] for c in (f.get("checks") or []) if c.get("level") == "fail"} for s, f in funds.items()}
        OL = outlook.build(sec, names, bad=badf)
        for s, p in payloads.items():
            if s in OL["shares"]:
                p["outlook"] = {"date": OL["date"], "h": OL["shares"][s]}
        _dump(OUT / "outlook.json", {k: v for k, v in OL.items() if k != "shares"})
        print(f"Price outlook: {len(OL['shares'])} shares")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("outlook step failed:", repr(e))
    try:                                    # best price to buy, technically (tested rule, see buyzone.py)
        import buyzone
        import db as _db
        bzs = json.loads((Path(__file__).parent / "data" / "model" / "buyzone.json").read_text())
        for s, p in payloads.items():
            d_ = _db.load_bars(s)
            if d_ is None or len(d_) < 60:
                continue
            got = buyzone.build({s: d_}, [s]).get(s)
            if got:
                p["buyzone"] = got
        _dump(OUT / "buyzone.json", bzs)
        print(f"Best buy price: {sum(1 for p in payloads.values() if 'buyzone' in p)} shares")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("buy price step failed:", repr(e))
    try:                                    # world markets banner (the Cloudflare site refreshes this itself)
        import world
        W = world.build()
        if W["items"]:
            _dump(OUT / "world.json", W)
        print(f"World markets: {len(W['items'])} of {len(world.symbols())}")
    except Exception as e:
        print("world step failed:", repr(e))
    for s, p in payloads.items():
        _dump(OUT / "stocks" / f"{s}.json", p)
    signals.sort(key=lambda x: (not x["watch"], -(x["score"] if x["score"] is not None else -1)))
    _dump(OUT / "signals.json", signals)
    try:                                    # interest rates and the rupee, for the Market page and Telegram
        import macro
        mx = macro.build(signals)
        if mx:
            _dump(OUT / "macro.json", mx)
    except Exception as e:
        print("rates step failed:", repr(e))
    try:                                    # day trading watch and its test
        import daytrade
        dt_ = daytrade.build(names)
        rated = {x["symbol"]: x for x in signals}
        for x in dt_["list"]:
            r = rated.get(x["s"]) or {}
            x["signal"], x["sector"] = r.get("signal"), r.get("sector")
        _dump(OUT / "daytrade.json", dt_)
        print(f"Day trading watch: {dt_['n']} shares qualify")
    except Exception as e:
        print("day trading step failed:", repr(e))

    # peers: every industry group with its medians and members (most traded first)
    def med(vals):
        v = sorted(x for x in vals if x is not None)
        return None if not v else round(v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2, 2)
    groups = {}
    for x in signals:
        if x["sector"]:
            groups.setdefault(x["sector"], []).append(x)
    peers = {}
    for name, ms in groups.items():
        ms.sort(key=lambda x: -(x["tov"] or 0))
        peers[name] = {"n": len(ms),
                       "median": {"pe": med(x["pe"] for x in ms), "pbv": med(x["pbv"] if x["pbv_ok"] else None for x in ms),
                                  "roe": med(x["roe"] for x in ms), "div_yield": med(x["div_yield"] for x in ms),
                                  "d20": med(x["d20"] for x in ms), "d60": med(x["d60"] for x in ms), "score": med(x["score"] for x in ms)},
                       "members": [{"s": x["symbol"], "name": x["name"], "close": x["close"], "chg": x["change_pct"], "d20": x["d20"],
                                    "d60": x["d60"], "pe": x["pe"], "pbv": x["pbv"] if x["pbv_ok"] else None, "roe": x["roe"],
                                    "signal": x["signal"], "score": x["score"], "invest": x["invest"], "tov": x["tov"]} for x in ms]}
    _dump(OUT / "peers.json", peers)

    # forecast accuracy (walk-forward test + live track record) for site/accuracy.html
    mdir = Path(__file__).parent / "data" / "model"
    acc = {}
    for key, fname in (("validation", "validation.json"), ("track", "track.json"), ("pressure", "pressure.json")):
        if (mdir / fname).exists():
            try:
                acc[key] = json.loads((mdir / fname).read_text())
            except ValueError:
                pass
    if forecast:
        top = sorted(forecast.get("forecasts", {}).items(), key=lambda kv: kv[1]["rank"])[:10]
        acc["today"] = {"date": forecast["date"], "n": forecast["n"],
                        "top": [{"symbol": k, "name": names.get(k, ""), **v} for k, v in top]}
    if rstudy:
        acc["ratings"] = {**rstudy, "groups": {g: {"title": t, "what": w} for g, (t, w) in ratings.WORDS.items()},
                          "rated_now": sum(1 for x in signals if x["score"] is not None), "shares_now": len(signals)}
    _dump(OUT / "accuracy.json", acc)

    # data-quality audit across all companies (feeds site/audit.html)
    import collections, re
    with_f = [x for x in signals if x["fields_n"]]
    field_ok, field_n, kinds = collections.Counter(), collections.Counter(), collections.Counter()
    companies = []
    for x in with_f:
        f = (json.loads((OUT / "stocks" / f"{x['symbol']}.json").read_text())["advice"].get("fundamentals") or {})
        for k, v in (f.get("verified") or {}).items():
            field_n[k] += 1; field_ok[k] += bool(v)
        for c in f.get("checks") or []:
            kind = re.sub(r"[-+\d.,()]+", " ", c["msg"].split(" - ")[0])
            kind = " ".join(w for w in kind.split()[:6] if not w.endswith("%"))
            kinds[(c["level"], kind)] += 1
        companies.append({"symbol": x["symbol"], "name": x["name"], "ok": x["data_ok"], "warn": x["data_warn"],
                          "fail": x["data_fail"], "verified": x["verified_n"], "fields": x["fields_n"],
                          "confidence": f.get("confidence"), "q": f.get("quarters_read"), "a": f.get("annuals_read"),
                          "ai": bool(f.get("ai_read"))})
    audit = {"companies_with_reports": len(with_f),
             "checks_passed": sum(x["data_ok"] for x in with_f), "warnings": sum(x["data_warn"] for x in with_f),
             "failures": sum(x["data_fail"] for x in with_f),
             "figures_verified": sum(x["verified_n"] for x in with_f), "figures_total": sum(x["fields_n"] for x in with_f),
             "fields": [{"field": k, "verified": field_ok[k], "total": field_n[k]} for k in sorted(field_n, key=lambda k: -field_n[k])],
             "kinds": [{"level": l, "kind": k, "count": n} for (l, k), n in kinds.most_common(60)],
             "companies": sorted(companies, key=lambda r: r["symbol"])}
    # share splits found in CSE's (unadjusted) prices and corrected for
    audit["splits"] = [{"symbol": s, "name": names.get(s, ""), **sp} for s in db.symbols_with_data(2) for sp in db.splits(s)]
    _dump(OUT / "audit.json", audit)
    if with_f:
        print(f"Audit: {audit['figures_verified']}/{audit['figures_total']} figures verified; "
              f"{audit['checks_passed']} checks passed, {audit['warnings']} warnings, {audit['failures']} failures")

    try:                                    # 10-year history per share and the 20-year market patterns
        import longterm
        mk = longterm.build(OUT)
        print(f"Long history: {mk['checks']['joined']} shares joined to older prices; market index from {mk['built_from']['start']}, "
              f"yearly gap to the official ASPI {mk['fit'].get('mean_gap')} points on average")
        # trends in every aspect, and the veterans' checklists (both read the joined long history)
        import trends
        import veterans
        T, tsum = trends.build(longterm.BARS, funds, sec, names, longterm.LEVEL)
        V, vsum = veterans.build(longterm.BARS, funds, sec, names, T, tsum["market"], longterm._shares())
        made = dt.datetime.now(collector.COLOMBO).strftime("%Y-%m-%d %H:%M")
        by_sym = {x["symbol"]: x for x in signals}
        shares = longterm._shares()
        for s, t in T.items():                  # what the list, the map and the search need about each share
            x = by_sym.get(s)
            if not x:
                continue
            x["tr"], x["trh"], x["rs"] = t["code"], t["headline"], t.get("rs")
            x["vet"] = "".join({"fits": "F", "partly": "P", "no": "N", "unknown": "U"}[l["verdict"]] for l in V.get(s, []))
            x["mcap"] = round(shares[s] * x["close"]) if shares.get(s) else None
            x["spark"] = [round(float(v), 4) for v in longterm.BARS[s]["close"].tail(30)]
        # every trading day on record, for the share page's full trade history (loaded only when asked for)
        nh = 0
        for s_, b in longterm.BARS.items():
            b = b.dropna(subset=["close"])
            b = b[b["volume"].fillna(0) > 0] if len(b) else b
            if not len(b):
                continue
            r2 = lambda col: [None if pd.isna(v) else round(float(v), 2) for v in b[col]]
            _dump(OUT / "hist" / f"{s_}.json", {"s": s_, "d": [x.strftime("%Y-%m-%d") for x in b.index], "c": r2("close"),
                                               "h": r2("high"), "l": r2("low"), "v": [int(v) for v in b["volume"].fillna(0)]})
            nh += 1
        print(f"Trade history files: {nh}")
        # the words: the home brief and a summary for each share
        import brief
        words = brief.build(signals, T, V, tsum, mk, longterm.official()[1])
        for s, t in T.items():
            x = by_sym.get(s)
            if x:
                _dump(OUT / "view" / f"{s}.json", {"symbol": s, "name": names.get(s, ""), "sector": sec.get(s, ""), "close": x["close"], "date": x["date"],
                                                    "signal": x["signal"], "summary": words["shares"].get(s, ""), "trend": trends.public(t), "lenses": V.get(s, [])})
        _dump(OUT / "brief.json", {**words["home"], "made": made})
        _dump(OUT / "signals.json", signals)
        _dump(OUT / "trends.json", {**tsum, "made": made})
        _dump(OUT / "veterans.json", {**vsum, "made": made})
        print(f"Trends: {tsum['counts']}; veterans: " + ", ".join(f"{k} {v['fits']}" for k, v in vsum["board"].items()))
    except Exception as e:                  # the site must still build without it
        import traceback
        traceback.print_exc()
        print("long history / trends step failed:", repr(e))

    meta = {"updated": dt.datetime.now(collector.COLOMBO).strftime("%Y-%m-%d %H:%M"), "stocks": len(signals),
            "tracked": len(db.symbols_with_data(1)), "watchlist": sorted(watch), "rules": analysis.RULES}
    _dump(OUT / "meta.json", meta)
    print(f"Built site: {len(signals)} stocks with signals ({meta['tracked']} tracked)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--backfill", nargs="*", default=[])
    ap.add_argument("--backfill-all", action="store_true")
    a = ap.parse_args()
    global NO_FETCH
    NO_FETCH = a.no_fetch

    if not a.no_fetch:
        collector.snapshot()
        requested = {s.strip().upper() for s in a.backfill if s.strip()}
        for s in requested:  # symbols you ask for are added to the watchlist
            db.set_watch(s, True)
        todo = set(requested)
        if a.backfill_all:
            todo |= {r["symbol"] for r in cse.trade_summary()}
        # watchlist stocks with short history get a full backfill (at most once a month each,
        # so thinly traded stocks aren't re-fetched every day)
        done = db.last_backfill()
        cutoff = (dt.datetime.now(collector.COLOMBO) - dt.timedelta(days=30)).strftime("%Y-%m-%d")
        todo |= {s for s in db.watchlist() if db.bar_count(s) < 150 and done.get(s, "") < cutoff}
        if todo:
            collector.backfill(sorted(todo))
        try:                                # retrain, re-test and forecast with the new day's bars
            import forecast
            X = forecast.build_dataset()
            v = forecast.validate(X)
            forecast.today(X)
            forecast.score()
            print(f"Forecast: walk-forward daily top 5 made a net gain {v['top']['5']['hit']}% of the time "
                  f"(any stock {v['all']['hit']}%), average net {v['top']['5']['avg_net']}%")
        except Exception as e:              # the site must still build if the model step fails
            print("forecast step failed:", repr(e))
        try:                                # CSE serves one year of ASPI: keep adding to our own file
            import longhist
            longhist.aspi()
        except Exception as e:
            print("ASPI history step failed:", repr(e))
        try:                                # what followed quiet buying / selling in the saved history
            pressure.study()
        except Exception as e:
            print("pressure study failed:", repr(e))
    build()


if __name__ == "__main__":
    main()
