"""Temporary probe: how much price history do free outside sources hold for CSE shares and the ASPI?"""
import datetime as dt, json, time, requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*"}
D = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")

def yahoo(sym, host="query1"):
    url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}"
    try:
        r = requests.get(url, params={"period1": 0, "period2": int(time.time()), "interval": "1d", "events": "div,split"}, headers=UA, timeout=30)
    except Exception as e:
        return print(f"yahoo {sym:16} ERROR {e!r}"[:200])
    if r.status_code != 200:
        return print(f"yahoo {sym:16} HTTP {r.status_code} {r.text[:120]!r}")
    try:
        res = r.json()["chart"]["result"][0]
    except Exception:
        return print(f"yahoo {sym:16} no result {r.text[:160]!r}")
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    close = q.get("close") or []
    vol = q.get("volume") or []
    ok = [(t, c, v) for t, c, v in zip(ts, close, vol or [None] * len(ts)) if c is not None]
    ev = res.get("events", {})
    meta = res.get("meta", {})
    if not ok:
        return print(f"yahoo {sym:16} empty; meta {json.dumps(meta)[:200]}")
    years = {}
    for t, c, v in ok:
        years[D(t)[:4]] = years.get(D(t)[:4], 0) + 1
    print(f"yahoo {sym:16} {len(ok):5} days  {D(ok[0][0])} .. {D(ok[-1][0])}  first {ok[0][1]:.2f} last {ok[-1][1]:.2f}  "
          f"splits {len(ev.get('splits', {}))} divs {len(ev.get('dividends', {}))}  name {meta.get('longName') or meta.get('shortName')!r}  cur {meta.get('currency')}")
    print("      days per year:", " ".join(f"{y}:{n}" for y, n in sorted(years.items())))
    print("      last 3:", [(D(t), round(c, 2), v) for t, c, v in ok[-3:]], "| zero-volume days:", sum(1 for _, _, v in ok if not v))
    if ev.get("splits"):
        print("      splits:", [(D(int(k)), s.get("splitRatio")) for k, s in sorted(ev["splits"].items())][:8])

for s in ("^CSE", "JKH-N0000.CM", "COMB-N0000.CM", "COMB-X0000.CM", "HNB-N0000.CM", "SAMP-N0000.CM", "LOLC-N0000.CM", "DIAL-N0000.CM",
          "ABAN-N0000.CM", "CTC-N0000.CM", "GUAR-N0000.CM", "CALU-U0000.CM", "SEMB-X0000.CM"):
    yahoo(s); time.sleep(1.5)
yahoo("JKH-N0000.CM", "query2")

for name, url in (("stooq ^cse", "https://stooq.com/q/d/l/?s=%5Ecse&i=d"), ("stooq ^aspi", "https://stooq.com/q/d/l/?s=%5Easpi&i=d"), ("stooq jkh.lk", "https://stooq.com/q/d/l/?s=jkh.lk&i=d")):
    try:
        r = requests.get(url, headers=UA, timeout=30)
        lines = r.text.strip().splitlines()
        print(name, r.status_code, len(lines), "lines |", lines[:2], "...", lines[-1:] )
    except Exception as e:
        print(name, "ERROR", repr(e)[:150])
