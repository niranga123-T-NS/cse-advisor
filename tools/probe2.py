import json, sys, datetime as dt
sys.path.insert(0, ".")
import cse_client as cse
cse.MIN_INTERVAL_SEC = 0.4
T = lambda ms: dt.datetime.fromtimestamp(ms/1000, tz=dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")
for sym, sid in (("JKH.N0000", 297), ("ABAN.N0000", 204)):
    for period in (1, 2, 3, 4, 5):
        j = cse.post("companyChartDataByStock", {"stockId": sid, "period": period}, retries=1) or {}
        pts = j.get("chartData") or []
        if not pts: print(sym, period, "none"); continue
        days = sorted({T(p["t"])[:10] for p in pts})
        print(f"\n{sym} period {period}: {len(pts)} points, {len(days)} days, {T(pts[0]['t'])} .. {T(pts[-1]['t'])}")
        print("  first", json.dumps(pts[0])); print("  last ", json.dumps(pts[-1]))
        # points per day for the last 3 days and typical spacing
        last = [p for p in pts if T(p["t"])[:10] == days[-1]]
        print("  last day points", len(last), [T(p["t"])[11:] for p in last[:6]])
for chart_id in (1, 2):
    for period in (1, 2, 3, 5):
        j = cse.post("chartData", {"symbol": "ASPI", "chartId": chart_id, "period": period}, retries=1)
        if isinstance(j, list) and j:
            print(f"\nindex chartId {chart_id} period {period}: {len(j)} pts {T(j[0]['d'])} .. {T(j[-1]['d'])} first {j[0]} last {j[-1]}")
        else:
            print("index", chart_id, period, "none", str(j)[:100])
ob = cse.post("orderBook", {"symbol": "COMB.N0000"}); print("\norderBook COMB", json.dumps(ob)[:600])
ob = cse.post("orderBook", {"symbol": "JKH.N0000"}); print("orderBook JKH", json.dumps(ob)[:900])
secs = cse.post("allSectors"); print("\nsectors", [(s["name"], s["sectorId"], s["symbol"], round(s["percentage"], 2)) for s in secs])
