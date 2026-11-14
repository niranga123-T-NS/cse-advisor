import json, sys, datetime as dt
sys.path.insert(0, ".")
import cse_client as cse
cse.MIN_INTERVAL_SEC = 0.4
T = lambda ms: dt.datetime.fromtimestamp(ms/1000, tz=dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")
for period in (6, 7, 8, 9, 10, 12, 0, 60, 365, 1825):
    j = cse.post("companyChartDataByStock", {"stockId": 297, "period": period}, retries=1) or {}
    pts = j.get("chartData") or []
    print("JKH period", period, len(pts), (T(pts[0]["t"]), T(pts[-1]["t"])) if pts else "")
for extra in ({"stockId": 297, "period": 5, "fromDate": "2021-01-01", "toDate": "2026-11-13"}, {"stockId": 297, "period": "5Y"}, {"stockId": 297, "period": "ALL"}, {"stockId": 297, "period": "MAX"}):
    j = cse.post("companyChartDataByStock", extra, retries=1) or {}
    pts = j.get("chartData") or []
    print("JKH", extra, len(pts), (T(pts[0]["t"]), T(pts[-1]["t"])) if pts else "")
for period in (4, 6, 7, 8, 10):
    j = cse.post("chartData", {"symbol": "ASPI", "chartId": 1, "period": period}, retries=1)
    print("ASPI period", period, (len(j), T(j[0]["d"]), T(j[-1]["d"])) if isinstance(j, list) and j else str(j)[:80])
for ep, data in [("companyHistory", {"symbol": "JKH.N0000"}), ("priceHistory", {"symbol": "JKH.N0000"}), ("historicalTrades", {"symbol": "JKH.N0000"}),
                 ("tradeHistory", {"stockId": 297}), ("companyTradeHistory", {"symbol": "JKH.N0000"}), ("shareHistory", {"symbol": "JKH.N0000"})]:
    j = cse.post(ep, data, retries=1)
    print(ep, "->", str(j)[:200] if j is not None else "FAILED")
