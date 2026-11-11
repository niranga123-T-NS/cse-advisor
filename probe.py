"""Check the CSE API is reachable and see raw response shapes.

    python probe.py              # market endpoints
    python probe.py JKH.N0000    # plus company + chart endpoints for a symbol
"""
import json
import sys

import cse_client as cse


def show(name, data, n=600):
    print(f"\n=== {name} ===")
    print(json.dumps(data, indent=1)[:n] if data is not None else "FAILED")


show("marketStatus", cse.post("marketStatus"))
show("aspiData", cse.post("aspiData"))
ts = cse.post("tradeSummary")
show("tradeSummary (raw, first chars)", ts, 900)
print("\nParsed tradeSummary rows:", len(cse.trade_summary()))

if len(sys.argv) > 1:
    sym = sys.argv[1]
    show(f"companyInfoSummery {sym}", cse.company_info(sym), 900)
    sid = cse.stock_id_for(sym)
    print("stockId:", sid)
    if sid is not None:
        for period in (1, 2, 3, 4, 5):
            raw = cse.post("companyChartDataByStock", {"stockId": sid, "period": period})
            show(f"companyChartDataByStock period={period}", raw, 400)
    print(f"\nParsed history bars (period 5): {len(cse.history(sym))}")
