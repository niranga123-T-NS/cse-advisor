"""Morning and closing summaries sent to your Telegram.

Setup (once): create a bot with @BotFather in Telegram, copy its token, then on GitHub open
Settings -> Secrets and variables -> Actions -> New repository secret, name TELEGRAM_TOKEN, paste the token.
Send /start to your new bot from your own Telegram. The first run finds your chat and remembers nothing else;
to pin it, add a second secret TELEGRAM_CHAT with the number the run prints.

  python telegram.py morning     before the open: the summary, what is worth a look, what to take care with, rates
  python telegram.py close       after the daily build: the market's day, biggest moves, rating changes, alerts, big trades
  python telegram.py test        a short test message
Nothing is sent, and the job still succeeds, when TELEGRAM_TOKEN is not set.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent
REPO = os.environ.get("GITHUB_REPOSITORY", "niranga123-T-NS/cse-advisor")
OWNER, NAME = REPO.split("/")
SITE = f"https://{OWNER.lower()}.github.io/{NAME}/"
LIVE = f"https://raw.githubusercontent.com/{REPO}/live/"
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
e = lambda s: html.escape(str(s), quote=False)
tk = lambda s: s.split(".")[0] + (".X" if ".X" in s else "")


def data(name: str):
    """A file of the site: the freshly built copy if this runs after a build, otherwise the published one."""
    p = ROOT / "site" / "data" / name
    if p.exists():
        return json.loads(p.read_text())
    try:
        r = requests.get(SITE + "data/" + name, timeout=30)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def live(name: str):
    try:
        r = requests.get(LIVE + name, timeout=30)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def chat_id(token: str) -> str | None:
    c = os.environ.get("TELEGRAM_CHAT", "").strip()
    if c:
        return c
    try:
        ups = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json().get("result", [])
    except (requests.RequestException, ValueError):
        return None
    chats = [u["message"]["chat"]["id"] for u in ups if (u.get("message") or {}).get("chat", {}).get("type") == "private"]
    if chats:
        print(f"Using the chat that last wrote to the bot: {chats[-1]}. Add it as the secret TELEGRAM_CHAT to pin it.")
        return str(chats[-1])
    print("No chat found: send /start to your bot in Telegram, then run this again.")
    return None


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_TOKEN is not set; nothing sent.\n---\n" + text)
        return False
    chat = chat_id(token)
    if not chat:
        return False
    parts, cur = [], ""
    for line in text.split("\n"):                       # Telegram allows 4096 characters a message
        if len(cur) + len(line) > 3800:
            parts.append(cur); cur = ""
        cur += line + "\n"
    parts.append(cur)
    ok = True
    for p in parts:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          data={"chat_id": chat, "text": p, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        print("Telegram:", r.status_code, r.text[:200] if not r.ok else "sent")
        ok = ok and r.ok
    return ok


def rates_line() -> str:
    m = data("macro.json")
    if not m or not m.get("latest"):
        return ""
    L, bits = m["latest"], []
    if L.get("tb364") is not None:
        bits.append(f"12-month T-bill {L['tb364']:.2f}%")
    if L.get("opr") is not None:
        bits.append(f"policy rate {L['opr']:.2f}%")
    if L.get("usd") is not None:
        bits.append(f"USD/LKR {L['usd']:.2f}")
    return ("Rates: " + ", ".join(bits) + ".") if bits else ""


def morning() -> str:
    b, now = data("brief.json") or {}, dt.datetime.now(COLOMBO)
    out = [f"<b>CSE Advisor, {now:%A %d %B}</b>", "Market opens 9:30, if today is a trading day.", ""]
    for p in (b.get("paras") or [])[:2]:
        out += [e(p), ""]
    if b.get("look"):
        out.append("<b>Worth a look</b>")
        out += [f"• <b>{e(tk(x['s']))}</b> {e(x['tag'])}: {e(x['why'])}" for x in b["look"][:5]]
        out.append("")
    if b.get("care"):
        out.append("<b>Take care</b>")
        out += [f"• <b>{e(tk(x['s']))}</b> {e(x['tag'])}: {e(x['why'])}" for x in b["care"][:3]]
        out.append("")
    r = rates_line()
    if r:
        out += [e(r), ""]
    out.append(f'<a href="{SITE}">Open CSE Advisor</a>. Readings by fixed rules, not financial advice.')
    return "\n".join(out)


def close() -> str:
    sig, M, ch = data("signals.json") or [], data("market.json") or {}, data("changes.json") or {}
    day = sig[0]["date"] if sig else dt.date.today().isoformat()
    out = [f"<b>CSE close, {dt.date.fromisoformat(day):%A %d %B}</b>"]
    od = M.get("official_daily") or []
    if len(od) >= 2 and od[-1][0] == day:
        a, b = od[-1][1], od[-2][1]
        out.append(f"ASPI {a:,.0f} ({(a / b - 1) * 100:+.2f}%).")
    traded = [x for x in sig if x.get("date") == day and x.get("change_pct") is not None]
    up, down = sum(x["change_pct"] > 0 for x in traded), sum(x["change_pct"] < 0 for x in traded)
    if traded:
        out.append(f"{up} shares rose and {down} fell.")
    busy = sorted([x for x in traded if (x.get("tov") or 0) >= 5e6], key=lambda x: -x["change_pct"])
    if busy:
        out += ["", "<b>Biggest moves among actively traded shares</b>",
                "Up: " + ", ".join(f"{e(tk(x['symbol']))} {x['change_pct']:+.2f}%" for x in busy[:3] if x["change_pct"] > 0),
                "Down: " + ", ".join(f"{e(tk(x['symbol']))} {x['change_pct']:+.2f}%" for x in busy[::-1][:3] if x["change_pct"] < 0)]
    C = ch.get("changes") or []
    if C:
        word = lambda g: (g or "").title()
        strong = [c for c in C if "STRONG" in (c["to"] or "")]
        other = [c for c in C if c not in strong]
        out += ["", f"<b>Rating changes</b> ({len(C)})"]
        out += [f"• <b>{e(tk(c['s']))}</b> now {word(c['to'])} (was {word(c['from'])})" for c in strong[:6]]
        if other:
            out.append("Also: " + ", ".join(f"{e(tk(c['s']))} {word(c['from'])} to {word(c['to'])}" for c in other[:8]) + (" and more" if len(other) > 8 else ""))
    al = live("alerts.json") or {}
    if al.get("date") == day:
        imp = [a for a in al.get("alerts", []) if a.get("v", 0) >= 2]
        out += ["", f"<b>Alerts</b>: {len(al.get('alerts', []))} today" + (f", {len(imp)} important:" if imp else ".")]
        out += [f"• {e(a['t'][:5])} {e(tk(a['s'])) if a.get('s') else 'Market'}: {e(a['m'])}" for a in imp[-5:]]
    od2 = live("orders.json") or {}
    if od2.get("date") == day and od2.get("big"):
        out += ["", "<b>Largest trades</b>"]
        for x in od2["big"][:3]:
            side = "crossing" if x.get("x") else "buying" if x.get("d", 0) > 0 else "selling" if x.get("d", 0) < 0 else "side unclear"
            out.append(f"• {e(tk(x['s']))} {x['q']:,.0f} at {x['p']:.2f} (LKR {x['v'] / 1e6:.1f}M, {side})")
    r = rates_line()
    if r:
        out += ["", e(r)]
    out += ["", f'<a href="{SITE}">Open CSE Advisor</a>. Readings by fixed rules, not financial advice.']
    return "\n".join(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    text = morning() if cmd == "morning" else close() if cmd == "close" else "CSE Advisor: the Telegram link works. You will get a summary before the open and after the close on trading days."
    send(text)
