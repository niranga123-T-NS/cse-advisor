"""World markets for the banner: metals, oil and fuels, the rupee, major currencies and share indices.

Source: Yahoo Finance's free chart data (delayed 10-30 minutes for futures and indices; FX rates are unofficial).
LME metal prices are paid data, so COMEX/CME futures stand in for them and are labelled so.
The Cloudflare site refreshes these every 5 minutes itself (functions/world.js); this file writes the copy
used by the GitHub Pages site and as a fallback.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json"}


def symbols() -> list[dict]:
    return json.loads((ROOT / "world_symbols.json").read_text())["items"]


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


def summarise(res: dict) -> dict | None:
    """One Yahoo chart result -> price, changes over 1 day / 1 week / 1 month, and a 1-month line."""
    meta = res.get("meta") or {}
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    ts = res.get("timestamp") or []
    pts = [(t, c) for t, c in zip(ts, q) if c]
    p = meta.get("regularMarketPrice") or (pts[-1][1] if pts else None)
    if not p or len(pts) < 3:
        return None
    closes = [c for _, c in pts]
    # the last daily bar is usually today's (still moving); compare with the bar before it
    same_day = meta.get("regularMarketTime") and pts[-1][0] // 86400 == int(meta["regularMarketTime"]) // 86400
    base = closes[:-1] if same_day else closes
    ago = lambda n: base[-n] if len(base) >= n else None
    return {"p": round(p, 4), "d1": _pct(p, ago(1)), "w1": _pct(p, ago(5)), "m1": _pct(p, ago(21)),
            "t": int(meta.get("regularMarketTime") or pts[-1][0]), "line": [round(c, 4) for c in closes[-22:]]}


def fetch_one(s: requests.Session, k: str) -> dict | None:
    for attempt in range(3):
        try:
            r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{k}", headers=UA, timeout=20,
                      params={"range": "3mo", "interval": "1d"})
            if r.status_code == 200:
                return summarise(r.json()["chart"]["result"][0])
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            pass
        time.sleep(2 * (attempt + 1))
    return None


def cross(a: dict | None, b: dict | None) -> dict | None:
    """a ÷ b, e.g. JPY/LKR from USD/LKR and USD/JPY."""
    if not a or not b or not b["p"]:
        return None
    ch = lambda x, y: round(((1 + x / 100) / (1 + y / 100) - 1) * 100, 2) if x is not None and y is not None else None
    n = min(len(a["line"]), len(b["line"]))
    return {"p": round(a["p"] / b["p"], 4), "d1": ch(a["d1"], b["d1"]), "w1": ch(a["w1"], b["w1"]), "m1": ch(a["m1"], b["m1"]),
            "t": min(a["t"], b["t"]), "line": [round(x / y, 4) for x, y in zip(a["line"][-n:], b["line"][-n:]) if y]}


def build() -> dict:
    items = symbols()
    direct = [it for it in items if not it.get("calc")]
    with requests.Session() as s, ThreadPoolExecutor(6) as ex:
        fetched = dict(zip([it["k"] for it in direct], ex.map(lambda it: fetch_one(s, it["k"]), direct)))
    got = [cross(fetched.get(it["calc"][0]), fetched.get(it["calc"][1])) if it.get("calc") else fetched.get(it["k"]) for it in items]
    out = [{**it, **g} for it, g in zip(items, got) if g]
    missing = [it["k"] for it, g in zip(items, got) if not g]
    if missing:
        print("world: no data for", ", ".join(missing))
    return {"updated": int(time.time()), "items": out}


if __name__ == "__main__":
    w = build()
    print(json.dumps({x["l"]: [x["p"], x["d1"]] for x in w["items"]}, indent=1))
