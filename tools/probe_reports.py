"""Test the report reader on a few companies and print what it found."""
import json, sys, time
sys.path.insert(0, ".")
import db, fundamentals as F
syms = sys.argv[1:] or ["JKH.N0000", "COMB.N0000", "HNB.N0000", "DIAL.N0000", "LOLC.N0000", "SAMP.N0000",
                        "AAF.N0000", "ABAN.N0000", "CTC.N0000", "HAYL.N0000"]
F.N_QUARTERLY, F.N_ANNUAL = 6, 2
t = time.time()
F.collect(syms, 40)
print(f"\nTOOK {time.time()-t:.0f}s")
for s in syms:
    d = F.load(s)
    df = db.load_bars(s)
    price = float(df.close.iloc[-1]) if len(df) else None
    print(f"\n######## {s} price {price}")
    for kind in ("quarterly", "annual"):
        for k, v in sorted(d.get(kind, {}).items(), key=lambda kv: kv[1]["end"] or "", reverse=True):
            print(f"  {kind[:1]} {v['end']} p{v.get('pages')} m{v.get('months')} eps={v.get('eps')}/{v.get('eps_prior')} "
                  f"pg={v.get('profit_growth')} rg={v.get('revenue_growth')} nav={v.get('navps')} dps={v.get('dps')} roe={v.get('roe')}")
    m = F.metrics(s, price)
    if m:
        print("  METRICS", json.dumps({k: v for k, v in m.items() if k not in ("quarters", "annual")}))
        print("  SCORE", F.score(m))
