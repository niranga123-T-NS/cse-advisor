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


def load() -> dict[str, str]:
    if not PATH.exists():
        return {}
    df = pd.read_csv(PATH, dtype=str).fillna("")
    return dict(zip(df.symbol, df.sector))


def build(refresh_all: bool = False):
    have = {} if refresh_all else load()
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
