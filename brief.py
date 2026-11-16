"""The words the app writes: the daily brief on the home screen and a short summary on each share's page.

Every sentence is assembled by fixed rules from the day's figures. No language model is involved, nothing is
predicted, and report figures are mentioned only when they passed the data checks. On the site these words are set
in a serif face so that commentary never looks like data.
"""
from __future__ import annotations

import datetime as dt

MONTH = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "May": "May", "Jun": "June", "Jul": "July",
         "Aug": "August", "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}
INVEST = ("graham", "buffett", "lynch", "greenblatt", "dreman")
TRADE = ("oneil", "minervini", "weinstein", "darvas", "turtle")
WHO = {"graham": "Graham", "buffett": "Buffett", "lynch": "Lynch", "greenblatt": "Greenblatt", "dreman": "Dreman", "oneil": "O'Neil",
       "minervini": "Minervini", "weinstein": "Weinstein", "darvas": "Darvas", "turtle": "the Turtle traders"}
WORD = {1: "rising", -1: "falling", 0: "flat"}


def _join(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _pct(v, n=1) -> str:
    return f"{v:+.{n}f}%".replace("+", "up ").replace("-", "down ") if v is not None else ""


def _day(d: str) -> str:
    t = dt.date.fromisoformat(d)
    return f"{t.strftime('%A')} {t.day} {t.strftime('%B')}"


def market_brief(signals: list[dict], tsum: dict, mk: dict, aspi) -> list[str]:
    M, I = tsum["market"], tsum["market"].get("index")
    p1, p2 = [], []
    if aspi is not None and len(aspi) >= 2:
        last, prev, day = float(aspi.iloc[-1]), float(aspi.iloc[-2]), aspi.index[-1].strftime("%Y-%m-%d")
        chg = (last / prev - 1) * 100
        s = f"The ASPI closed at {last:,.0f} on {_day(day)}, {'little changed' if abs(chg) < 0.05 else _pct(chg, 2)} on the day."
        ytd = mk.get("official_ytd")
        if I:
            s += f" It is {_pct(I['r20'])} over the past month" + (f" and {_pct(ytd['ret'])} since the start of the year." if ytd else ".")
        p1.append(s)
    if I:
        fr = I["price"]
        groups: dict[int, list[str]] = {1: [], 0: [], -1: []}
        for k, name in (("short", "the month"), ("medium", "the quarter"), ("long", "the year"), ("years", "three years")):
            if fr.get(k):
                groups[fr[k]["dir"]].append(name)
        bits = [f"{WORD[d]} over {_join(groups[d])}" for d in (1, 0, -1) if groups[d]]
        p1.append(f"By its averages the market is {_join(bits)}.")
    if M.get("advancers") is not None:
        p1.append(f"{M['advancers']} shares rose over the month and {M['decliners']} fell; {M['above50']:.0f}% stand above their 50-day average "
                  f"and {M['above200']:.0f}% above their 200-day.")
    secs = [s for s in tsum.get("sectors", []) if s["n"] >= 5 and s["r60"] is not None]
    if len(secs) >= 5:
        top, bottom = secs[:3], secs[-2:]
        p2.append(f"Over three months {_join([s['sector'] for s in top])} have done best and {_join([s['sector'] for s in bottom])} worst.")
    buys, sells = sum("BUY" in s["signal"] for s in signals), sum("SELL" in s["signal"] for s in signals)
    rated = sum(1 for s in signals if s.get("score") is not None)
    inv, trd = sum(bool(s.get("invest")) for s in signals), sum(bool(s.get("trade")) for s in signals)
    watch = sum(bool(s.get("trade_watch")) and not s.get("trade") for s in signals)
    p2.append(f"Of {rated} shares that trade enough to be rated, {buys} are rated buy and {sells} sell. {inv} pass the investing test; "
              + (f"{trd} pass the trading test" if trd else "none passes the trading test today") + (f", with {watch} one condition short." if watch else "."))
    now = (mk.get("patterns") or {}).get("now") or {}
    m, nx = now.get("month"), now.get("next_month")
    if m:
        s = f"{MONTH[m['m']]} has risen in {m['up']} of the last {m['n']} years"
        if nx:
            s += f", {MONTH[nx['m']]} in {nx['up']} of {nx['n']}"
        p2.append(s + ".")
    return [" ".join(p1), " ".join(p2)]


def picks(signals: list[dict], tsum: dict, T: dict) -> tuple[list[dict], list[dict]]:
    by = {s["symbol"]: s for s in signals}
    look, care, seen = [], [], set()

    def add(to, sym, tag, why, limit):
        if sym in seen or sym not in by or sum(x["tag"] == tag for x in to) >= limit:
            return
        seen.add(sym)
        to.append({"s": sym, "name": by[sym].get("name", ""), "tag": tag, "why": why})

    liquid = [s for s in signals if (s.get("tov") or 0) >= 1_000_000]
    well = [s for s in signals if (s.get("tov") or 0) >= 5_000_000 and s.get("score") is not None]
    for s in sorted([x for x in well if x["signal"] == "STRONG BUY"], key=lambda x: -x["score"]):
        add(look, s["symbol"], "Strong buy", f"Ahead of {s['score']}% of rated shares on price, volume and company figures"
            + ("; it also passes the investing test." if s.get("invest") else "."), 3)
    for s in sorted([x for x in well if x["signal"] == "STRONG SELL"], key=lambda x: x["score"]):
        add(care, s["symbol"], "Strong sell", f"Behind {100 - s['score']}% of rated shares on price, volume and company figures.", 3)
    for s in sorted([x for x in liquid if x.get("trade")], key=lambda x: -(x["score"] or 0)):
        add(look, s["symbol"], "Trading pick", f"Passes every trading condition; reward to risk after costs is {s.get('rr')}.", 3)
    for s in sorted([x for x in liquid if x.get("trade_watch") and not x.get("trade")], key=lambda x: -(x["score"] or 0)):
        add(look, s["symbol"], "Trading watch", "One condition short of a trading pick.", 2)
    for s in sorted([x for x in liquid if x.get("invest") and (x.get("tr") or 0) >= 1], key=lambda x: -(x["score"] or 0)):
        bits = [f"P/E {s['pe']}" if s.get("pe") else "", f"return on equity {s['roe']}%" if s.get("roe") else "", (s.get("trh") or "").lower()]
        why = _join(bits)
        add(look, s["symbol"], "Investment pick", why[:1].upper() + why[1:] + ".", 3)
    ago = lambda n: "a few trading days ago" if n is None else "yesterday" if n <= 1 else f"{n} trading days ago"
    busy = lambda sym: (by.get(sym, {}).get("tov") or 0) >= 5_000_000          # the home screen only points at shares that trade
    for x in tsum["lists"].get("turned_up", []):
        d = ((T.get(x["s"]) or {}).get("price") or {}).get("medium") or {}
        if busy(x["s"]):
            add(look, x["s"], "Just turned up", f"Its quarter trend turned up {ago(d.get('days'))}; {_pct(x.get('r20'))} in a month.", 2)
    for x in tsum["lists"].get("price_up_volume_up", []):
        v = (T.get(x["s"]) or {}).get("volume") or {}
        if busy(x["s"]) and v.get("ratio") and v["ratio"] <= 20:
            add(look, x["s"], "Volume building", f"Trading volume is {v['ratio']:.1f} times its earlier level while the price rises.", 2)
    many = sorted([s for s in liquid if (s.get("vet") or "")[:5].count("F") >= 3], key=lambda s: (-(s["vet"][:5].count("F")), -(s.get("tov") or 0)))
    for s in many:
        add(look, s["symbol"], "Checklists", f"Passes {s['vet'][:5].count('F')} of the 5 investing checklists.", 2)
    for x in tsum["lists"].get("turned_down", []):
        d = ((T.get(x["s"]) or {}).get("price") or {}).get("medium") or {}
        if busy(x["s"]):
            add(care, x["s"], "Just turned down", f"Its quarter trend turned down {ago(d.get('days'))}.", 2)
    for s in sorted([x for x in signals if x.get("pr") == "dist" and (x.get("tov") or 0) >= 5_000_000], key=lambda x: -(x.get("tov") or 0)):
        add(care, s["symbol"], "Quiet selling", "Volume has been arriving on weak days while the price holds up.", 2)
    return look[:9], care[:4]


def share_summary(s: dict, t: dict | None, lenses: list[dict] | None) -> str:
    out = []
    if t:
        head = t["headline"].rstrip(".")
        r, m = t.get("range") or {}, (t.get("price") or {}).get("short")
        tail = ""
        if m and r.get("from_high") is not None:
            where = "at its 52-week high" if r["from_high"] > -0.5 else f"{abs(r['from_high']):.0f}% below its 52-week high"
            tail = f": {_pct(m['chg'])} over the month and {where}"
        out.append(head + tail + ".")
    if s.get("pg"):
        moved = "" if s["signal"] == s["pg"] else f" (on price and volume alone it would be {s['pg'].lower()}; its company figures make the difference)"
        out.append(f"It is rated {s['signal'].lower()}{moved}: taking price, volume and company figures together it is ahead of {s['score']}% of the {s.get('of', '')} rated shares.".replace("  ", " "))
    elif s.get("why"):
        out.append(f"It is not rated, because {s['why']}.")
    val = [f"{s['pe']} times earnings" if s.get("pe") else "", f"a {s['roe']}% return on equity" if s.get("roe") else "",
           f"a {s['div_yield']}% dividend yield" if s.get("div_yield") else ""]
    val = [v for v in val if v]
    e = (t or {}).get("earnings")
    if val:
        sent = f"On checked figures it trades at {val[0]}" if s.get("pe") else f"Its checked figures show {val[0]}"
        if len(val) > 1:
            sent += ", with " + _join(val[1:])
        if e:
            sent += f"; profits rose in {e['up']} of the last {e['n']} reports"
        out.append(sent + ".")
    elif e:
        out.append(f"Profits rose in {e['up']} of the last {e['n']} checked reports; there is no checked reading of value.")
    else:
        out.append("Its reports have not yielded checked figures, so there is no reading of value.")
    if lenses:
        by = {x["id"]: x["verdict"] for x in lenses}
        inv, trd = [WHO[k] for k in INVEST if by.get(k) == "fits"], [WHO[k] for k in TRADE if by.get(k) == "fits"]
        own = "It passes this site's investing test" if s.get("invest") else ""
        if s.get("trade"):
            own = (own + " and its trading test" if own else "It passes this site's trading test")
        elif s.get("trade_watch"):
            own = (own + " and is one condition short of a trading pick" if own else "It is one condition short of a trading pick")
        if own:
            out.append(own + ".")
        if inv or trd:
            parts = ([f"the {_join(inv)} investing checklist{'s' if len(inv) > 1 else ''}"] if inv else []) + \
                    ([f"the trading rules of {_join(trd)}"] if trd else [])
            out.append(f"It fits {_join(parts)}.")
        else:
            out.append("It fits none of the veterans' checklists as things stand.")
    care = []
    if s.get("pr") == "dist":
        care.append("volume points to quiet selling")
    if (s.get("tov") or 0) < 1_000_000:
        care.append("it trades thinly, so prices can jump on a single order")
    if s.get("data_fail"):
        care.append("some of its report figures failed checks and are left out")
    if care:
        out.append(("Note that " + _join(care[:2]) + "."))
    return " ".join(out)


def build(signals: list[dict], T: dict, V: dict, tsum: dict, mk: dict, aspi) -> dict:
    look, care = picks(signals, tsum, T)
    day = max((s["date"] for s in signals), default="")
    home = {"date": day, "day": _day(day) if day else "", "paras": market_brief(signals, tsum, mk, aspi), "look": look, "care": care}
    return {"home": home, "shares": {s["symbol"]: share_summary(s, T.get(s["symbol"]), V.get(s["symbol"])) for s in signals}}
