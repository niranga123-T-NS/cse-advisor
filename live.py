"""Live engine: reads every counter from CSE every 15 seconds while the market is open, looks for sudden
changes, and publishes prices + alerts for the website.

Each 15-second tick
  1. fetch the whole board (one request), plus sector indices / ASPI once a minute and a few order books
  2. compare every stock with its own recent ticks and with what is normal for it (from the daily history):
       jump / drop      price moved unusually fast (5 or 15 minutes)
       volume           far more shares traded than normal for this time of day
       high / low       new 1-year high or low
       breakout         through the 20-day high/low on heavy volume
       sma50            crossed its 50-day average
       gap              opened far from yesterday's close
       peer             moving against its own sector
       sector / market  an industry-group index or the ASPI moved a lot
       block            a large trade (from CSE's per-price trade totals, compared tick to tick)
       book             a large order appeared among the orders waiting to buy or sell
       hidden           signs of a hidden buyer or seller (see Engine._hidden); an inference, never a fact
  3. publish to the `live` branch (one commit, replaced each tick, so the repository does not grow):
       t/HHMMSS.json   every 15 s : prices, alerts, sector moves, order-book totals   (small)
       m/HHMM.json     every minute: full rows + today's minute-by-minute prices
     Each file has its own name because GitHub caches a file for 5 minutes; a new name is always fresh.

Reliability (GitHub's scheduler can be hours late)
  * the workflow is scheduled every hour through the night; a run that finds the open within reach waits for it
  * only one run handles a session (older run wins); a run that nears GitHub's 6-hour limit hands over
  * at the close it saves the day's 15-second tape to data/intraday/ and starts the daily update itself

    python live.py            # normal: wait for the open, run to the close
    python live.py --test     # a few ticks right now on branch live-test (works when the market is closed)
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

import cse_client as cse

COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
TICK = 15                                  # seconds
START, END = (9, 25), (14, 40)             # CSE trades 09:30-14:30
BUDGET_MIN = float(os.environ.get("LIVE_BUDGET_MIN", "325"))   # hand over before GitHub's 6-hour job limit
REACH_MIN = 320                            # only wait for an open that is at most this far away
ROOT = Path(__file__).parent
cse.MIN_INTERVAL_SEC = 0.5
BIG_MIN = 2_000_000                        # LKR: smallest trade kept for the "big orders" list
CROSS_MIN = 20_000_000                     # CSE's minimum value for a crossing (a block agreed between two parties)
EPS = 1e-6


def now() -> dt.datetime:
    return dt.datetime.now(COLOMBO)


def hm(t: dt.datetime) -> tuple[int, int]:
    return (t.hour, t.minute)


# ------------------------------------------------------------------ git / GitHub plumbing

def git(*args, inp: str | None = None) -> str:
    return subprocess.run(["git", *args], input=inp, capture_output=True, text=True, check=True).stdout.strip()


_blob_cache: dict[str, tuple[int, str]] = {}


def _blob(name: str, content: str) -> str:
    h = hash(content)
    hit = _blob_cache.get(name)
    if hit and hit[0] == h:
        return hit[1]
    sha = git("hash-object", "-w", "--stdin", inp=content)
    _blob_cache[name] = (h, sha)
    return sha


def publish_to(files: dict[str, str], branch: str, msg: str) -> bool:
    """Write files as a single commit on `branch` and force-push it (no history kept)."""
    try:
        def mktree(items: dict, prefix: str) -> str:
            entries = []
            for name, content in items.items():
                if isinstance(content, dict):
                    entries.append(f"040000 tree {mktree(content, prefix + name + '/')}\t{name}")
                else:
                    entries.append(f"100644 blob {_blob(prefix + name, content)}\t{name}")
            return git("mktree", inp="\n".join(entries) + "\n")
        nested: dict = {}
        for name, content in files.items():
            parts = name.split("/")
            node = nested
            for p_ in parts[:-1]:
                node = node.setdefault(p_, {})
            node[parts[-1]] = content
        commit = git("commit-tree", mktree(nested, ""), "-m", msg)
        git("push", "-f", "origin", f"{commit}:refs/heads/{branch}")
        return True
    except subprocess.CalledProcessError as e:
        print("publish failed:", (e.stderr or "")[-300:])
        return False


def publish(files: dict[str, str], msg: str) -> bool:
    return publish_to(files, "live", msg)


def gh(method: str, path: str, body: dict | None = None):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return None
    try:
        return requests.request(method, f"https://api.github.com/repos/{repo}/{path}", json=body, timeout=20,
                                headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        print("GitHub API error:", e)
        return None


def dispatch(workflow: str, inputs: dict | None = None) -> bool:
    r = gh("POST", f"actions/workflows/{workflow}/dispatches", {"ref": "main", "inputs": inputs or {}})
    ok = r is not None and r.status_code == 204
    print(f"started {workflow}: {ok}" + ("" if ok or r is None else f" ({r.status_code} {r.text[:120]})"))
    return ok


def older_run_active() -> bool:
    """Is an older run of this workflow already handling the session?  (The oldest run always wins.)"""
    me, skip = int(os.environ.get("GITHUB_RUN_ID") or 0), int(os.environ.get("TAKEOVER_FROM") or 0)
    if not me:
        return False
    for status in ("in_progress", "queued"):
        r = gh("GET", f"actions/workflows/live.yml/runs?status={status}&per_page=40")
        if r is not None and r.ok:
            if any(x["id"] < me and x["id"] != skip for x in r.json().get("workflow_runs", [])):
                return True
    return False


# ------------------------------------------------------------------ what is normal for each stock

def load_reference(day: str) -> tuple[dict, dict]:
    """Per-stock context from the saved daily bars (before `day`), and the latest pattern forecast."""
    import pandas as pd

    import db
    import sectors
    sec = sectors.load()
    ref = {}
    for s in db.symbols_with_data(5):
        d = db.load_bars(s)
        d = d[d.index < pd.Timestamp(day)]
        if len(d) < 5:
            continue
        c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
        prev = c.shift()
        tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        pc = float(c.iloc[-1])
        ref[s] = {"pc": pc, "atr": float(tr.tail(14).mean() / pc * 100) if pc else 2.0,
                  "v20": float(v.tail(20).mean()), "tov20": float((c * v).tail(20).mean()),
                  "hi": float(h.max()), "lo": float(l.min()), "n": int(len(d)),
                  "hi20": float(h.tail(20).max()), "lo20": float(l.tail(20).min()),
                  "sma50": float(c.tail(50).mean()) if len(d) >= 50 else None, "sector": sec.get(s, "")}
    fc = {}
    files = sorted((ROOT / "data" / "forecasts").glob("*.json"))
    if files:
        try:
            fc = json.loads(files[-1].read_text()).get("forecasts", {})
        except ValueError:
            pass
    return ref, fc


# ------------------------------------------------------------------ the alert engine

class Engine:
    def __init__(self, ref: dict, fc: dict, day: str):
        self.ref, self.fc, self.day = ref, fc, day
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=140))   # (ts, price) ~35 min of ticks
        self.alerts: list[dict] = []
        self.seen: dict[str, float] = {}
        self.opened: set[str] = set()
        self.last_p: dict[str, float] = {}
        self.sec_pct: dict[str, float] = {}      # median % change today of each sector's liquid stocks
        self.sec_n: dict[str, int] = {}
        self.sectors: dict[str, dict] = {}       # official indices
        self.aspi: dict | None = None
        self.ob: dict[str, list] = {}
        # large trades, orders waiting, and signs of hidden buying / selling
        self.lv: dict | None = None                # last per-price totals {symbol: {price: [shares, trades]}}
        self.dirn: dict[str, int] = {}             # which side moved the price last: +1 buyers, -1 sellers
        self.flow: dict[str, list] = {}            # symbol -> LKR [bought at rising prices, sold at falling prices, unclear]
        self.prints = collections.defaultdict(lambda: collections.deque(maxlen=800))   # (ts, price, shares, trades, side)
        self.big: list[dict] = []
        self.rest: list[dict] = []
        self.hidden: list[dict] = []
        self.book: dict[str, dict] = {}
        self.polled: dict[str, float] = {}
        self.side_code = {"bid": 1, "ask": 2}      # CSE's buySell codes ...
        self.side_votes = [0, 0]                   # ... checked against the data: [books that agree, books that say the opposite]
        self.refill: dict[tuple, dict] = {}        # (symbol, side) -> the best price being watched for refilling
        self.print_log = collections.defaultdict(list)
        # for the archive
        self.ticks: list[str] = []
        self.tape = collections.defaultdict(list)     # symbol -> [[tick index, price, cumulative volume], ...]
        self.ob_log: list[list] = []
        self.idx_log: list[list] = []

    # -- helpers
    def emit(self, ts: float, sym: str, kind: str, msg: str, sev: int = 1, key: str | None = None,
             cooldown: float | None = None):
        k = key or f"{sym}|{kind}"
        last = self.seen.get(k)
        if last is not None and (cooldown is None or ts - last < cooldown):
            return False
        self.seen[k] = ts
        self.alerts.append({"t": self._clock(ts), "s": sym, "k": kind, "m": msg, "v": sev})
        return True

    @staticmethod
    def _clock(ts: float) -> str:
        return dt.datetime.fromtimestamp(ts, COLOMBO).strftime("%H:%M:%S")

    @staticmethod
    def _session_fraction(ts: float) -> float:
        t = dt.datetime.fromtimestamp(ts, COLOMBO)
        mins = (t.hour * 60 + t.minute) - (9 * 60 + 30)
        return min(1.0, max(0.15, mins / 300))    # the open is busier than a straight line, so never below 15%

    def _price_ago(self, sym: str, ts: float, window: float) -> float | None:
        h = self.hist[sym]
        if not h or ts - h[0][0] < window * 0.7:      # not enough history to cover the window
            return None
        for t0, p0 in h:
            if t0 >= ts - window:
                return p0
        return None

    # -- one tick
    def tick(self, ts: float, rows: dict[str, dict]):
        i = len(self.ticks)
        self.ticks.append(dt.datetime.fromtimestamp(ts, COLOMBO).strftime("%H:%M:%S"))
        frac = self._session_fraction(ts)
        # sector medians first (needed for the peer comparison)
        by_sec = collections.defaultdict(list)
        for s, r in rows.items():
            f = self.ref.get(s)
            if f and f["sector"] and f["tov20"] >= 1_000_000 and r["c"] is not None:
                by_sec[f["sector"]].append(r["c"])
        self.sec_pct = {k: sorted(v)[len(v) // 2] for k, v in by_sec.items() if len(v) >= 4}
        self.sec_n = {k: len(v) for k, v in by_sec.items()}

        for s, r in rows.items():
            p, v, to, pct = r["p"], r["v"] or 0.0, r["to"] or 0.0, r["c"]
            tape = self.tape[s]
            if not tape or tape[-1][1] != p or tape[-1][2] != v:
                tape.append([i, p, v])
            f = self.ref.get(s)
            prev_p = self.last_p.get(s, f["pc"] if f else p)
            self.hist[s].append((ts, p))
            self.last_p[s] = p
            if not f or v <= 0:
                continue
            liquid = f["tov20"] >= 500_000 or to >= 2_000_000
            small_tick = p < 20                       # a 10-cent step is a big % for cheap shares
            atr = min(max(f["atr"], 0.5), 8.0)

            # opening gap
            if s not in self.opened:
                self.opened.add(s)
                o = r.get("o") or p
                gap = (o / f["pc"] - 1) * 100 if f["pc"] else 0
                if liquid and abs(gap) >= max(3.0, atr) and (not small_tick or abs(o - f["pc"]) >= 0.3 - 1e-9):
                    self.emit(ts, s, "gap", f"opened {abs(gap):.1f}% {'above' if gap > 0 else 'below'} yesterday's close "
                                            f"({f['pc']:.2f} → {o:.2f})", 2)
            if not liquid:
                continue

            # fast moves (only where there is enough trading for the move to be real, not a bid-ask bounce)
            active = f["tov20"] >= 2_000_000 or to >= 5_000_000
            for window, base, mult in ((300, 1.5, 0.6), (900, 2.5, 1.0)):
                old = self._price_ago(s, ts, window) if active else None
                if not old:
                    continue
                chg = (p / old - 1) * 100
                thr = max(base, mult * atr)
                if abs(chg) >= thr and (not small_tick or abs(p - old) >= 0.2 - 1e-9):
                    kind = "jump" if chg > 0 else "drop"
                    self.emit(ts, s, kind, f"{'up' if chg > 0 else 'down'} {abs(chg):.1f}% in {window // 60} minutes "
                                           f"({old:.2f} → {p:.2f})", 2 if abs(chg) >= 2 * thr else 1,
                              key=f"{s}|{kind}", cooldown=900)
                    break

            # volume far above normal for this time of day
            expected = f["v20"] * frac
            ratio = v / expected if expected > 0 else 0
            if to >= 1_000_000:
                for lvl in (10, 6, 3):
                    if ratio >= lvl:
                        if f"{s}|vol|{lvl}" not in self.seen:
                            self.emit(ts, s, "volume", f"volume {ratio:.1f}× normal for this time of day "
                                                       f"({v:,.0f} shares, LKR {to / 1e6:.1f}M)", 2 if lvl >= 6 else 1,
                                      key=f"{s}|vol|{lvl}")
                            for lower in (3, 6):
                                if lower < lvl:
                                    self.seen.setdefault(f"{s}|vol|{lower}", ts)
                        break

            # new highs / lows and breakouts
            span = "1-year" if f["n"] >= 200 else f"{f['n']}-day"
            if p > f["hi"] + 1e-9:
                self.emit(ts, s, "high", f"new {span} high at {p:.2f} (previous {f['hi']:.2f})", 2)
            elif p < f["lo"] - 1e-9:
                self.emit(ts, s, "low", f"new {span} low at {p:.2f} (previous {f['lo']:.2f})", 2)
            elif p > f["hi20"] + 1e-9 and ratio >= 1.5 and to >= 1_000_000:
                self.emit(ts, s, "breakout", f"broke above its 20-day high ({f['hi20']:.2f}) on {ratio:.1f}× volume", 1)
            elif p < f["lo20"] - 1e-9 and ratio >= 1.5 and to >= 1_000_000:
                self.emit(ts, s, "breakdown", f"fell below its 20-day low ({f['lo20']:.2f}) on {ratio:.1f}× volume", 1)

            # 50-day average
            m = f["sma50"]
            if m and active and abs(p - prev_p) > 0:
                if prev_p < m <= p:
                    self.emit(ts, s, "sma50", f"crossed above its 50-day average ({m:.2f})", 1, key=f"{s}|sma50|up")
                elif prev_p > m >= p:
                    self.emit(ts, s, "sma50", f"fell below its 50-day average ({m:.2f})", 1, key=f"{s}|sma50|down")

            # against its peers
            sp = self.sec_pct.get(f["sector"])
            if sp is not None and pct is not None and f["tov20"] >= 1_000_000:
                diff = pct - sp
                if abs(diff) >= max(3.0, 1.2 * atr) and (not small_tick or abs(p - f["pc"]) >= 0.3 - 1e-9):
                    self.emit(ts, s, "peer", f"{pct:+.1f}% today while {f['sector']} peers are {sp:+.1f}%", 1,
                              key=f"{s}|peer|{'up' if diff > 0 else 'down'}")

    def market(self, ts: float, sectors_now: dict | None, aspi: dict | None):
        i = len(self.ticks) - 1
        if sectors_now:
            self.sectors = sectors_now
            for name, d in sectors_now.items():
                pct = d.get("pct")
                if pct is None:
                    continue
                for lvl in (3.0, 1.5):
                    if abs(pct) >= lvl:
                        sign = "up" if pct > 0 else "down"
                        if f"SEC|{name}|{sign}|{lvl}" not in self.seen:
                            self.emit(ts, "", "sector", f"{name} index {pct:+.1f}% today", 2 if lvl >= 3 else 1,
                                      key=f"SEC|{name}|{sign}|{lvl}")
                            self.seen.setdefault(f"SEC|{name}|{sign}|1.5", ts)
                        break
            self.idx_log.append([i, {k: round(v["pct"], 2) for k, v in sectors_now.items() if v.get("pct") is not None}])
        if aspi and aspi.get("percentage") is not None:
            self.aspi = aspi
            pct = aspi["percentage"]
            for lvl in (3, 2, 1):
                if abs(pct) >= lvl:
                    sign = "up" if pct > 0 else "down"
                    if f"ASPI|{sign}|{lvl}" not in self.seen:
                        self.emit(ts, "", "market", f"ASPI {pct:+.2f}% today ({aspi.get('value', 0):,.0f})", 2,
                                  key=f"ASPI|{sign}|{lvl}")
                        for lower in (1, 2):
                            if lower < lvl:
                                self.seen.setdefault(f"ASPI|{sign}|{lower}", ts)
                    break

    # -- large trades and hidden buying / selling
    def trades(self, ts: float, levels: dict, rows: dict):
        """What traded since the last reading, price by price.  Call before tick() (it needs the previous prices).

        CSE publishes, for every counter, today's shares and number of trades at each price.  The difference
        between two readings is exactly what traded in between.  The SIDE is not published; it is estimated:
          * at or above the best sell price -> a buyer took it;  at or below the best buy price -> a seller hit it
          * otherwise the tick rule: above the previous price = buying, below = selling, same = as the last move.
        """
        first, prev_all = self.lv is None, self.lv or {}
        t, i = self._clock(ts), len(self.ticks)
        keep = {}
        for s, lv in levels.items():
            if s not in rows:                       # not traded today: these totals are from an earlier session
                continue
            keep[s] = lv
            old = prev_all.get(s)
            if old is not None and any(lv.get(px, (0.0, 0))[0] < q0[0] - EPS for px, q0 in old.items()):
                continue                            # totals went down (a correction by CSE): start again from here
            old = old or {}
            new = []
            for px, (q, n) in lv.items():
                q0, n0 = old.get(px, (0.0, 0))
                if q > q0 + EPS:
                    new.append((px, q - q0, max(1, n - n0)))
            if not new:
                continue
            f = self.ref.get(s)
            tov = f["tov20"] if f else 0.0
            if first:                               # started mid-session: only single trades are known exactly
                for px, dq, dn in new:
                    if dn == 1 and px * dq >= BIG_MIN:
                        self._big("", s, 0, dq, px * dq, 1, tov)
                continue
            last = self.last_p.get(s) or (f["pc"] if f else None) or new[0][0]
            bk = self.book.get(s)
            bid, ask = (bk.get("bid"), bk.get("ask")) if bk and ts - bk["ts"] <= 90 else (None, None)
            groups: dict[int, list] = {1: [], -1: [], 0: []}
            for px, dq, dn in sorted(new):
                if dn <= 2 and px * dq >= CROSS_MIN:          # a crossing: agreed between two parties, no pressure either way
                    side = 0
                elif ask and px >= ask[0] - EPS:
                    side = 1
                elif bid and px <= bid[0] + EPS:
                    side = -1
                elif px > last + EPS:
                    side = 1
                elif px < last - EPS:
                    side = -1
                else:
                    side = self.dirn.get(s, 0)
                groups[side].append((px, dq, dn))
                self.prints[s].append((ts, px, dq, dn, side))
                self.print_log[s].append([i, px, dq, dn, side])
            r = rows.get(s) or {}
            cur = r.get("p") or max(new, key=lambda x: x[1])[0]
            if cur > last + EPS:
                self.dirn[s] = 1
            elif cur < last - EPS:
                self.dirn[s] = -1
            fl = self.flow.setdefault(s, [0.0, 0.0, 0.0])
            for side, g in groups.items():
                if not g:
                    continue
                q, val, n = sum(x[1] for x in g), sum(x[0] * x[1] for x in g), sum(x[2] for x in g)
                fl[0 if side > 0 else 1 if side < 0 else 2] += val
                if val >= BIG_MIN:                  # several trades in one instant are usually one large order filling
                    self._big(t, s, side, q, val, n, tov, ts)
            if f:
                self._hidden(ts, s, f, cur)
        self.lv = keep

    def _big(self, t: str, s: str, side: int, q: float, val: float, n: int, tov: float, ts: float | None = None):
        cross = n <= 2 and val >= CROSS_MIN         # one or two huge trades: almost certainly a negotiated crossing
        e = {"t": t, "s": s, "d": 0 if cross else side, "q": round(q), "p": round(val / q, 2), "v": round(val), "n": n}
        if cross:
            e["x"] = 1
        if tov > 0:
            e["r"] = round(val / tov * 100)          # % of a normal day's turnover in this share
        self.big.append(e)
        if len(self.big) > 500:                      # keep the largest and the latest
            ids = {id(x) for x in sorted(self.big, key=lambda x: -x["v"])[:300]} | {id(x) for x in self.big[-150:]}
            self.big = [x for x in self.big if id(x) in ids]
        if ts is not None and val >= max(5_000_000, 0.1 * tov):
            word = "crossing (a block agreed between two parties)" if cross else \
                {1: "large buying", -1: "large selling", 0: "large trade"}[side]
            self.emit(ts, s, "block", f"{word}: {q:,.0f} shares at {val / q:.2f} (LKR {val / 1e6:.1f}M"
                                      + (f" in {n} trades" if n > 1 else "") + ")",
                      2 if val >= max(CROSS_MIN, 0.5 * tov) else 1, key=f"{s}|block|{side}", cooldown=180)

    def _flag(self, ts: float, s: str, side: int, kind: str, price: float | None, val: float, msg: str, big: bool = False):
        group = {"level": "lvl", "refill": "lvl", "absorb": "abs"}.get(kind, kind)
        if group == "abs" and ts - self.seen.get(f"{s}|hid|lvl|{side}", -1e18) < 1800:
            return                                   # already flagged more precisely, at a price
        if self.emit(ts, s, "hidden", msg, 2 if big else 1, key=f"{s}|hid|{group}|{side}", cooldown=1800):
            self.hidden.append({"t": self._clock(ts), "s": s, "d": side, "k": kind, "p": price, "v": round(val), "m": msg})

    def _hidden(self, ts: float, s: str, f: dict, cur: float):
        """Signs that someone is buying or selling more than the screen shows.  All of these are inferences.

        level    heavy trading keeps coming back to the lowest (highest) price of the last 30 minutes and it
                 does not break: somebody keeps buying (selling) there - what a hidden / reloading order looks like
        absorb   far more was sold at falling prices than bought at rising ones, yet the price is not lower
                 (or the mirror image): the pressure is being absorbed by the other side
        slices   one trade size repeated again and again: a large order being worked in equal pieces
        (refill, from the order book, is in _book)
        """
        P = self.prints[s]
        thr = max(3_000_000, 0.1 * f["tov20"])
        W = [x for x in P if x[0] >= ts - 1800]
        if len(W) >= 5:
            tot = sum(x[1] * x[2] for x in W)
            prices = {round(x[1], 4) for x in W}
            lo, hi = min(prices), max(prices)
            need = 0.6 if len(prices) <= 2 else 0.4       # between just two prices, half the volume at each is normal
            if hi > lo + EPS:
                for level, side in ((lo, 1), (hi, -1)):
                    hits = [x for x in W if abs(x[1] - level) < EPS]
                    val, bursts = sum(x[1] * x[2] for x in hits), len({x[0] for x in hits})
                    mins = (hits[-1][0] - hits[0][0]) / 60
                    if bursts >= 5 and mins >= 5 and ts - hits[-1][0] <= 300 and val >= thr and val >= need * tot:
                        sh = sum(x[2] for x in hits)
                        self._flag(ts, s, side, "level", level, val,
                                   f"possible hidden {'buyer' if side > 0 else 'seller'} at {level:.2f}: {sh:,.0f} shares "
                                   f"traded there in {bursts} bursts over {mins:.0f} min without the price going "
                                   f"{'lower' if side > 0 else 'higher'}", val >= 3 * thr)
            buy = sum(x[1] * x[2] for x in W if x[4] > 0)
            sell = sum(x[1] * x[2] for x in W if x[4] < 0)
            start = W[0][1]
            if sell >= thr and sell >= 2 * buy and cur >= start - EPS:
                self._flag(ts, s, 1, "absorb", None, sell,
                           f"possible hidden buying: LKR {sell / 1e6:.1f}M was sold at falling prices in 30 min, yet the "
                           f"price is not lower ({start:.2f} → {cur:.2f})", sell >= 3 * thr)
            elif buy >= thr and buy >= 2 * sell and cur <= start + EPS:
                self._flag(ts, s, -1, "absorb", None, buy,
                           f"possible hidden selling: LKR {buy / 1e6:.1f}M was bought at rising prices in 30 min, yet the "
                           f"price is not higher ({start:.2f} → {cur:.2f})", buy >= 3 * thr)
        H = [x for x in P if x[0] >= ts - 3600 and x[3] == 1]
        if len(H) >= 5:
            q, k = collections.Counter(round(x[2]) for x in H).most_common(1)[0]
            val = q * cur * k
            if k >= 5 and q * cur >= 250_000 and val >= thr / 2:
                votes = sum(x[4] for x in H if round(x[2]) == q)
                side = 1 if votes >= 0.6 * k else -1 if votes <= -0.6 * k else 0
                self._flag(ts, s, side, "slices", None, val,
                           f"possible large {'buy ' if side > 0 else 'sell ' if side < 0 else ''}order worked in slices: "
                           f"{q:,} shares traded {k} times in the last hour")

    # -- orders waiting in the book
    def order_books(self, ts: float, rows: dict, deadline: float, most: int = 6):
        """Refresh a few order books each tick, least recently checked first: shares that just alerted, today's
        most traded, and the usual most traded.  CSE gives one share per request, so this is a rotation."""
        recent = [a["s"] for a in reversed(self.alerts[-30:]) if a["s"]]
        active = sorted((x for x in rows if rows[x].get("to")), key=lambda x: -rows[x]["to"])[:30]
        top = sorted(self.ref, key=lambda x: -self.ref[x]["tov20"])[:12]
        want = list(dict.fromkeys(recent[:10] + active + top))
        want.sort(key=lambda x: self.polled.get(x, 0.0))
        t = self._clock(ts)
        for k, s in enumerate(want[:most]):
            if k and time.time() > deadline:
                break
            self.polled[s] = ts
            ob = cse.order_book(s)
            if ob:
                self._book(ts, t, s, ob, rows.get(s) or {})

    def _book(self, ts: float, t: str, s: str, ob: dict, r: dict):
        f = self.ref.get(s) or {}
        tov = f.get("tov20", 0.0)
        px = r.get("p") or self.last_p.get(s) or f.get("pc") or ob.get("best") or 0.0
        by: dict = {}
        for lv in ob.get("levels") or []:
            by.setdefault(lv["side"], lv)                 # the first (best) level of each side
        c = self.side_code
        b, a = by.get(c["bid"]), by.get(c["ask"])
        if b and a and abs(b["p"] - a["p"]) > EPS:        # a buy price is never above a sell price
            self.side_votes[0 if b["p"] < a["p"] else 1] += 1
            agree, against = self.side_votes
            if against > agree:                           # most books say the codes are the other way round
                c["bid"], c["ask"] = c["ask"], c["bid"]
                self.side_votes = [against, agree]
                b, a = a, b
                print("order book: buySell codes are the other way round ->", c)
            elif b["p"] > a["p"]:                         # a one-off crossed book: do not trust its sides
                b = a = None
        cur = {"ts": ts, "t": t, "bids": ob["bids"], "asks": ob["asks"],
               "bid": [b["p"], b["q"], b["n"]] if b else None, "ask": [a["p"], a["q"], a["n"]] if a else None}
        prev = self.book.get(s)
        thr = max(5_000_000, 0.1 * tov)
        if prev and ts - prev["ts"] <= 600 and px:
            for key, side in (("bids", 1), ("asks", -1)):       # many more shares waiting than a few minutes ago
                d = cur[key] - prev[key]
                if d * px >= thr and d >= 0.2 * max(prev[key], 1.0):
                    self._rest(ts, t, s, side, d, px, "new")
        for key, side in (("bid", 1), ("ask", -1)):             # a best price that keeps refilling as shares trade into it
            lv, rf = cur.get(key), self.refill.get((s, key))
            if not lv:
                self.refill.pop((s, key), None)
            elif not rf or abs(rf["p"] - lv[0]) > EPS:
                self.refill[(s, key)] = {"p": lv[0], "max": lv[1], "since": ts}
            else:
                rf["max"] = max(rf["max"], lv[1])
                hit = sum(x[2] for x in self.prints[s] if x[0] > rf["since"] and abs(x[1] - rf["p"]) < EPS)
                if hit >= 3 * rf["max"] and hit * rf["p"] >= thr / 2:
                    self._flag(ts, s, side, "refill", rf["p"], hit * rf["p"],
                               f"possible hidden {'buy' if side > 0 else 'sell'} order at {rf['p']:.2f}: at most "
                               f"{rf['max']:,.0f} shares were showing, yet {hit:,.0f} traded there in "
                               f"{(ts - rf['since']) / 60:.0f} min and {lv[1]:,.0f} still show", hit * rf["p"] >= 2 * thr)
        for key, side in (("bid", 1), ("ask", -1)):             # a very large order at the best price
            lv = cur.get(key)
            if lv and lv[0] * lv[1] >= max(10_000_000, 0.2 * tov):
                self._rest(ts, t, s, side, lv[1], lv[0], "best", lv[2])
        self.book[s] = cur
        self.ob[s] = [ob["bids"], ob["asks"], cur["bid"][0] if cur["bid"] else None, t,
                      cur["ask"][0] if cur["ask"] else None]
        self.ob_log.append([len(self.ticks) - 1, s, ob["bids"], ob["asks"], cur["bid"], cur["ask"]])

    def _rest(self, ts: float, t: str, s: str, side: int, q: float, px: float, kind: str, n: int = 0):
        val, what = q * px, "buy" if side > 0 else "sell"
        if kind == "best":
            key, cool = f"{s}|rest|best|{side}|{px:.2f}", 1800
            msg = (f"large {what} order waiting at the best price: {q:,.0f} shares at {px:.2f} (LKR {val / 1e6:.1f}M"
                   + (f", {n} order{'' if n == 1 else 's'}" if n else "") + ")")
        else:
            key, cool = f"{s}|rest|new|{side}", 600
            msg = f"new {what} orders placed: about {q:,.0f} more shares waiting to {what} (≈ LKR {val / 1e6:.1f}M)"
        if self.emit(ts, s, "book", msg, 2 if val >= CROSS_MIN else 1, key=key, cooldown=cool):
            e = {"t": t, "s": s, "d": side, "q": round(q), "p": round(px, 2), "v": round(val), "k": kind}
            if n:
                e["n"] = n
            self.rest.append(e)
            del self.rest[:-300]

    def orders_view(self, n: int = 10) -> dict:
        """The 'big orders' panel: largest trades, largest orders seen waiting, latest hidden-pattern flags, and
        today's money flow for the shares with the most of it."""
        fl = sorted(self.flow.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:40 if n <= 10 else 300]
        return {"big": sorted(self.big, key=lambda x: -x["v"])[:n], "rest": sorted(self.rest, key=lambda x: -x["v"])[:n],
                "hid": self.hidden[-n:][::-1], "nb": len(self.big), "nr": len(self.rest), "nh": len(self.hidden),
                "fl": {s: [round(v[0] / 1e6, 2), round(v[1] / 1e6, 2)] for s, v in fl if v[0] + v[1] >= 1e6}}

    # -- persistence
    def state(self) -> dict:
        return {"date": self.day, "alerts": self.alerts, "seen": self.seen, "opened": sorted(self.opened),
                "big": self.big, "rest": self.rest, "hidden": self.hidden, "flow": self.flow, "dirn": self.dirn,
                "lv": None if self.lv is None else {s: [[px, q, n] for px, (q, n) in lv.items()] for s, lv in self.lv.items()}}

    def restore(self, st: dict):
        if st.get("date") != self.day:
            return
        self.alerts, self.seen = st.get("alerts", []), st.get("seen", {})
        self.opened = set(st.get("opened", []))
        self.big, self.rest, self.hidden = st.get("big", []), st.get("rest", []), st.get("hidden", [])
        self.flow, self.dirn = st.get("flow", {}), st.get("dirn", {})
        if st.get("lv") is not None:
            self.lv = {s: {px: [q, n] for px, q, n in lv} for s, lv in st["lv"].items()}
        print(f"Resumed {len(self.alerts)} alerts and {len(self.big)} large trades from earlier today")

    def archive(self) -> Path | None:
        """Save this run's 15-second tape to data/intraday/ on main (raw material for intraday pattern models)."""
        if len(self.ticks) < 8:
            return None
        out = {"date": self.day, "tick_seconds": TICK, "ticks": self.ticks, "tape": self.tape, "alerts": self.alerts,
               "order_books": self.ob_log, "indices": self.idx_log,
               "big": self.big, "resting": self.rest, "hidden": self.hidden, "flow": self.flow, "prints": self.print_log}
        d = ROOT / "data" / "intraday"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.day}-{self.ticks[0][:5].replace(':', '')}.json.gz"
        with gzip.open(p, "wt") as f:
            json.dump(out, f, separators=(",", ":"))
        try:
            git("add", str(p))
            git("commit", "-m", f"Intraday tape {self.day} from {self.ticks[0]}")
            for _ in range(4):
                try:
                    git("pull", "--rebase", "-X", "theirs", "origin", "main")
                    git("push", "origin", "HEAD:main")
                    print("archived", p.name)
                    break
                except subprocess.CalledProcessError:
                    time.sleep(5)
        except subprocess.CalledProcessError as e:
            print("archive commit failed:", (e.stderr or "")[-200:])
        return p


