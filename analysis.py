"""Indicators, a transparent scoring model, and a simple long-only backtest.

Everything is rule-based and explainable: each signal lists the reasons behind it.
Tune the weights/thresholds in RULES and check them with the backtest before trusting them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULES = {
    "buy": 1.5,          # score >= this -> BUY
    "strong_buy": 3.0,
    "sell": -1.5,        # score <= this -> SELL
    "strong_sell": -3.0,
    "stop_atr": 2.0,     # suggested stop = close - 2 x ATR
    "target_atr": 3.0,   # suggested target = close + 3 x ATR
    "cost_per_side": 0.0112,   # ~1.12% CSE retail brokerage+fees per side; adjust to your broker
    "min_turnover": 1_000_000, # LKR/day average below this is flagged illiquid
}


# ---------------------------------------------------------------- indicators

def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["close"]
    d["sma20"] = c.rolling(20).mean()
    d["sma50"] = c.rolling(50).mean()
    d["sma200"] = c.rolling(200).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = (100 - 100 / (1 + rs)).fillna(100.0).where(loss.notna())

    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]

    std = c.rolling(20).std()
    d["bb_up"], d["bb_lo"] = d["sma20"] + 2 * std, d["sma20"] - 2 * std

    prev = c.shift()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    d["vol_avg20"] = d["volume"].rolling(20).mean()
    d["turnover_avg20"] = (d["close"] * d["volume"]).rolling(20).mean()
    return d


# ---------------------------------------------------------------- scoring

def _score_row(r: pd.Series, prev: pd.Series | None) -> tuple[float, list[str]]:
    s, why = 0.0, []

    def add(points, text):
        nonlocal s
        s += points
        why.append(f"{'+' if points > 0 else ''}{points:g}  {text}")

    if pd.notna(r.sma50):
        add(1, "Price above 50-day average (uptrend)") if r.close > r.sma50 else add(-1, "Price below 50-day average (downtrend)")
    if pd.notna(r.sma20) and pd.notna(r.sma50):
        add(1, "20-day average above 50-day") if r.sma20 > r.sma50 else add(-1, "20-day average below 50-day")
    if pd.notna(r.sma200):
        add(0.5, "Above 200-day average (long-term uptrend)") if r.close > r.sma200 else add(-0.5, "Below 200-day average")
    if pd.notna(r.rsi):
        if r.rsi < 30:
            add(1, f"RSI {r.rsi:.0f}: oversold")
        elif r.rsi > 70:
            add(-1, f"RSI {r.rsi:.0f}: overbought")
    if pd.notna(r.macd_hist) and prev is not None and pd.notna(prev.macd_hist):
        if r.macd_hist > 0 and prev.macd_hist <= 0:
            add(1.5, "MACD bullish crossover")
        elif r.macd_hist < 0 and prev.macd_hist >= 0:
            add(-1.5, "MACD bearish crossover")
        elif r.macd_hist > 0:
            add(0.5, "MACD momentum positive")
        else:
            add(-0.5, "MACD momentum negative")
    if pd.notna(r.bb_lo):
        if r.close < r.bb_lo:
            add(0.5, "Below lower Bollinger band (stretched down)")
        elif r.close > r.bb_up:
            add(-0.5, "Above upper Bollinger band (stretched up)")
    if pd.notna(r.vol_avg20) and r.vol_avg20 > 0 and prev is not None and r.volume > 1.5 * r.vol_avg20:
        up = r.close > prev.close
        add(0.5 if up else -0.5, f"Volume {r.volume / r.vol_avg20:.1f}x average on an {'up' if up else 'down'} day")
    return s, why


def label(score: float) -> str:
    if score >= RULES["strong_buy"]:
        return "STRONG BUY"
    if score >= RULES["buy"]:
        return "BUY"
    if score <= RULES["strong_sell"]:
        return "STRONG SELL"
    if score <= RULES["sell"]:
        return "SELL"
    return "HOLD"


def score_series(d: pd.DataFrame) -> pd.Series:
    scores, prev = [], None
    for _, r in d.iterrows():
        scores.append(_score_row(r, prev)[0])
        prev = r
    return pd.Series(scores, index=d.index)


def advice(df: pd.DataFrame) -> dict | None:
    """Latest signal for one stock, with reasons and risk levels."""
    if len(df) < 30:
        return None
    d = indicators(df)
    r, prev = d.iloc[-1], d.iloc[-2]
    score, why = _score_row(r, prev)
    out = {
        "date": d.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(r.close), 2),
        "change_pct": round(float((r.close / prev.close - 1) * 100), 2) if prev.close else None,
        "score": round(score, 1),
        "signal": label(score),
        "reasons": why,
        "rsi": None if pd.isna(r.rsi) else round(float(r.rsi), 1),
        "stop": None, "target": None,
        "warnings": [],
    }
    if pd.notna(r.atr):
        out["stop"] = round(float(r.close - RULES["stop_atr"] * r.atr), 2)
        out["target"] = round(float(r.close + RULES["target_atr"] * r.atr), 2)
    if pd.notna(r.turnover_avg20) and r.turnover_avg20 < RULES["min_turnover"]:
        out["warnings"].append(f"Low liquidity: ~LKR {r.turnover_avg20:,.0f}/day traded - hard to exit")
    if len(df) < 200:
        out["warnings"].append(f"Only {len(df)} days of history - long-term trend not assessed")
    return out


# ---------------------------------------------------------------- backtest

def backtest(df: pd.DataFrame) -> dict | None:
    """Long-only: enter next day's open on BUY, exit next open on SELL or if stop is hit."""
    if len(df) < 60:
        return None
    d = indicators(df)
    d["score"] = score_series(d)
    cost = RULES["cost_per_side"]
    equity, pos, entry, stop = 1.0, False, 0.0, 0.0
    curve, trades = [], []
    rows = list(d.itertuples())
    for i in range(1, len(rows)):
        y, t = rows[i - 1], rows[i]
        if pos and t.low <= stop:  # stop hit intraday
            px = min(t.open, stop)
            trades.append(px * (1 - cost) / entry - 1)
            equity *= px * (1 - cost) / (entry_mark)
            pos = False
        elif pos and y.score <= RULES["sell"]:
            px = t.open
            trades.append(px * (1 - cost) / entry - 1)
            equity *= px * (1 - cost) / entry_mark
            pos = False
        elif not pos and y.score >= RULES["buy"] and pd.notna(y.atr):
            entry = t.open * (1 + cost)
            entry_mark = entry
            stop = t.open - RULES["stop_atr"] * y.atr
            pos = True
        if pos:  # mark to market at close
            equity *= t.close / entry_mark
            entry_mark = t.close
        curve.append(equity)
    eq = pd.Series(curve)
    dd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
    wins = [x for x in trades if x > 0]
    return {
        "period": f"{d.index[0]:%Y-%m-%d} to {d.index[-1]:%Y-%m-%d}",
        "strategy_return_pct": round((equity - 1) * 100, 1),
        "buy_hold_return_pct": round((d.close.iloc[-1] / d.close.iloc[0] - 1) * 100, 1),
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 0) if trades else None,
        "avg_trade_pct": round(100 * float(np.mean(trades)), 1) if trades else None,
        "max_drawdown_pct": round(dd * 100, 1),
        "still_open": pos,
    }
