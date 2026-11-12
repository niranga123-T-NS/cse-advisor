"""Live prices: fetch every counter from CSE once a minute during market hours and publish them.

Runs as a GitHub Actions job (see .github/workflows/live.yml). Each minute it writes
  live.json      latest price, change %, volume, high/low for every traded counter
  intraday.json  today's minute-by-minute prices per counter
to a separate branch called `live` (one commit, overwritten each minute, so the repo doesn't grow).
The website reads those files from raw.githubusercontent.com every minute while the market is open.

    python live.py            # run until the market closes
    python live.py --once     # one snapshot (for testing)
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time

import cse_client as cse

COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
START, END = (9, 25), (14, 40)   # CSE trades 09:30-14:30


def now():
    return dt.datetime.now(COLOMBO)


def snapshot() -> list[dict]:
    raw = cse.post("tradeSummary") or {}
    if isinstance(raw, dict):
        rows = raw.get("reqTradeSummery") or raw.get("reqTradeSummary") or []
    else:
        rows = raw if isinstance(raw, list) else []
    out = []
    for r in rows:
        try:
            p = float(r.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not r.get("symbol") or p <= 0:
            continue
        num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None
        out.append({"s": r["symbol"], "p": p, "c": num("percentageChange"), "ch": num("change"),
                    "v": num("sharevolume") or num("shareVolume") or num("quantity"),
                    "to": num("turnover"), "h": num("high"), "l": num("low"), "o": num("open"),
                    "pc": num("previousClose")})
    return out


def git(*args, inp: str | None = None) -> str:
    return subprocess.run(["git", *args], input=inp, capture_output=True, text=True, check=True).stdout.strip()


def publish(files: dict[str, str], msg: str) -> bool:
    """Write files as a single commit on branch `live` and force-push it (no history kept)."""
    try:
        entries = []
        for name, content in files.items():
            sha = git("hash-object", "-w", "--stdin", inp=content)
            entries.append(f"100644 blob {sha}\t{name}")
        tree = git("mktree", inp="\n".join(entries) + "\n")
        commit = git("commit-tree", tree, "-m", msg)
        git("push", "-f", "origin", f"{commit}:refs/heads/live")
        return True
    except subprocess.CalledProcessError as e:
        print("publish failed:", e.stderr[-300:] if e.stderr else e)
        return False


def main(once=False):
    day = now().strftime("%Y-%m-%d")
    times: list[str] = []
    series: dict[str, list] = {}   # symbol -> [[minute_index, price], ...] (only when price changes)
    last_sig, unchanged = None, 0
    while True:
        t = now()
        if not once and (t.hour, t.minute) >= END:
            print("Market closed - stopping.")
            break
        rows = snapshot()
        if rows:
            hhmm = t.strftime("%H:%M")
            times.append(hhmm)
            i = len(times) - 1
            for r in rows:
                s = series.setdefault(r["s"], [])
                if not s or s[-1][1] != r["p"]:
                    s.append([i, r["p"]])
            status = cse.market_status() or ""
            live = {"updated": t.strftime("%Y-%m-%d %H:%M:%S"), "date": day, "market": status, "rows": rows}
            intraday = {"date": day, "t": times, "p": series}
            ok = publish({"live.json": json.dumps(live, separators=(",", ":")),
                          "intraday.json": json.dumps(intraday, separators=(",", ":"))}, f"live {hhmm}")
            print(f"{hhmm}  {len(rows)} counters  market={status!r}  published={ok}")
            # holiday / closed market: board never changes -> stop after 30 minutes past opening
            sig = hash(json.dumps(rows, sort_keys=True))
            unchanged = unchanged + 1 if sig == last_sig else 0
            last_sig = sig
            if not once and unchanged >= 30 and (t.hour, t.minute) >= (10, 0):
                print("No price changes for 30 minutes - market not trading today, stopping.")
                break
        else:
            print(t.strftime("%H:%M"), "no data from CSE")
        if once:
            break
        time.sleep(max(5, 60 - now().second))  # wake at the start of each minute


if __name__ == "__main__":
    main(once="--once" in sys.argv)
