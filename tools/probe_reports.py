import io, json, re, sys, collections
sys.path.insert(0, ".")
import cse_client as cse, requests
from pypdf import PdfReader
for sym in ["JKH.N0000", "COMB.N0000"]:
    j = cse.post("financials", {"symbol": sym})
    rf = j.get("reqFinancial") or []
    print(f"\n##### {sym} reqFinancial {len(rf)}")
    c = collections.Counter((r.get("elmId"), r.get("data")) for r in rf)
    for k, v in list(c.items())[:40]: print(v, k)
    print("all keys:", sorted({k for r in rf for k in r}))
    print("sample distinct rows:", json.dumps([r for r in rf if r.get("data") != "Financial Statements Summary"][:15])[:2500])
    for kind in ["infoQuarterlyData", "infoAnnualData"]:
        items = j.get(kind) or []
        print(f"\n{kind}: {len(items)}; titles:", [i["fileText"] for i in items[:14]])
        it = items[0]
        url = "https://cdn.cse.lk/" + it["path"]
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        print("PDF", url, r.status_code, len(r.content)//1024, "KB")
        try:
            pdf = PdfReader(io.BytesIO(r.content)); print("pages", len(pdf.pages))
            text = "\n".join((pdf.pages[i].extract_text() or "") for i in range(min(len(pdf.pages), 8 if kind=="infoQuarterlyData" else 40)))
            print("chars", len(text))
            for kw in ["Revenue", "Profit for the period", "Profit attributable", "Earnings per share", "Net assets per share", "Total equity", "Interest income", "Net interest"]:
                m = [l for l in text.splitlines() if kw.lower() in l.lower()][:3]
                print(f"  {kw}: {m}")
        except Exception as e:
            print("pdf err", e)
