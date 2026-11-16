import json, cse_client as cse
cse.MIN_INTERVAL_SEC = 0.6
for sym in ("JKH.N0000", "CSLK.N0000", "COMB.N0000"):
    j = cse.post("orderBook", {"symbol": sym}, retries=1)
    print("=== orderBook", sym, json.dumps(j)[:1500])
for ep in ("marketDepth", "orderBookDepth", "orderBookDetail", "getMarketDepth", "depth", "marketByPrice",
           "orderBookDetails", "bidAsk", "topBidAsk", "detailOrderBook", "orderBookFull", "companyOrderBook"):
    for data in ({"symbol": "JKH.N0000"}, {"stockId": cse.stock_id_for("JKH.N0000")}):
        j = cse.post(ep, data, retries=1)
        print("=== ", ep, data, "->", json.dumps(j)[:400] if j is not None else "None")
