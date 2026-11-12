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
    "fib_lookback": 120,       # trading days (~6 months) to find the swing high and low
    "fib_min_swing": 0.10,     # ignore swings smaller than 10% (no meaningful levels)
}

FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fib_swing(high: np.ndarray, low: np.ndarray, end: int, n: int):
    """Swing high/low in the n bars up to `end` (inclusive). Returns (hi, lo, i_hi, i_lo) or None."""
    a = max(0, end - n + 1)
    hs, ls = high[a:end + 1], low[a:end + 1]
    if len(hs) < 20:
        return None
    i_hi, i_lo = a + int(np.argmax(hs)), a + int(np.argmin(ls))
    hi, lo = float(high[i_hi]), float(low[i_lo])
    if lo <= 0 or (hi - lo) / lo < RULES["fib_min_swing"]:
        return None
    return hi, lo, i_hi, i_lo


def fib_levels(hi, lo, up: bool) -> dict:
    """Retracement levels. Uptrend (low came first): measured down from the high; downtrend: up from the low."""
    rng = hi - lo
    return {r: (hi - r * rng) if up else (lo + r * rng) for r in FIB_RATIOS}


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

    # Fibonacci: swing high/low of the last ~6 months as of each day
    h, l = d["high"].to_numpy(float), d["low"].to_numpy(float)
    fh, fl, fu = [], [], []
    for i in range(len(d)):
        sw = fib_swing(h, l, i, RULES["fib_lookback"])
        fh.append(sw[0] if sw else np.nan); fl.append(sw[1] if sw else np.nan)
        fu.append((sw[3] < sw[2]) if sw else np.nan)  # True: low came before high -> uptrend
    d["fib_hi"], d["fib_lo"], d["fib_up"] = fh, fl, fu
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
    # Fibonacci retracement: pullbacks to 38.2-61.8% often find support (uptrend) or resistance (downtrend);
    # breaking the 78.6% level suggests the move is failing / reversing
    if pd.notna(r.fib_hi) and prev is not None:
        up = bool(r.fib_up)
        lv = fib_levels(r.fib_hi, r.fib_lo, up)
        atr = r.atr if pd.notna(r.atr) else 0
        tol = max(0.01 * r.close, 0.5 * atr)
        near = [k for k in (0.382, 0.5, 0.618) if abs(r.close - lv[k]) <= tol]
        rising = r.close > prev.close
        if up:
            if r.close < lv[0.786]:
                add(-1, f"Broke below 78.6% Fibonacci retracement ({lv[0.786]:.2f}) - uptrend failing")
            elif near:
                k = near[0]
                add(1 if rising else 0.5, f"{'Bouncing off' if rising else 'At'} {k*100:.1f}% Fibonacci support ({lv[k]:.2f})")
        else:
            if r.close > lv[0.786]:
                add(1, f"Broke above 78.6% Fibonacci retracement ({lv[0.786]:.2f}) - downtrend reversing")
            elif near:
                k = near[0]
                add(-1 if not rising else -0.5, f"{'Rejected at' if not rising else 'At'} {k*100:.1f}% Fibonacci resistance ({lv[k]:.2f})")
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


def _f(v):
    return None if v is None or pd.isna(v) else round(float(v), 4)