# ------------------------------------------------------------------ fetching

def board() -> tuple[dict[str, dict], str | None]:
    """Rows for counters that have traded in the latest session, and that session's date."""
    raw = cse.trade_summary()
    dates = [cse.colombo_date(r["last_traded"]) for r in raw if r.get("last_traded")]
    session = max(dates) if dates else None
    rows = {}
    for r in raw:
        if session and cse.colombo_date(r.get("last_traded")) != session:
            continue
        pct = r["change_pct"]
        rows[r["symbol"]] = {"p": r["close"], "c": None if pct is None else round(pct, 2), "ch": r.get("change"),
                             "v": r["volume"], "to": r["turnover"], "h": r["high"], "l": r["low"], "o": r["open"],
                             "pc": r.get("prev_close"), "n": r.get("trades")}
    return rows, session


def resume(branch: str, day: str):
    """Pick up today's minute series and alerts if a previous run already published some."""
    times, series, state = [], {}, {}
    try:
        git("fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        old = json.loads(git("show", f"origin/{branch}:intraday.json"))
        if old.get("date") == day:
            times, series = old["t"], old["p"]
            print(f"Resuming today's minute series ({len(times)} minutes so far)")
        state = json.loads(git("show", f"origin/{branch}:state.json"))
    except (subprocess.CalledProcessError, ValueError, KeyError):
        pass
    return times, series, state


# ------------------------------------------------------------------ main loop

def main(test: bool = False):
    t_start = time.time()
    branch = "live-test" if test else "live"
    n = now()
    if not test:
        if n.weekday() >= 5 or hm(n) >= END:
            print("Outside trading hours - nothing to do.")
            return
        open_at = n.replace(hour=START[0], minute=START[1], second=0, microsecond=0)
        if (open_at - n).total_seconds() / 60 > REACH_MIN:
            print("The open is too far away - a later scheduled run will take the session.")
            return
        if older_run_active():
            print("An older run is already handling today's session - standing down.")
            return
    day = n.strftime("%Y-%m-%d")
    ref, fc = load_reference(day)
    print(f"Reference data for {len(ref)} stocks; {len(fc)} forecasts")

    def hand_over(eng: Engine | None):
        if eng:
            eng.archive()
        dispatch("live.yml", {"takeover": os.environ.get("GITHUB_RUN_ID", "")})

    # wait for the open
    last_check = time.time()
    while not test and hm(now()) < START:
        if (time.time() - t_start) / 60 > BUDGET_MIN:
            return hand_over(None)
        if time.time() - last_check > 300:
            last_check = time.time()
            if older_run_active():
                print("An older run is handling the session - standing down.")
                return
        time.sleep(20)

    eng = Engine(ref, fc, day)
    times, series, state = resume(branch, day)
    eng.restore(state)
    recent_ticks: dict[str, str] = {}
    recent_mins: dict[str, str] = {}
    base_files: dict[str, str] = {}
    fails, tick_no, traded_today, status = 0, 0, False, ""
    max_ticks = int(os.environ.get("LIVE_TEST_TICKS", "8")) if test else 10 ** 9
    while tick_no < max_ticks:
        t = now()
        if not test and hm(t) >= END:
            print("Market closed - stopping.")
            break
        if not test and (time.time() - t_start) / 60 > BUDGET_MIN:
            print("Near the job time limit - handing over to a fresh run.")
            return hand_over(eng)
        if not test and tick_no and tick_no % 20 == 0 and older_run_active():
            print("An older run is handling the session - standing down.")
            eng.archive()
            return
        ts = time.time()
        try:
            rows, session = board()
        except Exception as e:                      # never let one bad response stop the session
            print("board error:", e)
            rows, session = {}, None
        on_minute = t.second < TICK
        if rows and (session == day or test):
            traded_today = True
            try:                                    # before tick(): it compares with the previous prices
                levels = cse.detailed_trades()
                if levels:
                    eng.trades(ts, levels, rows)
            except Exception as e:
                print("trades error:", repr(e))
            eng.tick(ts, rows)
            if on_minute or tick_no == 0:
                try:
                    status = cse.market_status() or status
                    eng.market(ts, cse.sector_indices(), cse.aspi())
                except Exception as e:
                    print("index error:", e)
            try:
                eng.order_books(ts, rows, deadline=ts + 9.0)
            except Exception as e:
                print("order book error:", repr(e))
            stamp = t.strftime("%Y-%m-%d %H:%M:%S")
            tick = {"t": stamp, "m": status, "n": len(eng.alerts), "a": eng.alerts[-40:][::-1],
                    "r": {s: [r["p"], r["c"], r["v"]] for s, r in rows.items()},
                    "x": {"aspi": [eng.aspi.get("value"), round(eng.aspi.get("percentage") or 0, 2)] if eng.aspi else None,
                          "sec": {k: round(v["pct"], 2) for k, v in eng.sectors.items() if v.get("pct") is not None},
                          "peers": {k: round(v, 2) for k, v in eng.sec_pct.items()}},
                    "ob": eng.ob, "bg": eng.orders_view(10)}
            recent_ticks[t.strftime("%H%M") + f"{(t.second // TICK) * TICK:02d}"] = json.dumps(tick, separators=(",", ":"))
            for k in sorted(recent_ticks)[:-12]:
                del recent_ticks[k]
            if on_minute or tick_no == 0:
                hhmm = t.strftime("%H:%M")
                if not times or times[-1] != hhmm:
                    times.append(hhmm)
                i = len(times) - 1
                for s, r in rows.items():
                    sr = series.setdefault(s, [])
                    if not sr or sr[-1][1] != r["p"]:
                        sr.append([i, r["p"]])
                full = [{"s": s, **r} for s, r in rows.items()]
                live = {"updated": stamp, "date": day, "market": status, "rows": full}
                intraday = {"date": day, "t": times, "p": series}
                orders = {"date": day, "updated": stamp, **eng.orders_view(60)}
                recent_mins[t.strftime("%H%M")] = json.dumps({**live, "intraday": intraday, "orders": orders}, separators=(",", ":"))
                for k in sorted(recent_mins)[:-6]:
                    del recent_mins[k]
                base_files = {"live.json": json.dumps(live, separators=(",", ":")),
                              "intraday.json": json.dumps(intraday, separators=(",", ":")),
                              "state.json": json.dumps(eng.state(), separators=(",", ":")),
                              "alerts.json": json.dumps({"date": day, "alerts": eng.alerts}, separators=(",", ":")),
                              "orders.json": json.dumps(orders, separators=(",", ":"))}
            # when pushes keep failing, fall back to once a minute
            if fails < 3 or on_minute:
                files = dict(base_files)
                files.update({f"t/{k}.json": v for k, v in recent_ticks.items()})
                files.update({f"m/{k}.json": v for k, v in recent_mins.items()})
                ok = publish_to(files, branch, f"live {stamp[11:]}")
                fails = 0 if ok else fails + 1
                if on_minute or not ok or test:
                    print(f"{stamp[11:]}  {len(rows)} counters  {len(eng.alerts)} alerts  market={status!r}  published={ok}"
                          f"  tick took {time.time() - ts:.1f}s")
        else:
            if on_minute:
                print(t.strftime("%H:%M"), "no trades yet today" if rows else "no data from CSE")
            if not test and hm(t) >= (10, 15) and not traded_today:
                print("No trades by 10:15 - the market is not trading today. Stopping.")
                return
        tick_no += 1
        time.sleep(max(1.0, TICK - (time.time() % TICK)))      # wake on the next :00/:15/:30/:45

    if test:    # self-check of the pieces the real run relies on: seeing other runs, and starting a workflow
        v = eng.orders_view(5)
        print("per-price totals:", 0 if eng.lv is None else len(eng.lv), "counters,",
              0 if eng.lv is None else sum(len(x) for x in eng.lv.values()), "price levels")
        print("largest single trades of the last session:", json.dumps(v["big"]))
        print("order books read:", json.dumps({k: {x: b[x] for x in ("bids", "asks", "bid", "ask")} for k, b in list(eng.book.items())[:8]}))
        print("self-check: older run active =", older_run_active(), "| can start workflows =", dispatch("publish.yml"))
        return
    if traded_today:
        eng.archive()
        dispatch("update.yml")


if __name__ == "__main__":
    main(test="--test" in sys.argv)
