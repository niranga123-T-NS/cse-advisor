"""Extract the statement lines Claude needs to read a company's key figures by hand.

For each symbol it takes, from the CSE report listing already saved in data/fundamentals/<SYM>.json:
  * the latest interim (quarterly) report,
  * the latest year-end interim report (the quarter ending at the financial year end), and
  * the latest annual report,
downloads them, and keeps only the pages and lines that matter (period headers, revenue, profit attributable
to owners, EPS, net assets per share, dividend per share), with page numbers.
Output: extracts/<SYM>.txt, pushed as one commit to the `extracts` branch.

    python extract.py JKH.N0000 COMB.N0000 ...
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import fundamentals as F
import reports

KEY = re.compile(r"revenue|turnover|gross income|interest income|net interest|written premium|profit|attributable|"
                 r"owners of|equity holders|earnings per|eps\b|net assets? (value )?per|dividends? per|"
                 r"months|quarter|year ended|period ended|rs\.?\s*['’]?\s*000|rs\.?\s*mn|lkr|group|company|bank|audited", re.I)
PAGE_HIT = re.compile(r"earnings\s*(/|\(loss\))?\s*per|net assets? (value )?per|dividends? per|"
                      r"profit attributable|attributable to|owners of the", re.I)
NUMS = re.compile(r"\d[\d,]*\.?\d*")


def pages_text(data: bytes, first: int, last: int) -> list[str]:
    return [reports.pdf_text(data, p, p) for p in range(first, last + 1)]


def extract_report(item: dict, kind: str, max_pages: int) -> str:
    data = reports.fetch_pdf(item["url"])
    if not data:
        return f"  (download failed: {item['url']})\n"
    n = reports.pdf_pages(data)
    rng = list(range(1, min(n, 25) + 1)) if kind != "annual" else \
        list(range(1, min(n, 40) + 1)) + list(range(max(41, n - 140), n + 1))
    out, used = [], 0
    for p in rng:
        t = reports.pdf_text(data, p, p)
        if not PAGE_HIT.search(t):
            continue
        lines = [re.sub(r"\s{3,}", "   ", l.rstrip()) for l in t.splitlines() if l.strip()]
        keep, prev_label = [], False
        for l in lines:
            if len(l) >= 260:
                continue
            if KEY.search(l):
                keep.append(l)
                prev_label = len(NUMS.findall(l)) < 2   # a label whose numbers are on the next line
            elif prev_label and len(NUMS.findall(l)) >= 2:
                keep.append(l)                         # keep those numbers
                prev_label = False
        if not keep:
            continue
        out.append(f"  --- page {p} ---")
        out.extend("  " + l for l in keep[:70])
        used += 1
        if used >= max_pages:
            break
    return "\n".join(out) + "\n" if out else "  (no statement pages found)\n"


def main(symbols: list[str]):
    files = {}
    for s in symbols:
        d = F.load(s)
        lst = d.get("listing") or reports.list_reports(s)
        qs, ans = lst.get("quarterly", []), lst.get("annual", [])
        fy_m = next((int(a["end"][5:7]) for a in ans if a.get("end") and not a.get("year_only")), 12)
        picks = []
        if qs:
            picks.append(("LATEST INTERIM", qs[0], "quarterly", 3))
            q4 = next((q for q in qs if q.get("end") and int(q["end"][5:7]) == fy_m), None)
            if q4 and q4 is not qs[0]:
                picks.append(("YEAR-END INTERIM", q4, "quarterly", 3))
        if ans:
            picks.append(("LATEST ANNUAL REPORT", ans[0], "annual", 4))
        parts = [f"##### {s}   shares in issue: {d.get('shares')}   other class: {d.get('shares_other_class')}\n"]
        for label, it, kind, mp in picks:
            print(s, label, it["end"])
            parts.append(f"=== {label}: {it['title']}  (period end {it['end']})\n  {it['url']}\n")
            parts.append(extract_report(it, kind, mp))
        files[f"extracts/{s}.txt"] = "".join(parts)
    # one commit on branch 'extracts' containing previous extracts plus these
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "+refs/heads/extracts:refs/remotes/origin/extracts"], check=True)
        old = subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/extracts"], capture_output=True, text=True).stdout.split()
        for name in old:
            if name not in files:
                files[name] = subprocess.run(["git", "show", f"origin/extracts:{name}"], capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        pass
    import live
    print("published:", live.publish_to(files, "extracts", f"extracts: {' '.join(symbols)[:200]}"))


EPS_LINE = re.compile(r"earnings\s*(/\s*\(?loss\)?\s*)?per|\(loss\)\s*per|loss\)?\s*per\s*(ordinary\s*)?share|\beps\b|basic|diluted", re.I)
HEAD_LINE = re.compile(r"months|quarter|period|year ended|ended|unaudited|audited|group|company|bank|consolidated|\b20\d\d\b|note", re.I)


def extract_eps(item: dict) -> str:
    """Only what is needed to read earnings per share: the column headers near each EPS row and the rows themselves."""
    data = reports.fetch_pdf(item["url"])
    if not data:
        return "  (download failed)\n"
    n = reports.pdf_pages(data)
    out, used = [], 0
    for p in range(1, min(n, 30) + 1):
        t = reports.pdf_text(data, p, p)
        if not EPS_LINE.search(t):
            continue
        lines = [re.sub(r"\s{3,}", "   ", l.rstrip()) for l in t.splitlines() if l.strip() and len(l) < 260]
        idx = [i for i, l in enumerate(lines) if EPS_LINE.search(l)]
        if not idx:
            continue
        heads = [l for l in lines[:40] if HEAD_LINE.search(l) and len(NUMS.findall(l)) <= 8][:10]
        keep = []
        for i in idx:
            keep.extend(lines[max(0, i - 1): i + 4])
        seen, rows = set(), []
        for l in heads + ["..."] + keep:
            if l not in seen:
                seen.add(l); rows.append(l)
        out.append(f"  --- page {p} ---")
        out.extend("  " + l for l in rows[:45])
        used += 1
        if used >= 3:
            break
    if not out:
        txt = reports.pdf_text(data, 1, min(n, 3))
        return f"  (no EPS row found; {n} pages; text on first pages: {len(txt.strip())} characters{' - probably scanned images' if len(txt.strip()) < 200 else ''})\n"
    return "\n".join(out) + "\n"


def gaps(symbols: list[str]):
    """Quarterly reports read without an EPS figure: extract their EPS rows to extracts/q/<SYM>.txt."""
    files = {}
    for s in symbols:
        d = F.load(s)
        miss = [(k, x) for k, x in (d.get("quarterly") or {}).items() if x.get("eps") is None]
        if not miss:
            continue
        parts = [f"##### {s}   shares in issue: {d.get('shares')}\n"]
        for k, x in sorted(miss, key=lambda t: t[1].get("end") or "", reverse=True):
            print(s, x.get("end"), flush=True)
            parts.append(f"=== id {k}  period end {x.get('end')}  {x.get('title', '')[:90]}\n  {x.get('url')}\n")
            parts.append(extract_eps({"url": x.get("url")}))
        files[f"extracts/q/{s}.txt"] = "".join(parts)
    import live
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "+refs/heads/extracts:refs/remotes/origin/extracts"], check=True)
        old = subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/extracts"], capture_output=True, text=True).stdout.split()
        for name in old:
            if name not in files:
                files[name] = subprocess.run(["git", "show", f"origin/extracts:{name}"], capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        pass
    print("published:", live.publish_to(files, "extracts", f"EPS gaps: {len(symbols)} companies"))


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:]]
    if args and args[0] == "--GAPS":
        gaps(args[1:])
    else:
        main(args)