def classify(a: dict) -> dict:
    """Mark a stock as a TRADING candidate (short-term setup) and/or an INVESTMENT candidate (long-term value).

    TRADE  - liquid enough to get in and out (avg >= LKR 5M/day), technical score >= 2,
             price above its 50-day average with positive MACD momentum, RSI 40-70 (not overbought),
             reward-to-risk >= 1.5 (next Fibonacci resistance or ATR target vs ATR stop),
             and fundamentals not clearly bad (fundamental score > -2).
    INVEST - only VERIFIED report figures count: profitable with P/E 0-15, earnings growing
             (year-to-date EPS or multi-year EPS trend), quality or value (ROE >= 12% or P/BV <= 1.2),
             fundamental score >= 2, and tradeable (avg >= LKR 1M/day).
    """
    ind, f = a.get("ind") or {}, a.get("fundamentals") or {}
    V = f.get("verified") or {}
    ok = lambda k: V.get(k, False) and f.get(k) is not None
    close, tov = a["close"], ind.get("turnover_avg20") or 0
    out = {"trade": False, "invest": False, "trade_why": [], "invest_why": [], "trade_missing": [], "invest_missing": []}

    # ---- trading
    t, miss = out["trade_why"], out["trade_missing"]
    rr_target = (a.get("fib") or {}).get("trade_target") or a.get("target")
    # reward/risk AFTER trading costs (buy + sell ~2 x 1.12% on CSE): reward shrinks, risk grows
    cost = 2 * RULES["cost_per_side"] * close
    risk = (close - a["stop"]) + cost if a.get("stop") else None
    rr = (rr_target - close - cost) / risk if rr_target and risk and risk > 0 else None
    checks = [
        (tov >= 5_000_000, f"liquid (LKR {tov/1e6:.1f}M/day)", f"low liquidity (LKR {tov/1e6:.1f}M/day, need 5M)"),
        (a["tech_score"] >= 2, f"technical score {a['tech_score']}", f"technical score {a['tech_score']} (need 2+)"),
        (ind.get("sma50") is not None and close > ind["sma50"], "above 50-day average", "below 50-day average"),
        ((ind.get("macd_hist") or 0) > 0, "MACD momentum positive", "MACD momentum not positive"),
        (a.get("rsi") is not None and 40 <= a["rsi"] <= 70, f"RSI {a.get('rsi')} (room to run)", f"RSI {a.get('rsi')} (want 40-70)"),
        (rr is not None and round(rr, 1) >= 1.5, f"reward/risk {rr:.1f} after costs (target {rr_target:.2f}, stop {a.get('stop')})" if rr else "",
         f"reward/risk after costs {rr:.1f} (need 1.5)" if rr is not None else "no reward/risk"),
        ((a.get("fund_score") is None) or a["fund_score"] > -2, "fundamentals not negative", f"weak fundamentals ({a.get('fund_score')})"),
    ]
    for passed, yes, no in checks:
        (t if passed else miss).append(yes if passed else no)
    out["trade"] = not miss
    # one condition short (but liquid, uptrend and not fundamentally weak) -> worth watching
    out["trade_watch"] = len(miss) == 1 and tov >= 5_000_000 and close > (ind.get("sma50") or 1e18) \
        and ((a.get("fund_score") is None) or a["fund_score"] > -2)
    if rr is not None:
        out["reward_risk"] = round(rr, 2)
        out["trade_target"], out["trade_stop"] = round(rr_target, 2), a.get("stop")

    # ---- investment (verified figures only)
    w, miss = out["invest_why"], out["invest_missing"]
    pe = f.get("pe") if ok("pe") else None
    growth = (ok("eps_growth_ytd") and f["eps_growth_ytd"] > 0) or (ok("eps_cagr") and f["eps_cagr"] > 0) \
        or (ok("profit_growth") and f["profit_growth"] > 0)
    quality = (ok("roe") and f["roe"] >= 12) or (ok("pbv") and 0 < f["pbv"] <= 1.2)
    checks = [
        (pe is not None and 0 < pe <= 15, f"verified P/E {pe}", "no verified P/E 0-15" if pe is None else f"P/E {pe} (want ≤ 15)"),
        (growth, "earnings growing (verified)", "no verified earnings growth"),
        (quality, " / ".join(x for x in [f"ROE {f.get('roe')}%" if ok("roe") and f["roe"] >= 12 else "",
                                          f"P/BV {f.get('pbv')}" if ok("pbv") and 0 < f["pbv"] <= 1.2 else ""] if x) or "quality/value",
         "no verified ROE ≥ 12% or P/BV ≤ 1.2"),
        ((a.get("fund_score") or 0) >= 2, f"fundamental score {a.get('fund_score')}", f"fundamental score {a.get('fund_score')} (need 2+)"),
        (tov >= 1_000_000, "tradeable", f"very illiquid (LKR {tov/1e6:.2f}M/day)"),
    ]
    for passed, yes, no in checks:
        (w if passed else miss).append(yes if passed else no)
    out["invest"] = not miss
    if out["invest"]:
        if ok("div_yield") and f["div_yield"] >= 4:
            w.append(f"dividend yield {f['div_yield']}%")
        if ind.get("sma200") and close < ind["sma200"]:
            w.append("note: below 200-day average - consider buying in stages")
    return out


