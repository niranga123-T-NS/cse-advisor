"""Build data/sectors.csv: each counter's GICS industry group (from CSE's company profile).

    python sectors.py            # only symbols not yet in the file
    python sectors.py --all      # refresh everything
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

import cse_client as cse
import db

PATH = Path(__file__).parent / "data" / "sectors.csv"


def normalise(raw: str) -> str:
    """CSE's profile pages spell the industry groups many ways; map them onto the standard list."""
    t = "".join(ch for ch in (raw or "").lower() if ch.isalpha() or ch in " &").strip()
    if not t:
        return ""
    rules = [(("food", "staples"), "Food & Staples Retailing"), (("food",), "Food, Beverage & Tobacco"),
             (("beverage",), "Food, Beverage & Tobacco"), (("banks finance",), "Diversified Financials"),
             (("insurance",), "Insurance"),
             (("investment banking",), "Diversified Financials"), (("diversified financ",), "Diversified Financials"),
             (("consumer finance",), "Diversified Financials"),
             (("bank",), "Banks"), (("real estate",), "Real Estate"), (("health",), "Health Care Equipment & Services"),
             (("capital goods",), "Capital Goods"), (("industrials",), "Capital Goods"), (("trading",), "Capital Goods"),
             (("material",), "Materials"), (("power",), "Utilities"), (("utilit",), "Utilities"), (("energy",), "Energy"),
             (("software",), "Software & Services"), (("telecom",), "Telecommunication Services"),
             (("retail",), "Retailing"), (("consumer durables",), "Consumer Durables & Apparel"),
             (("consumer services",), "Consumer Services"), (("household",), "Household & Personal Products"),
             (("transport",), "Transportation"), (("auto",), "Automobiles & Components"),
             (("commercial",), "Commercial & Professional Services"), (("services",), "Commercial & Professional Services")]
    for keys, name in rules:
        if all(k in t for k in keys):
            return name
    return raw.strip()


def load_raw() -> dict[str, str]:
    if not PATH.exists():
        return {}
    df = pd.read_csv(PATH, dtype=str).fillna("")
    return dict(zip(df.symbol, df.sector))


def load() -> dict[str, str]:
    """symbol -> standard industry group."""
    return {s: normalise(v) for s, v in load_raw().items()}


def build(refresh_all: bool = False):
    have = {} if refresh_all else load_raw()
    syms = sorted(set(db.security_names()) | set(db.symbols_with_data(1)))
    todo = [s for s in syms if not have.get(s)]
    for i, s in enumerate(todo):
        j = cse.post("companyProfile", {"symbol": s}, retries=2) or {}
        info = (j.get("reqComSumInfo") or [{}])[0] or {}
        sec = (info.get("sector") or "").strip()
        if sec:
            have[s] = sec
        if i % 25 == 0:
            print(f"{i}/{len(todo)} {s}: {sec}")
    # a non-voting / other class shares its company's sector
    for s in syms:
        if not have.get(s):
            base = s.split(".")[0]
            sib = next((v for k, v in have.items() if k.split(".")[0] == base and v), "")
            if sib:
                have[s] = sib
    PATH.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(have.items()), columns=["symbol", "sector"]).to_csv(PATH, index=False)
    print(f"{len(have)} symbols with a sector; missing: {[s for s in syms if not have.get(s)][:20]}")


if __name__ == "__main__":
    build("--all" in sys.argv)
