import json, cse_client as cse
cse.MIN_INTERVAL_SEC = 0.6
s = "CSLK.N0000"
tries = [{"symbol": s}, {"symbol": s, "buySell": 2}, {"symbol": s, "buySell": "2"}, {"symbol": s, "side": "S"},
         {"symbol": s, "side": "2"}, {"symbol": s, "priceLevel": 2}, {"symbol": s, "level": 2}, {"symbol": s, "type": "ask"},
         {"symbol": s, "type": "S"}, {"symbol": s, "depth": 10}, {"symbol": s, "levels": 10}, {"symbol": s, "board": "-1"},
         {"securityId": 6052}, {"symbol": s, "securityId": 6052, "buySell": 2}]
for d in tries:
    j = cse.post("orderBook", d, retries=1)
    print("===", d, "->", json.dumps(j)[:700] if j is not None else None)
for ep in ("orderBookAsk", "orderBookBid", "orderBookSell", "orderBookBuy", "askOrderBook", "bidOrderBook", "orderBookAll", "orderBookTotal", "orderbook"):
    j = cse.post(ep, {"symbol": s}, retries=1)
    print("===", ep, "->", json.dumps(j)[:400] if j is not None else None)