def advice(df: pd.DataFrame, fund: dict | None = None) -> dict | None:
    """Latest signal for one stock, with reasons and risk levels.

    `fund` is the output of fundamentals.metrics(); its points are added to the technical score.
    """
    if len(df) < 30:
        return None
    import fundamentals  # local import keeps analysis usable on its own
    d = indicators(df)
    r, prev = d.iloc[-1], d.iloc[-2]
    tech, why = _score_row(r, prev)
    fscore, fwhy = fundamentals.score(fund)
    score = tech + fscore
    out = {
        "date": d.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(r.close), 2),
        "change_pct": round(float((r.close / prev.close - 1) * 100), 2) if prev.close else None,
        "score": round(score, 1),
        "tech_score": round(tech, 1),
        "fund_score": round(fscore, 1) if fund else None,
        "signal": label(score),
        "reasons": why,
        "fund_reasons": fwhy,
        "fundamentals": {k: v for k, v in (fund or {}).items() if not k.startswith("_")} or None,
        "rsi": None if pd.isna(r.rsi) else round(float(r.rsi), 1),
        "stop": None, "target": None,
        "warnings": [],
        "fib": None,
        "ind": {"sma50": _f(r.sma50), "sma200": _f(r.sma200), "macd_hist": _f(r.macd_hist),
                "turnover_avg20": _f(r.turnover_avg20), "atr": _f(r.atr), "bars": len(d)},
    }
    if pd.notna(r.atr):
        out["stop"] = round(float(r.close - RULES["stop_atr"] * r.atr), 2)
        out["target"] = round(float(r.close + RULES["target_atr"] * r.atr), 2)
    if pd.notna(r.fib_hi):
        up = bool(r.fib_up)
        sw = fib_swing(d["high"].to_numpy(float), d["low"].to_numpy(float), len(d) - 1, RULES["fib_lookback"])
        lv = fib_levels(r.fib_hi, r.fib_lo, up)
        rng = r.fib_hi - r.fib_lo
        out["fib"] = {
            "trend": "up" if up else "down",
            "high": round(float(r.fib_hi), 2), "low": round(float(r.fib_lo), 2),
            "high_date": d.index[sw[2]].strftime("%Y-%m-%d"), "low_date": d.index[sw[3]].strftime("%Y-%m-%d"),
            "levels": {f"{k*100:.1f}": round(float(v), 2) for k, v in lv.items()},
            # extensions beyond the high (uptrend) - common profit targets
            "ext": {"127.2": round(float(r.fib_lo + 1.272 * rng), 2), "161.8": round(float(r.fib_lo + 1.618 * rng), 2)} if up else
                   {"127.2": round(float(r.fib_hi - 1.272 * rng), 2), "161.8": round(float(r.fib_hi - 1.618 * rng), 2)},
        }
        # trading target: first Fibonacci level / swing high / extension at least 1 ATR above the price
        atr_ = r.atr if pd.notna(r.atr) else 0
        cands = sorted(v for v in list(lv.values()) + [r.fib_hi] + list(out["fib"]["ext"].values()) if v >= r.close + atr_)
        if cands:
            out["fib"]["trade_target"] = round(float(cands[0]), 2)
        # nearest Fibonacci level above the price is the next resistance
        above = sorted(v for v in list(lv.values()) + [r.fib_hi] if v > r.close * 1.005)
        if above:
            out["fib"]["next_resistance"] = round(float(above[0]), 2)
        below = sorted((v for v in list(lv.values()) + [r.fib_lo] if v < r.close * 0.995), reverse=True)
        if below:
            out["fib"]["next_support"] = round(float(below[0]), 2)
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
