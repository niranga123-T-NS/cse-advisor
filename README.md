# CSE Advisor (web version)

A free website for the Colombo Stock Exchange. Every weekday at 14:45 a scheduled GitHub job fetches CSE prices, works out indicator signals (BUY/HOLD/SELL with reasons, stop and target levels, and a backtest), and publishes a dashboard you can open on any phone or computer.

> The signals are mechanical indicator rules. They are not financial advice.

Your site will be at: `https://<your-github-username>.github.io/cse-advisor/`

## One-time setup (about 10 minutes)

1. **Create a GitHub account** at github.com if you don't have one.
2. **Create a new repository** named `cse-advisor` and make it **Public**. The free plan only offers GitHub Pages on public repos. Only market data and your watchlist will be visible.
3. **Upload these files.** Easiest is from a computer: unzip, then **Add file → Upload files**, and drag in *everything inside* the `cse-advisor` folder, including the hidden `.github` folder. On a Mac, press Cmd+Shift+. in Finder to show hidden folders.
4. **Turn on Pages.** Go to **Settings → Pages → Source: GitHub Actions**.
5. **Run it the first time.** Go to **Actions → Update CSE data → Run workflow**. Leave the boxes empty, or tick *backfill all* to load every listed stock (takes 10–15 minutes). When the run turns green, open your site link.

From then on it updates itself every weekday after the market closes.

## Using it

- **Add stocks:** press **+ Add stocks** on the site. It opens the GitHub run page, where you can enter symbols such as `HAYL.N0000 CTC.N0000`. They're added to `watchlist.txt` and their history is loaded. You can also edit `watchlist.txt` directly on GitHub.
- **Stars** ☆ on the site are saved in that browser only. `watchlist.txt` sets which stocks get full history.
- Every stock that trades on a given day gets saved. Stocks outside your watchlist gain a signal once they have 30 days of saved data.

## Company reports (fundamentals)

Every night at 01:30 the **Read company reports** job checks CSE for new reports. For each company it reads the latest **10 quarterly** and **5 annual** report PDFs, using plain text matching (free, no API key). From them it works out:

- **EPS** for the last 12 months and **P/E**. A full year's EPS is only trusted when two sources agree, or when it comes from the annual report.
- **Net assets per share**, **P/BV** and **ROE**.
- **Profit and revenue growth** compared with the same period last year, and how consistent that growth has been.
- **Dividend per share** and **yield**, plus **EPS growth per year** across the annual reports.

Each figure is sanity-checked against the share price, and anything implausible is left blank rather than guessed. These points are added to the technical score; the stock page shows both separately, with links to every report used. The first full read of all companies takes 3–4 hours; after that, each night takes minutes.

Text matching can't read every company's layout. Some stocks will show "partial" or "low" data, so check the linked report before relying on a figure.

## If the run fails

Open the failed run in **Actions** and read the log.
- **"0 daily bars" or tradeSummary failed:** the unofficial CSE API may have changed, or it may block GitHub's servers. Run `python probe.py JKH.N0000` on your own computer to compare. If it works there but not on GitHub, the block is the cause; the local version below will still work.
- **Pages error:** check step 4.

## Running it on your own computer instead (optional)

```bash
pip install -r requirements.txt
python build_site.py --backfill JKH.N0000      # fetch and build
python app.py                                   # live dashboard at http://127.0.0.1:5000
```

Longer history from a TradingView CSV export: `python collector.py import-csv FILE.csv JKH.N0000`

## Tuning the signals

The scoring rules, buy/sell thresholds, stop and target multiples, and the ~1.12% per-side trading cost are all in `RULES` at the top of `analysis.py`. Edit them on GitHub and the next run uses them.

| Rule | Points |
|---|---|
| Price above / below 50-day average | +1 / −1 |
| 20-day average above / below 50-day | +1 / −1 |
| Above / below 200-day average | +0.5 / −0.5 |
| RSI < 30 / > 70 | +1 / −1 |
| MACD bullish / bearish crossover | +1.5 / −1.5 (otherwise ±0.5) |
| Below lower / above upper Bollinger band | +0.5 / −0.5 |
| Volume > 1.5× average on an up / down day | +0.5 / −0.5 |

A score of 1.5 or more is BUY (3 or more is STRONG BUY). A score of −1.5 or less is SELL.

## Files

| File | Purpose |
|---|---|
| `.github/workflows/update.yml` | The daily scheduled job |
| `build_site.py` | Fetches data and builds `site/data/*.json` |
| `.github/workflows/fundamentals.yml` | Nightly job that reads company reports |
| `reports.py` | Lists CSE report PDFs and reads figures out of them |
| `fundamentals.py` | Collects report figures, works out P/E, growth, etc. and fundamental points |
| `site/index.html` | The website |
| `cse_client.py` | Unofficial CSE API client (limited to 1 request/sec) |
| `collector.py` | Daily snapshot (skips holidays), history backfill, CSV import |
| `db.py` | Storage as CSV files in `data/` |
| `analysis.py` | Indicators, scoring, backtest |
| `app.py`, `templates/` | Optional local dashboard |
| `probe.py` | Shows raw CSE API responses for debugging |
