"""Thin client for the (unofficial) Colombo Stock Exchange JSON API at https://www.cse.lk/api/.

The endpoints are undocumented and can change without notice, so every parser here is
defensive: it accepts several possible key names and skips rows it cannot understand.
Run `python probe.py` to dump raw responses if something stops working.
"""
from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "https://www.cse.lk/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal CSE research tool)",
    "Origin": "https://www.cse.lk",
    "Referer": "https://www.cse.lk/",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/plain, */*",
}
MIN_INTERVAL_SEC = 1.0  # be polite: at most ~1 request/second

_session = requests.Session()
_session.headers.update(HEADERS)
_last_call = 0.0


def post(endpoint: str, data: dict | None = None, retries: int = 3) -> Any:
    """POST to a CSE endpoint and return parsed JSON (or None on failure)."""
    global _last_call
    for attempt in range(retries):
        wait = MIN_INTERVAL_SEC - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            r = _session.post(BASE_URL + endpoint, data=data or {}, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                print(f"[cse] {endpoint} failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))
    return None


def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- market-wide

def market_status() -> str | None:
    j = post("marketStatus")
    return j.get("status") if isinstance(j, dict) else None


def aspi() -> dict | None:
    return post("aspiData")


def snp_sl20() -> dict | None:
    return post("snpData")


def trade_summary() -> list[dict]:
    """Today's board: one row per traded security, normalised to OHLCV-style fields."""
    j = post("tradeSummary")
    rows = []
    if isinstance(j, dict):
        rows = _pick(j, "reqTradeSummery", "reqTradeSummary", default=[]) or []
    elif isinstance(j, list):
        rows = j
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = _pick(r, "symbol")
        close = _num(_pick(r, "price", "lastTradedPrice", "closingPrice"))
        if not sym or close is None:
            continue
        out.append({
            "symbol": sym,
            "name": _pick(r, "name", default=""),
            "open": _num(_pick(r, "open", "openPrice")) or close,
            "high": _num(_pick(r, "high", "highPrice")) or close,
            "low": _num(_pick(r, "low", "lowPrice")) or close,
            "close": close,
            "volume": _num(_pick(r, "sharevolume", "shareVolume", "quantity", "qty")) or 0.0,
            "turnover": _num(_pick(r, "turnover")) or 0.0,
            "change_pct": _num(_pick(r, "percentageChange", "changePercentage")),
            "stock_id": _pick(r, "id", "securityId"),
        })
    return out


# ---------------------------------------------------------------- per-company

def company_info(symbol: str) -> dict | None:
    """Returns the raw company summary, e.g. reqSymbolInfo.{id, name, lastTradedPrice, ...}."""
    return post("companyInfoSummery", {"symbol": symbol})


def stock_id_for(symbol: str):
    j = company_info(symbol)
    if isinstance(j, dict):
        info = j.get("reqSymbolInfo") or {}
        return _pick(info, "id", "securityId")
    return None


def history(symbol: str, period: int = 5) -> list[dict]:
    """Daily history for one stock.

    CSE's chart endpoint takes the internal stockId and a `period` code
    (on the website: 1=1D, 2=1W, 3=1M, 4=1Q/6M, 5=1Y; codes may change).
    Each point has a timestamp `t` (ms) and some of o/h/l/c/q/p.
    """
    sid = stock_id_for(symbol)
    if sid is None:
        return []
    j = post("companyChartDataByStock", {"stockId": sid, "period": period})
    points = []
    if isinstance(j, dict):
        points = _pick(j, "chartData", default=None)
        if points is None:
            inner = _pick(j, "reqTradeSummery", "reqChartData", default={}) or {}
            points = inner.get("chartData", []) if isinstance(inner, dict) else inner
    elif isinstance(j, list):
        points = j
    bars = {}
    for p in points or []:
        if not isinstance(p, dict):
            continue
        t = _pick(p, "t", "d", "date", "tradeDate")
        c = _num(_pick(p, "c", "p", "close", "price"))
        if t is None or c is None:
            continue
        date = _to_date(t)
        if not date:
            continue
        bars[date] = {  # keep last point per day
            "date": date,
            "open": _num(_pick(p, "o", "open")) or c,
            "high": _num(_pick(p, "h", "high")) or c,
            "low": _num(_pick(p, "l", "low")) or c,
            "close": c,
            "volume": _num(_pick(p, "q", "v", "volume", "qty")) or 0.0,
        }
    return [bars[d] for d in sorted(bars)]


def _to_date(t) -> str | None:
    import datetime as dt
    try:
        t = float(t)
        if t > 1e11:  # milliseconds
            t /= 1000
        colombo = dt.timezone(dt.timedelta(hours=5, minutes=30))
        return dt.datetime.fromtimestamp(t, tz=colombo).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        s = str(t)[:10]
        return s if len(s) == 10 and s[4] == "-" else None
