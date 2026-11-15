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


SPLIT_MIN = 1.8      # a price that changes by this factor in one step, and stays there, was rebased (a share split)
WHOLE = [1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 75, 80, 100, 125, 150, 200, 250, 300, 400, 500, 1000]


def find_splits(df: pd.DataFrame, step_min: float = 1.7, stay_min: float = SPLIT_MIN) -> list[tuple]:
    """Share splits (and other capital changes that rebase the price) found in unadjusted daily bars.

    CSE's price history is not adjusted for them: on the day a 10-for-1 split takes effect the close simply drops
    by 90%. A listed share does not otherwise lose or gain this much in one step and stay there, so a split is:
      * a close that changes by a factor of 1.7 or more (or doubles) from a price of Rs 2 or more, and
      * stays: the middle close of the next days against the middle close of the days before gives 1.8 or more.
    That ratio of middles is the factor, set to a whole ratio (2, 5, 10, 200 ...) when within 6% of one.
    Returns [(date, factor, ratio seen)], factor above 1 for a split and below 1 for a consolidation.
    """
    import math
    c = df["close"].to_numpy(dtype=float)
    out, i = [], 1
    while i < len(c):
        step = c[i - 1] / c[i] if c[i] > 0 else 1.0
        if c[i - 1] >= 2 and (step >= step_min or step <= 0.5):
            seen = float(pd.Series(c[max(0, i - 5):i]).median() / pd.Series(c[i:i + 5]).median())
            if seen >= stay_min or seen <= 1 / 2.0:
                f = seen if seen > 1 else 1 / seen
                near = min(WHOLE, key=lambda k: abs(math.log(f / k)))
                if abs(math.log(f / near)) <= math.log(1.06):
                    f = float(near)
                out.append((df.index[i], f if seen > 1 else 1 / f, seen))
                i += 5                               # the first days after are still settling
                continue
        i += 1
    return out


def load_raw_bars(symbol: str) -> pd.DataFrame:
    """Daily bars exactly as CSE gave them (not adjusted for share splits)."""
    p = _path(symbol)
    if not p.exists():
        return pd.DataFrame(columns=COLS[1:])
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    return df.astype(float)


def _share_factor(symbol: str, date, price_factor: float) -> float | None:
    """The split ratio according to the company's own reports: shares in issue after the date over shares before
    (each report implies its share count as profit / earnings per share; CSE gives today's count).
    Used only when it lands within 3% of a whole ratio, agrees roughly with what the price did, and the two
    ways of counting 'after' do not point to different whole ratios."""
    import json
    import math
    p = ROOT / "data" / "fundamentals" / f"{symbol}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except ValueError:
        return None
    day = pd.Timestamp(date).strftime("%Y-%m-%d")
    q = sorted((r for r in (d.get("quarterly") or {}).values() if r.get("ok") and r.get("eps") and r.get("profit")), key=lambda r: r["end"])
    implied = lambda r: abs(r["profit"] * (r.get("unit") or 1) / r["eps"])
    before = [implied(r) for r in q if r["end"] < day][-3:]
    after = [implied(r) for r in q if r["end"] >= day][:2]
    if not before:
        return None
    b = float(pd.Series(before).median())
    found = set()
    for a in ([float(pd.Series(after).median())] if after else []) + ([float(d["shares"])] if d.get("shares") else []):
        ratio = a / b if b else 0
        if ratio <= 0 or abs(math.log(ratio / price_factor)) > math.log(1.3):
            continue                                  # nothing like what the price did: another kind of share issue
        near = min(WHOLE, key=lambda k: abs(math.log(ratio / k)))
        if abs(math.log(ratio / near)) <= math.log(1.03):
            found.add(float(near))
    return found.pop() if len(found) == 1 else None


def _second_source(symbol: str, date) -> float | None:
    """The same split as it appears in the second price source (the long-history download), when that source has
    daily prices across it and shows a whole ratio. It often has trading days CSE's one-year chart leaves out."""
    import math
    p = ROOT / "data" / "long" / "raw" / f"{symbol}.csv.gz"
    if not p.exists():
        return None
    y = pd.read_csv(p, parse_dates=["date"]).set_index("date").astype(float)
    y = y[(y["volume"].fillna(0) > 0) & (y["close"] > 0)]
    y = y[(y.index >= pd.Timestamp(date) - pd.Timedelta(days=60)) & (y.index <= pd.Timestamp(date) + pd.Timedelta(days=40))]
    if len(y) < 8:
        return None
    for d, f, _ in find_splits(y, 1.45, 1.5):
        if abs((d - pd.Timestamp(date)).days) <= 25 and f > 1 and any(abs(math.log(f / k)) < 1e-9 for k in WHOLE):
            return float(f)
    return None


def splits(symbol: str) -> list[dict]:
    """The splits found for one counter: date, the factor applied, the ratio seen in the price, and what the factor rests on.

    The ratio seen in CSE's prices can be well off the true one when the price ran up or down around the split (shares
    are often suspended for a week and reopen elsewhere), so better evidence is used when there is any:
    the change in the share count in the company's own reports, then a whole ratio in the second price source."""
    df = load_raw_bars(symbol)
    out = []
    for d, f, seen in (find_splits(df) if len(df) > 1 else []):
        basis = "price, whole ratio" if f in WHOLE or (f < 1 and 1 / f in WHOLE) else "price"
        if f > 1:
            by_count, by_second = _share_factor(symbol, d, seen), _second_source(symbol, d)
            if by_count:
                f, basis = by_count, "share count in company reports" + (" and second price source" if by_second == by_count else "")
            elif by_second:
                f, basis = by_second, "second price source"
        out.append({"date": d.strftime("%Y-%m-%d"), "factor": round(f, 4), "seen": round(seen, 3), "basis": basis})
    return out


def load_bars(symbol: str) -> pd.DataFrame:
    """Daily bars on today's share basis: prices before a share split are divided by its factor (volumes multiplied),
    so a split does not look like a crash to the indicators, alerts and models that read this."""
    df = load_raw_bars(symbol)
    if len(df) < 2:
        return df
    for sp in splits(symbol):
        date, factor = pd.Timestamp(sp["date"]), sp["factor"]
        before = df.index < date
        df.loc[before, ["open", "high", "low", "close"]] = df.loc[before, ["open", "high", "low", "close"]] / factor
        df.loc[before, "volume"] = df.loc[before, "volume"] * factor
    return df



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


HEADER = ("# One CSE symbol per line. These get full history loaded automatically.\n"
          "# New listings are added automatically when they first trade.\n")
BACKFILLED = ROOT / "data" / "backfilled.csv"


def add_watch(symbols, on: bool = True):
    w = set(watchlist())
    (w.update if on else w.difference_update)(symbols)
    WATCH.write_text(HEADER + "\n".join(sorted(w)) + "\n")


def set_watch(symbol: str, on: bool):
    add_watch([symbol], on)


def last_backfill() -> dict[str, str]:
    if not BACKFILLED.exists():
        return {}
    df = pd.read_csv(BACKFILLED, dtype=str)
    return dict(zip(df.symbol, df.date))


def mark_backfilled(symbols, date: str):
    d = last_backfill()
    d.update({s: date for s in symbols})
    BACKFILLED.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(d.items()), columns=["symbol", "date"]).to_csv(BACKFILLED, index=False)
