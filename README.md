# CSE Advisor (web version)

A free website for the Colombo Stock Exchange. Every weekday, right after the close, a GitHub job fetches CSE prices, works out indicator signals (BUY/HOLD/SELL with reasons, stop and target levels, and a backtest), and publishes a dashboard you can open on any phone or computer.

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

## Trading vs investment picks

Each stock page has a "Trading or investing?" card, and the list filters by **📈 Investment picks**, **⚡ Trading picks** and **👀 Trade watch**.

- **TRADE** (short-term setup, weeks) — every condition must pass:
  - average turnover of at least LKR 5M a day
  - technical score of 2 or more
  - price above its 50-day average
  - MACD momentum positive
  - RSI between 40 and 70
  - fundamental score above −2
  - **reward/risk of at least 1.5 after ~2.24% round-trip costs**, where the target is the first Fibonacci level or extension at least one ATR above the price, and the stop is 2×ATR below
- **WATCH**: liquid, in an uptrend, and only one condition short.
- **INVEST** (long term) — **verified** report figures only:
  - P/E between 0 and 15
  - earnings growing (year-to-date EPS, the EPS trend or profit)
  - ROE of at least 12% or P/BV of 1.2 or less
  - fundamental score of 2 or more
  - average turnover of at least LKR 1M a day

The labels refresh after each day's close.

## Live prices and alerts (every 15 seconds)

While the market is open the **Live prices** job reads every counter from CSE every 15 seconds and publishes the result for the site (on a separate branch called `live`, one commit that is replaced each time). The site refreshes every 15 seconds and shows how old its data is; it runs 25–40 seconds behind the exchange feed (GitHub needs that long to serve a newly published file reliably).

Each tick the job compares every share with its own recent ticks and with what is normal for it, and raises an **alert** when something changes suddenly:

