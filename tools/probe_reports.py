import json, sys
sys.path.insert(0, ".")
import cse_client as cse
sym = "JKH.N0000"
def show(name, j, n=1500):
    print(f"\n=== {name} ===")
    if j is None: print("FAILED"); return
    if isinstance(j, dict):
        print("keys:", list(j.keys()))
        for k, v in j.items():
            if isinstance(v, list):
                print(f" {k}: list[{len(v)}]", json.dumps(v[:3], default=str)[:n])
            else:
                print(f" {k}:", json.dumps(v, default=str)[:400])
    else:
        print(json.dumps(j, default=str)[:n])
for ep, data in [("financials", {"symbol": sym}), ("companyFinancials", {"symbol": sym}),
                 ("getFinancialAnnouncement", {}), ("getFinancialAnnouncement", {"symbol": sym}),
                 ("annualReports", {"symbol": sym}), ("companyAnnualReports", {"symbol": sym}),
                 ("companyInfoSummery", {"symbol": sym})]:
    show(f"{ep} {data}", cse.post(ep, data, retries=1))
    show(f"{ep}?symbol (query)", cse.post(f"{ep}?symbol={sym}", {}, retries=1)) if data.get("symbol") else None
