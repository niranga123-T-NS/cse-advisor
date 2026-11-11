"""Storage as plain CSV files in data/ so GitHub can keep the history in the repo.

data/history/<SYMBOL>.csv   date,open,high,low,close,volume
data/securities.csv         symbol,name,stock_id
watchlist.txt               one symbol per line (these get full history backfilled)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
HIST = ROOT / "data" / "history"
SEC = ROOT / "data" / "securities.csv"
WATCH = ROOT / "watchlist.txt"
COLS = ["date", "open", "high", "low", "close", "volume"]


def _path(symbol: str) -> Path:
    return HIST / f"{symbol}.csv"


def upsert_bars(symbol: str, bars: list[dict]) -> int:
    if not bars:
        return 0
    HIST.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(bars)[COLS]
    p = _path(symbol)
    if p.exists():
        new = pd.concat([pd.read_csv(p), new])
    new = new.drop_duplicates("date", keep="last").sort_values("date")
    new.to_csv(p, index=False, float_format="%.4f")
    return len(bars)


def load_bars(symbol: str) -> pd.DataFrame:
    p = _path(symbol)
    if not p.exists():
        return pd.DataFrame(columns=COLS[1:])
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    return df.astype(float)


def bar_count(symbol: str) -> int:
    p = _path(symbol)
    return max(0, sum(1 for _ in p.open()) - 1) if p.exists() else 0


def symbols_with_data(min_bars: int = 1) -> list[str]:
    if not HIST.exists():
        return []
    return sorted(p.stem for p in HIST.glob("*.csv") if bar_count(p.stem) >= min_bars)


def _securities() -> pd.DataFrame:
    if SEC.exists():
        return pd.read_csv(SEC, dtype=str).fillna("")
    return pd.DataFrame(columns=["symbol", "name", "stock_id"])


def upsert_security(symbol: str, name: str = "", stock_id=None):
    df = _securities()
    row = df.index[df.symbol == symbol]
    if len(row):
        i = row[0]
        if name:
            df.at[i, "name"] = name
        if stock_id is not None:
            df.at[i, "stock_id"] = str(stock_id)
    else:
        df.loc[len(df)] = [symbol, name, "" if stock_id is None else str(stock_id)]
    SEC.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("symbol").to_csv(SEC, index=False)


def security_names() -> dict[str, str]:
    df = _securities()
    return dict(zip(df.symbol, df.name))


def watchlist() -> list[str]:
    if not WATCH.exists():
        return []
    lines = (l.split("#")[0].strip().upper() for l in WATCH.read_text().splitlines())
    return sorted({l for l in lines if l})


def set_watch(symbol: str, on: bool):
    w = set(watchlist())
    w.add(symbol) if on else w.discard(symbol)
    header = "# One CSE symbol per line. These get full history loaded automatically.\n"
    WATCH.write_text(header + "\n".join(sorted(w)) + "\n")