| Alert | Meaning |
|---|---|
| ▲ jump / ▼ drop | moved unusually fast in 5 or 15 minutes (threshold scales with the share's normal daily range) |
| ◉ volume | 3×, 6× or 10× the normal volume for this time of day |
| ★ high / ☆ low | new 1-year high or low |
| ↗ breakout / ↘ breakdown | through the 20-day high or low on heavy volume |
| ~ 50-day | crossed its 50-day average |
| ⇅ gap | opened 3%+ away from yesterday's close |
| ≠ peer | moving 3+ points against its own sector |
| ▦ sector / ▣ market | an industry-group index moved 1.5%+ or the ASPI 1%+ |
| ■ block | a large trade (LKR 5M+ and 10%+ of the share's normal daily turnover) |
| ≡ book | a large order appeared among the orders waiting to buy or sell |
| ◐ hidden | signs of a hidden buyer or seller (an inference, see below) |

Open **🔔 Alerts** on the site to see them (filters: All, Important, ★ Favourites). Alerts describe what just happened; they are not predictions. In thin shares a single trade can trigger one. The job also records order-book totals (shares waiting to buy and to sell) for the most active shares, shown on the share's page.

At the close the job saves the whole day's 15-second tape to `data/intraday/` (the raw material for testing intraday patterns later) and starts **Update CSE data** itself.

**Scheduling.** GitHub's scheduler ran this repo's jobs 5–8 hours late, so the job no longer depends on one start time. It is scheduled every hour through the night and morning; the first run that finds the open within reach waits for it, later runs stand down, and a run that nears GitHub's 6-hour limit hands over to a fresh one. If live prices are ever missing, run **Actions → Live prices → Run workflow**.

## Big orders and hidden buying or selling

CSE does not publish individual orders or who is behind them. It does publish, for every counter, today's shares and number of trades **at each price**, and for one counter at a time the **totals waiting to buy and to sell** plus the best price. The live job reads the first every 15 seconds for the whole market and the second in rotation for the 30 or so most traded shares, and works out the rest:

- **Trades** (the 10 largest today). The change in the per-price totals between two readings is exactly what traded in between. Several trades in the same instant are counted as one, because a large order usually fills against many small ones. Whether it was *buying* or *selling* is an estimate: at or above the best sell price = a buyer took it, at or below the best buy price = a seller hit it, otherwise by whether the price moved up or down. One or two trades worth LKR 20M+ are marked as a **crossing** (a block agreed between two parties; no pressure either way).
- **Waiting.** A jump in the shares waiting to buy or sell (LKR 5M+ and 20%+ more than a few minutes earlier), or a very large order sitting at the best price.
- **Hidden?** Inferences only:
  - *price level*: heavy trading keeps returning to the lowest (or highest) price of the last 30 minutes, in 5+ separate bursts, and the price does not break it;
  - *refill*: more shares have traded at the best price than were ever shown there, and it is still showing;
  - *absorbed*: twice as much was sold at falling prices as bought at rising ones (or the reverse), yet the price has not moved that way;
  - *slices*: the same trade size repeated 5+ times in an hour.

Each share's page has a **Buying and selling pressure** card: a 20-day reading from daily prices and volumes (money flow and up-day versus down-day volume, compared with what the price did), and during market hours today's buying and selling, large trades and hidden-order signs for that share. The list can be filtered by **Quiet buying** and **Quiet selling**. What followed those daily readings in the past year is measured on the Accuracy page (`python pressure.py`); the intraday signs are saved with each day's tape (`data/intraday/`) so they can be scored once there are a few weeks of them. The thresholds are in `live.py` (`BIG_MIN`, `CROSS_MIN`, `Engine._hidden`, `Engine._book`) and `pressure.py`.

## Peers

Every share has its industry group (from CSE's company profiles, `data/sectors.csv`). The share's page compares it with its peers: P/E, price/book, ROE, 1- and 3-month performance and score against the sector median, its rank, and the most traded peers with their live moves.

## Pattern forecast and its accuracy

`forecast.py` trains a gradient-boosted model on ~60 pattern measures from daily prices and volumes and estimates, for every tradeable share, the chance of a gain **after costs** over the next 5 trading days. It is re-trained and re-tested after every close.

The test is strict: a walk-forward test in which each day is scored by a model built only from earlier days, with 2.24% round-trip costs taken off. **On the first test the forecast did not beat trading costs** (see **🎯 Accuracy** on the site for the current numbers, month by month). Every published forecast is also saved and scored once its 5 days are up, so the page builds a live record that cannot be adjusted afterwards. Treat the forecast as information, not as a trading signal, unless and until that page shows a proven edge.

## Company reports (fundamentals)

Every night at 01:30 the **Read company reports** job checks CSE for new reports. For each company it reads the latest **10 quarterly** and **5 annual** report PDFs, using plain text matching (free, no API key). From them it works out:

- **EPS** for the last 12 months and **P/E**. A full year's EPS is only trusted when two sources agree, or when it comes from the annual report.
- **Net assets per share**, **P/BV** and **ROE**.
- **Profit and revenue growth** compared with the same period last year, and how consistent that growth has been.
- **Dividend per share** and **yield**, plus **EPS growth per year** across the annual reports.

Each figure is sanity-checked against the share price, and anything implausible is left blank rather than guessed. These points are added to the technical score; the stock page shows both separately, with links to every report used. The first full read of all companies takes 3–4 hours; after that, each night takes minutes.

### How report figures are checked

Each figure keeps the exact PDF line it came from; on a stock's page, open "Exact lines read from the PDFs" to see them. Before any figure affects the score it must pass a cross-check:

1. **Last year's column = last year's report.** Every report prints the same period last year beside the current figures. When that column matches the figure in last year's own report, the row and columns were read correctly. The reader also uses this match to pick the right columns when a row holds several.
2. **Printed % change.** Where a report prints a % change column, it must match the current and prior figures.
3. **Quarter vs year-to-date by arithmetic.** Year-to-date = the previous report's year-to-date + this quarter, and the year-end figure must equal the annual report's EPS. Header wording isn't trusted.
4. **EPS = attributable profit ÷ shares in issue** (latest four reports).
5. **EPS growth must agree with profit growth.**
6. **Net assets per share:** the balance sheet's comparative column must equal the figure at the last financial year end.
7. **Plausibility:** every per-share figure is checked against the share price. Full-year EPS more than 13 months old is never used for P/E. US-dollar translation rows are skipped. Long-term EPS growth is only measured across years whose figures link up, so share splits don't create false trends.

Figures that fail are shown with ⚠ and excluded from the score. The stock page lists every check (passed / warning / failed), and the header shows how many figures were verified across all companies. The filter "Data issues" lists companies with failed checks.

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
| Fibonacci (6-month swing): up-move bouncing off / sitting at 38.2–61.8% support | +1 / +0.5 |
| Fibonacci: close below 78.6% in an up-move (move failing) | −1 |
| Fibonacci: down-move rejected at / sitting at 38.2–61.8% resistance | −1 / −0.5 |
| Fibonacci: close above 78.6% in a down-move (reversing) | +1 |

A score of 1.5 or more is BUY (3 or more is STRONG BUY). A score of −1.5 or less is SELL.

## Share splits

CSE's price history is **not adjusted for share splits**: on the day a 10-for-1 split takes effect the price simply drops 90%. `db.load_bars()` finds every such step (a one-day change of 1.7x or more, from Rs 2 or more, that stays) and divides the earlier prices by the ratio, so charts, signals, alerts and the forecast are on today's share basis. The ratio comes from, in order: the change in the share count in the company's own reports, a whole ratio in the second price source, a whole ratio in the price itself, or the raw price ratio (approximate). The Data checks page lists every split found and what its ratio rests on. `db.load_raw_bars()` gives the prices exactly as CSE published them.

## 10-year history and 20-year market patterns

CSE serves one year of prices, for shares and for the ASPI. Older years are downloaded once from **Yahoo Finance** (`python longhist.py fetch`, or **Actions → Long price history**), which has most CSE shares back to 2000–2008. It is a free, unofficial source with faults, so `longterm.py` cleans and checks it before use:

1. days without trades are removed (Yahoo repeats the last price on them);
2. each split Yahoo lists is tested against the prices around it and applied if it was not;
3. a price that is rebased in one step and stays there is treated as an unlisted split (flagged as unconfirmed); a jump that fits nothing ends the usable history there;
4. the join with CSE's own prices is tested: on the days both cover they must agree (most shares have about 60 such days), otherwise the older years are not used.

Each share's page has a **10-year history** card (chart, change over 1 to 20 years, each calendar year, largest fall) with the checks made on that share. Prices only; dividends are not included.

The **📈 Market** page shows the ASPI over 20 years and the patterns in it. Old ASPI values are not available daily, so the line is built in three layers: the official daily ASPI for the last year (`data/index/ASPI.csv`, extended every day), the official year-end closes (`data/index/aspi_annual.csv`) which the line passes through exactly, and between those an index built from the cleaned share prices (weighted by company size, chain-linked) that supplies the shape inside each year. The page reports how far the built index strays from the official ASPI before it is tied (a few points in most years; much more in 2021). Patterns measured on it: month of the year (with a test of whether a month's record could be luck), day of the week, falls of 20%+ and their recoveries, results of holding 1 to 10 years, and what followed strong and weak months.

## Trends

`trends.py` reads the trend of every share, industry group and the market, in the same simple way in four time frames (month = 20 trading days, quarter = 50, year = 200, three years = 750): **rising** when the price is above its average for that time frame and the average is itself going up, **falling** when it is below a falling average, otherwise sideways. Around that: how long each trend has lasted and how steady it is, the 52-week range, and the trend in trading volume, profits (year-on-year growth in the latest checked reports), revenue, earnings per share by year, dividends, and strength against all shares (a 1–99 score) and against sector peers.

The **📊 Trends** page shows the market's own trend, how many shares are rising or falling in each time frame, every industry group, and lists such as "just turned up", "rising on growing volume" and "earnings rising, price falling". Each share's page has a Trends card. The list can be filtered by "Rising in every time frame".

## Veterans' checklists

`veterans.py` applies the published rules of ten well-known investors and traders to every share: Graham, Buffett, Lynch, Greenblatt and Dreman for investing; O'Neil (CAN SLIM), Minervini (trend template), Weinstein (stage analysis), Darvas (boxes) and the Turtle traders for trading. The **🎓 Veterans** page shows, for any share, each rule as met, not met or *cannot be checked*, and lists the shares that pass each checklist best.

These are mechanical readings of published rules, **not the opinions of those people**. Rules that need figures this site does not read (debt, cash flow, margins, fund holdings) are shown as "cannot be checked" and never guessed. A share "fits" when the method's key rule is met and at least 80% of the checkable rules are (all of them for Minervini's template and the Turtle entry); it "cannot be judged" when the key rule, or more than 40% of the rules the site is able to check, has no figure to go on. Greenblatt's formula is approximated with earnings yield and return on equity because EBIT and capital employed are not available. Thresholds and wording are in `veterans.py`.

## Phones and tablets

On a phone the list shows change under the price and score under the signal so every column fits, and tapping a share opens it full-screen with a **‹ All shares** bar (the phone's Back works too). Tablets held upright keep the list above the share; wide screens show them side by side.

## Files

| File | Purpose |
|---|---|
| `.github/workflows/update.yml` | The daily scheduled job |
| `build_site.py` | Fetches data and builds `site/data/*.json` |
| `.github/workflows/fundamentals.yml` | Nightly job that reads company reports (4 parallel parts) |
| `.github/workflows/live.yml`, `live.py` | Live prices and alerts every 15 seconds during market hours |
| `forecast.py` | Pattern forecast, walk-forward accuracy test, live scoring |
| `pressure.py` | Daily buying/selling pressure reading and the test of what followed it |
| `longhist.py`, `.github/workflows/history.yml` | Downloads 10–20 years of prices (Yahoo Finance) and the official daily ASPI |
| `longterm.py` | Cleans and checks the long history; 10-year figures per share; market index and 20-year patterns |
| `site/market.html` | Market history page |
| `trends.py`, `site/trends.html` | Trends for every share, sector and the market |
| `veterans.py`, `site/veterans.html` | Checklists from the published methods of well-known investors and traders |
| `sectors.py` | Industry group for each counter |
| `site/accuracy.html` | Forecast accuracy page |
| `.github/workflows/publish.yml` | Rebuild and publish the site on demand |
| `reports.py` | Lists CSE report PDFs and reads figures out of them |
| `fundamentals.py` | Collects report figures, works out P/E, growth, etc. and fundamental points |
| `site/index.html` | The website |
| `cse_client.py` | Unofficial CSE API client (limited to 1 request/sec) |
| `collector.py` | Daily snapshot (skips holidays), history backfill, CSV import |
| `db.py` | Storage as CSV files in `data/` |
| `analysis.py` | Indicators, scoring, backtest |
| `app.py`, `templates/` | Optional local dashboard |
| `probe.py` | Shows raw CSE API responses for debugging |
