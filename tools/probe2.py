import json, sys, time
sys.path.insert(0, ".")
import cse_client as cse
cse.MIN_INTERVAL_SEC = 0.4
def show(name, j, n=700):
    print(f"\n=== {name} ===")
    if j is None: print("FAILED"); return
    if isinstance(j, dict):
        print("keys:", list(j.keys())[:40])
        for k, v in list(j.items())[:12]:
            if isinstance(v, list):
                print(f" {k}: list[{len(v)}]", json.dumps(v[:2], default=str)[:n])
            elif isinstance(v, dict):
                print(f" {k}: dict", json.dumps(v, default=str)[:n])
            else:
                print(f" {k}:", json.dumps(v, default=str)[:200])
    elif isinstance(j, list):
        print(f"list[{len(j)}]", json.dumps(j[:2], default=str)[:n])
    else:
        print(str(j)[:n])
ts = cse.post("tradeSummary")
rows = ts.get("reqTradeSummery") or []
print("tradeSummary rows", len(rows)); print(json.dumps(rows[0], default=str)); print(json.dumps(next(r for r in rows if r.get("symbol")=="JKH.N0000"), default=str))
show("marketStatus", cse.post("marketStatus")); show("marketSummery", cse.post("marketSummery")); show("aspiData", cse.post("aspiData"))
info = cse.post("companyInfoSummery", {"symbol": "JKH.N0000"}); show("companyInfoSummery", info, 1500)
sid = (info.get("reqSymbolInfo") or {}).get("id")
for ep, data in [("allSectors", {}), ("companyProfile", {"symbol": "JKH.N0000"}), ("detailedTrades", {"symbol": "JKH.N0000"}), ("detailedTrades", {}),
                 ("todaySharePrice", {}), ("dailyMarketSummery", {}), ("orderBook", {"symbol": "JKH.N0000"}), ("marketDepth", {"symbol": "JKH.N0000"}),
                 ("sectors", {}), ("sectorList", {}), ("listedCompanies", {}), ("companiesBySector", {}), ("sectorCompanies", {"sectorId": 1}),
                 ("allSecurityCode", {}), ("list_by_market_cap", {}), ("alphabetical", {"alphabet": "J"}), ("chartData", {"symbol": "JKH.N0000", "chartId": 1, "period": 1}),
                 ("companyChartDataByStock", {"stockId": sid, "period": 1}), ("indexSectors", {}), ("gicsSectors", {}), ("getSectors", {}), ("sectorSummary", {}),
                 ("allSectorCompanies", {}), ("securitiesBySector", {"sector": "Banks"}), ("companyDirectory", {}), ("listedCompanyDirectory", {})]:
    try:
        show(f"{ep} {data}", cse.post(ep, data, retries=1))
    except Exception as e:
        print(ep, "ERR", e)
